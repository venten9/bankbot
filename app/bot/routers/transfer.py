import html
import logging
from decimal import Decimal
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.formatters.receipts import format_receipt_message
from app.bot.keyboards.inline import get_transfer_confirm_keyboard
from app.bot.keyboards.reply import get_cancel_reply_keyboard, get_main_menu_keyboard
from app.bot.states.states import TransferStates
from app.database.models import Account, User
from app.services.banking import BankingService
from app.services.transfers import (
    AccountFrozenError,
    InsufficientFundsError,
    TransferService,
)

logger = logging.getLogger(__name__)
transfer_router = Router(name="transfer_router")


@transfer_router.message(Command("transfer"))
@transfer_router.message(F.text == "💸 Переводы")
async def handle_start_transfer(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Initiate multi-step transfer wizard."""
    main_acc = await BankingService.get_user_main_account(session, user.id)
    if not main_acc:
        await message.answer("Основной счет не найден.")
        return

    if main_acc.is_frozen:
        await message.answer(
            "❄️ <b>Ваша карта заморожена.</b>\nРазморозьте её в меню карты перед совершением перевода.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    if main_acc.balance < Decimal("1.00"):
        await message.answer(
            f"❌ Недостаточно средств для перевода. Доступно: <b>{main_acc.balance:.2f} ₽</b> (минимум 1.00 ₽).",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    await state.set_state(TransferStates.waiting_for_recipient)
    prompt = (
        "💸 <b>Перевод клиенту Т-Банка</b>\n\n"
        "Введите получателя одним из способов:\n"
        "• Telegram @username (например, <code>@durov</code>)\n"
        "• Номер телефона (например, <code>+79991234567</code>)\n"
        "• Номер карты (16 цифр: <code>2200 70••...</code>)\n"
        "• Telegram ID получателя\n\n"
        "Для отмены отправьте /cancel или нажмите ❌ Отмена."
    )
    await message.answer(text=prompt, reply_markup=get_cancel_reply_keyboard())


@transfer_router.message(TransferStates.waiting_for_recipient)
async def process_transfer_recipient(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Resolve recipient by input and prompt for transfer amount."""
    query_text = (message.text or "").strip()
    if query_text in ["❌ Отмена", "/cancel"]:
        await state.clear()
        await message.answer(
            "❌ Перевод отменен.", reply_markup=get_main_menu_keyboard()
        )
        return

    recipient_acc = await TransferService.resolve_recipient(session, query_text)
    if not recipient_acc:
        await message.answer(
            "❌ Получатель не найден в Т-Банке.\nПроверьте правильность @username, телефона или номера карты и введите снова:"
        )
        return

    main_acc = await BankingService.get_user_main_account(session, user.id)
    if recipient_acc.id == main_acc.id or recipient_acc.user_id == user.id:
        await message.answer(
            "❌ Нельзя перевести деньги самому себе на ту же карту.\nУкажите другого получателя:"
        )
        return

    rec_user = await session.get(User, recipient_acc.user_id)
    rec_name = rec_user.full_name if rec_user else "Клиент Т-Банка"
    rec_username = f"@{rec_user.username}" if rec_user and rec_user.username else "нет username"

    await state.update_data(
        recipient_account_id=recipient_acc.id,
        recipient_user_id=rec_user.id if rec_user else None,
        recipient_telegram_id=rec_user.telegram_id if rec_user else None,
        recipient_full_name=rec_name,
        recipient_username=rec_username,
    )
    await state.set_state(TransferStates.waiting_for_amount)

    avail_bal = main_acc.balance if main_acc else Decimal("0.00")
    safe_rec_name = html.escape(rec_name, quote=False)
    safe_rec_username = html.escape(rec_username, quote=False)
    await message.answer(
        f"✅ Получатель: <b>{safe_rec_name}</b> ({safe_rec_username})\n\n"
        f"💰 Доступно на вашей карте: <b>{avail_bal:.2f} ₽</b>\n"
        "Введите сумму перевода (от 1.00 ₽):",
        reply_markup=get_cancel_reply_keyboard(),
    )


@transfer_router.message(TransferStates.waiting_for_amount)
async def process_transfer_amount(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Validate transfer amount and display confirmation inline keyboard."""
    text = (message.text or "").strip()
    if text in ["❌ Отмена", "/cancel"]:
        await state.clear()
        await message.answer(
            "❌ Перевод отменен.", reply_markup=get_main_menu_keyboard()
        )
        return

    try:
        clean_amt = text.replace(",", ".").replace("₽", "").replace(" ", "").strip()
        amount = Decimal(clean_amt).quantize(Decimal("0.01"))
        if amount < Decimal("1.00"):
            await message.answer("❌ Минимальная сумма перевода — 1.00 ₽. Введите сумму:")
            return
        if amount > Decimal("1000000.00"):
            await message.answer(
                "❌ Максимальная сумма перевода — 1 000 000.00 ₽. Введите сумму:"
            )
            return
    except Exception:
        await message.answer("❌ Некорректный формат суммы. Введите число (например, 500 или 1250.50):")
        return

    main_acc = await BankingService.get_user_main_account(session, user.id)
    if not main_acc or amount > main_acc.balance:
        cur_bal = main_acc.balance if main_acc else Decimal("0.00")
        await message.answer(
            f"❌ Недостаточно средств. Доступно: <b>{cur_bal:.2f} ₽</b>, запрошено: <b>{amount:.2f} ₽</b>.\n"
            "Введите другую сумму:"
        )
        return

    await state.update_data(amount=str(amount))
    await state.set_state(TransferStates.confirm)

    data = await state.get_data()
    rec_display_name = html.escape(str(data.get("recipient_full_name") or "Клиент Т-Банка"), quote=False)
    rec_display_username = html.escape(str(data.get("recipient_username") or "нет username"), quote=False)
    confirm_text = (
        "💸 <b>Подтверждение перевода</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Получатель: <b>{rec_display_name}</b> ({rec_display_username})\n"
        "💳 Счет списания: <b>Т-Банк Black</b>\n"
        f"💵 Сумма: <b>{amount:.2f} ₽</b>\n"
        "⚡ Комиссия: <b>0.00 ₽ (СБП 0%)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Подтвердите перевод:"
    )
    await message.answer(
        text=confirm_text,
        reply_markup=get_transfer_confirm_keyboard(),
    )


@transfer_router.callback_query(TransferStates.confirm, F.data == "trf:confirm")
async def handle_confirm_transfer(
    query: CallbackQuery, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Execute transfer, generate digital receipt, and dispatch push alert to recipient."""
    data = await state.get_data()
    recipient_account_id = data.get("recipient_account_id")
    recipient_telegram_id = data.get("recipient_telegram_id")
    recipient_user_id = data.get("recipient_user_id")
    amount_str = data.get("amount")

    if not recipient_account_id or not amount_str:
        await query.answer("Сессия перевода устарела.", show_alert=True)
        await state.clear()
        return

    amount = Decimal(amount_str)

    try:
        tx = await TransferService.execute_transfer(
            session=session,
            sender_user_id=user.id,
            recipient_account_id=recipient_account_id,
            amount=amount,
            category="Переводы",
            description="Перевод через Т-Банк",
        )
        await session.commit()
    except InsufficientFundsError as e:
        await query.answer("Недостаточно средств", show_alert=True)
        await query.message.answer(f"❌ {e}", reply_markup=get_main_menu_keyboard())
        await state.clear()
        return
    except AccountFrozenError as e:
        await query.answer("Карта заморожена", show_alert=True)
        await query.message.answer(f"❌ {e}", reply_markup=get_main_menu_keyboard())
        await state.clear()
        return
    except Exception as e:
        await query.answer("Ошибка перевода", show_alert=True)
        await query.message.answer(f"❌ Ошибка операции: {e}", reply_markup=get_main_menu_keyboard())
        await state.clear()
        return

    # Receipt for sender
    try:
        rec_user = await session.get(User, recipient_user_id) if recipient_user_id else None
        sender_card = user.cards[0] if user.cards else None
        receipt_text = format_receipt_message(
            transaction=tx,
            sender_user=user,
            recipient_user=rec_user,
            sender_card=sender_card,
        )
        try:
            await query.message.edit_text(receipt_text)
        except Exception as edit_err:
            logger.debug("Could not edit message with receipt, sending new answer: %s", edit_err)
            await query.message.answer(receipt_text)
        await query.message.answer("🎉 Перевод успешно выполнен!", reply_markup=get_main_menu_keyboard())
    except Exception as ui_err:
        logger.warning("Error rendering sender receipt: %s", ui_err)
        await query.message.answer("🎉 Перевод успешно выполнен!", reply_markup=get_main_menu_keyboard())

    # Real-time push notification to recipient with HTML entity escaping
    if recipient_telegram_id and query.bot:
        try:
            rec_acc = await session.get(Account, recipient_account_id)
            rec_bal = rec_acc.balance if rec_acc else Decimal("0.00")
            sender_name = html.escape(user.full_name or "Клиент Т-Банка", quote=False)
            sender_username_escaped = html.escape(user.username, quote=False) if user.username else "без юзернейма"
            push_text = (
                f"💸 <b>Входящий перевод через СБП!</b>\n"
                f"Сумма: <b>+{amount:.2f} ₽</b>\n"
                f"Отправитель: <b>{sender_name}</b> (@{sender_username_escaped})\n"
                f"Баланс счета: <b>{rec_bal:.2f} ₽</b>\n\n"
                f"🧾 Чек операции: <code>#{tx.id[:8].upper()}</code>"
            )
            await query.bot.send_message(chat_id=recipient_telegram_id, text=push_text)
        except Exception as push_err:
            logger.warning("Failed to dispatch recipient push notification to %s: %s", recipient_telegram_id, push_err)

    await query.answer("Перевод выполнен!")
    await state.clear()


@transfer_router.callback_query(TransferStates.confirm, F.data == "trf:cancel")
async def handle_cancel_transfer_callback(
    query: CallbackQuery, state: FSMContext
) -> None:
    """Cancel transfer on confirmation inline button tap."""
    await state.clear()
    await query.message.edit_text("❌ Перевод отменен.")
    await query.message.answer("Главное меню:", reply_markup=get_main_menu_keyboard())
    await query.answer("Перевод отменен")
