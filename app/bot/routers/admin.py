"""Admin Panel Router: RBAC Guards, Dashboard, User Search & Inspection, Freeze/Unfreeze, Adjustments, and Broadcasts."""

import html
import logging
from decimal import Decimal, InvalidOperation
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.filters.admin import IsAdminFilter
from app.bot.formatters.admin import (
    format_adjustment_confirmation,
    format_admin_audit_logs,
    format_admin_dashboard,
    format_admin_user_card,
    format_broadcast_preview,
    format_cleanup_confirmation,
    format_cleanup_result,
    format_cooldown_reset_summary,
    format_cheat_menu,
    format_grant_menu,
    format_grant_property_result,
    format_grant_stock_result,
    format_market_manipulation_result,
    format_market_regulator,
    format_stock_manipulation_result,
    format_user_ledger_statement,
)
from app.bot.keyboards.admin import (
    get_admin_adjustment_confirm_keyboard,
    get_admin_back_keyboard,
    get_admin_broadcast_confirm_keyboard,
    get_admin_cancel_keyboard,
    get_admin_cleanup_confirm_keyboard,
    get_admin_cheat_keyboard,
    get_admin_dashboard_keyboard,
    get_admin_grant_keyboard,
    get_admin_ledger_keyboard,
    get_admin_market_keyboard,
    get_admin_stock_detail_keyboard,
    get_admin_user_card_keyboard,
)
from app.bot.keyboards.reply import get_main_menu_keyboard
from app.bot.states.states import AdminStates, BroadcastStates
from app.database.models import Account, Stock, Transaction, User
from app.services.admin import AdminService
from app.services.banking import BankingService
from app.services.investments import InvestmentService
from app.services.property import _PROPERTY_INDEX
from app.services.transfers import InsufficientFundsError


logger = logging.getLogger(__name__)

admin_router = Router(name="admin_router")

DENIAL_TEXT = (
    "⛔ <b>Доступ запрещен!</b>\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "Вы не являетесь сотрудником службы безопасности или топ-менеджером Т-Банка.\n"
    "Попытка несанкционированного доступа зафиксирована службой безопасности Т-Банка."
)


# ==============================================================================
# Helper: Render Comprehensive User Inspection Profile
# ==============================================================================

async def render_user_inspection_card(
    target: Message | CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Render comprehensive user inspection profile with card, balances, and recent ledger."""
    # Ensure relationships loaded
    user_res = await session.execute(
        select(User)
        .where(User.id == user.id)
        .options(
            selectinload(User.accounts),
            selectinload(User.cards),
            selectinload(User.savings_account),
        )
    )
    loaded_user = user_res.scalars().first() or user

    overview = await BankingService.get_user_overview(session, loaded_user.id)
    account_ids = [acc.id for acc in loaded_user.accounts]
    recent_txs: list[Transaction] = []
    if account_ids:
        tx_res = await session.execute(
            select(Transaction)
            .where(
                (Transaction.sender_account_id.in_(account_ids))
                | (Transaction.recipient_account_id.in_(account_ids))
            )
            .order_by(Transaction.created_at.desc())
            .limit(5)
        )
        recent_txs = list(tx_res.scalars().all())

    text = format_admin_user_card(loaded_user, overview, recent_txs, account_ids)
    keyboard = get_admin_user_card_keyboard(loaded_user.id, is_frozen=loaded_user.is_blocked)

    if isinstance(target, Message):
        await target.answer(text=text, reply_markup=keyboard)
    elif isinstance(target, CallbackQuery) and target.message:
        try:
            await target.message.edit_text(text=text, reply_markup=keyboard)
        except Exception:
            await target.message.answer(text=text, reply_markup=keyboard)


# ==============================================================================
# 1. Dashboard & Core Navigation (Authorized Admins)
# ==============================================================================

@admin_router.message(Command("admin"), IsAdminFilter())
async def handle_admin_dashboard(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    """Handle /admin command for authorized administrators: show system dashboard."""
    await state.clear()
    metrics = await AdminService.get_metrics(session)
    text = format_admin_dashboard(metrics)
    await message.answer(text=text, reply_markup=get_admin_dashboard_keyboard())


@admin_router.callback_query(F.data.in_(["admin:dashboard", "admin"]), IsAdminFilter())
async def handle_admin_dashboard_callback(
    query: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    """Return to admin dashboard via inline button."""
    await state.clear()
    metrics = await AdminService.get_metrics(session)
    text = format_admin_dashboard(metrics)
    if query.message:
        await query.message.edit_text(text=text, reply_markup=get_admin_dashboard_keyboard())
    await query.answer()


@admin_router.callback_query(F.data == "admin:metrics:refresh", IsAdminFilter())
async def handle_metrics_refresh(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Refresh live metrics dashboard."""
    metrics = await AdminService.get_metrics(session)
    text = format_admin_dashboard(metrics)
    if query.message:
        await query.message.edit_text(text=text, reply_markup=get_admin_dashboard_keyboard())
    await query.answer("Метрики обновлены 🔄")


@admin_router.callback_query(F.data == "admin:exit", IsAdminFilter())
async def handle_admin_exit(
    query: CallbackQuery, state: FSMContext
) -> None:
    """Exit admin panel and return to user banking menu."""
    await state.clear()
    if query.message:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.answer(
            "🚪 <b>Вы вышли из панели администратора.</b>\nГлавное меню Т-Банка доступно ниже:",
            reply_markup=get_main_menu_keyboard(),
        )
    await query.answer("Вы вышли из админки")


# ==============================================================================
# 2. User Lookup & Inspection Flow
# ==============================================================================

@admin_router.callback_query(
    F.data.in_(["admin:user:search", "admin:users:search"]), IsAdminFilter()
)
async def handle_user_search_prompt(
    query: CallbackQuery, state: FSMContext
) -> None:
    """Prompt administrator to enter search query."""
    await state.set_state(AdminStates.search_query)
    text = (
        "👤 <b>Поиск и управление клиентами Т-Банка</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Введите один из идентификаторов пользователя:\n"
        "• <b>@username</b> (например, <code>@ivan_client</code>)\n"
        "• <b>Telegram ID</b> (например, <code>123456789</code>)\n"
        "• <b>16-значный номер карты</b> (например, <code>2200 7012 3456 7890</code>)\n"
        "• <b>Номер телефона</b> (например, <code>+79991234567</code>)\n\n"
        "Или нажмите <b>Отмена</b> для возврата в меню:"
    )
    if query.message:
        await query.message.edit_text(text=text, reply_markup=get_admin_cancel_keyboard())
    await query.answer()


@admin_router.message(AdminStates.search_query, IsAdminFilter())
async def process_user_search_query(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    """Resolve user by multi-identifier input and render inspection profile."""
    raw_query = (message.text or "").strip()
    if raw_query in ("❌ Отмена", "/cancel", "Отмена"):
        await state.clear()
        metrics = await AdminService.get_metrics(session)
        await message.answer(
            "❌ Поиск отменен. Возврат в панель управления.",
            reply_markup=get_admin_dashboard_keyboard(),
        )
        return

    found_user = await AdminService.search_user(session, raw_query)
    if not found_user:
        await message.answer(
            f"❌ <b>Пользователь не найден</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"По запросу <code>{html.escape(raw_query)}</code> клиент не найден в базе Т-Банка.\n\n"
            f"Проверьте правильность введенных данных и отправьте новый запрос, "
            f"или отправьте /cancel для выхода.",
            reply_markup=get_admin_cancel_keyboard(),
        )
        return

    await state.clear()
    await render_user_inspection_card(message, session, found_user)


@admin_router.callback_query(F.data.regexp(r"^admin:user:(?:view:)?(\d+)$"), IsAdminFilter())
async def handle_user_view(
    query: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    """Render user inspection profile by user ID."""
    await state.clear()
    match = query.data.split(":")[-1]
    target_user_id = int(match)
    user_res = await session.execute(
        select(User)
        .where(User.id == target_user_id)
        .options(
            selectinload(User.accounts),
            selectinload(User.cards),
            selectinload(User.savings_account),
        )
    )
    user = user_res.scalars().first()
    if not user:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    await render_user_inspection_card(query, session, user)
    await query.answer()


# ==============================================================================
# 3. Account Freeze & Unfreeze Actions
# ==============================================================================

@admin_router.callback_query(
    F.data.regexp(r"^admin:user:(freeze|unfreeze):(\d+)$"), IsAdminFilter()
)
async def handle_admin_toggle_freeze_user(
    query: CallbackQuery, session: AsyncSession, bot: Bot
) -> None:
    """Freeze or unfreeze all user accounts and cards with audit logging and client alert."""
    parts = query.data.split(":")
    action_type = parts[-2]
    target_user_id = int(parts[-1])
    is_freeze = (action_type == "freeze")

    user_res = await session.execute(
        select(User)
        .where(User.id == target_user_id)
        .options(selectinload(User.accounts), selectinload(User.cards))
    )
    user = user_res.scalars().first()
    if not user:
        await query.answer("Пользователь не найден.", show_alert=True)
        return

    admin_id = query.from_user.id
    reason = "Заморозка по решению администратора" if is_freeze else "Разморозка по решению администратора"
    await AdminService.freeze_user(
        session=session,
        admin_telegram_id=admin_id,
        target_user_id=user.id,
        freeze=is_freeze,
        reason=reason,
    )
    await session.commit()

    # User push notification
    if is_freeze:
        push_text = (
            "❄️ <b>Ваш аккаунт в Т-Банке временно заморожен</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Действие выполнено службой безопасности банка.\n"
            "Все расходные операции временно приостановлены.\n"
            "Для уточнения деталей обратитесь в поддержку."
        )
        alert_text = "❄️ Аккаунт пользователя заморожен."
    else:
        push_text = (
            "☀️ <b>Ваш аккаунт в Т-Банке успешно разморожен!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Все функции, переводы и операции снова доступны."
        )
        alert_text = "☀️ Аккаунт пользователя разморожен."

    try:
        await bot.send_message(chat_id=user.telegram_id, text=push_text, parse_mode="HTML")
    except Exception as exc:
        logger.warning("Failed to send freeze/unfreeze notification to %s: %s", user.telegram_id, exc)

    await render_user_inspection_card(query, session, user)
    await query.answer(alert_text, show_alert=True)


# ==============================================================================
# 4. User Ledger Statement & Audit Logs
# ==============================================================================

@admin_router.callback_query(F.data.regexp(r"^admin:user:ledger:(\d+)$"), IsAdminFilter())
async def handle_user_ledger(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Display detailed transaction ledger statement for inspected client."""
    target_user_id = int(query.data.split(":")[-1])
    user_res = await session.execute(
        select(User).where(User.id == target_user_id).options(selectinload(User.accounts))
    )
    user = user_res.scalars().first()
    if not user:
        await query.answer("Пользователь не найден.", show_alert=True)
        return

    account_ids = [acc.id for acc in user.accounts]
    txs: list[Transaction] = []
    if account_ids:
        res = await session.execute(
            select(Transaction)
            .where(
                (Transaction.sender_account_id.in_(account_ids))
                | (Transaction.recipient_account_id.in_(account_ids))
            )
            .order_by(Transaction.created_at.desc())
            .limit(15)
        )
        txs = list(res.scalars().all())

    text = format_user_ledger_statement(user, txs, account_ids)
    if query.message:
        await query.message.edit_text(text=text, reply_markup=get_admin_ledger_keyboard(user.id))
    await query.answer()


@admin_router.callback_query(
    F.data.in_(["admin:audit:log", "admin:logs:view"]), IsAdminFilter()
)
async def handle_audit_logs(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Display recent administrative audit logs."""
    logs = await AdminService.get_admin_logs(session, limit=20)
    text = format_admin_audit_logs(logs)
    if query.message:
        await query.message.edit_text(text=text, reply_markup=get_admin_back_keyboard())
    await query.answer()


# ==============================================================================
# 5. Balance Adjustment Flow (FSM: AdminStates.balance_delta & balance_reason)
# ==============================================================================

@admin_router.callback_query(
    F.data.regexp(r"^admin:(?:user:)?(credit|debit):(\d+)$"), IsAdminFilter()
)
async def handle_admin_balance_initiate(
    query: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    """Initiate balance credit or debit flow for inspected client."""
    parts = query.data.split(":")
    action = parts[-2] if "credit" in parts[-2] or "debit" in parts[-2] else parts[1]
    target_user_id = int(parts[-1])

    user = await session.get(User, target_user_id)
    if not user:
        await query.answer("Пользователь не найден.", show_alert=True)
        return

    account = await BankingService.get_user_main_account(session, target_user_id)
    if not account:
        await query.answer("Основной счет клиента не найден.", show_alert=True)
        return

    adjust_type = "CREDIT" if action == "credit" else "DEBIT"
    await state.set_state(AdminStates.balance_delta)
    await state.update_data(
        target_user_id=target_user_id,
        target_telegram_id=user.telegram_id,
        target_username=user.username or "",
        target_full_name=user.full_name or "Клиент",
        adjust_type=adjust_type,
        current_balance=str(account.balance),
    )

    op_header = "➕ Начисление средств клиенту" if adjust_type == "CREDIT" else "➖ Списание средств у клиента"
    text = (
        f"<b>{op_header}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Клиент: <b>{html.escape(user.full_name or 'Клиент', quote=False)}</b> "
        f"(@{html.escape(user.username, quote=False) if user.username else 'нет username'}, ID: <code>{user.telegram_id}</code>)\n"
        f"Текущий баланс: <b>{account.balance:,.2f} ₽</b>\n\n"
        f"Введите сумму операции (например, <code>1500.00</code>):"
    )
    if query.message:
        await query.message.edit_text(text=text, reply_markup=get_admin_cancel_keyboard(user.id))
    await query.answer()


@admin_router.message(AdminStates.balance_delta, IsAdminFilter())
async def process_balance_delta(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    """Parse and validate adjustment amount (positive decimal, debit balance check)."""
    raw_text = (message.text or "").strip()
    data = await state.get_data()
    target_user_id = data.get("target_user_id")

    if raw_text in ("❌ Отмена", "/cancel", "Отмена"):
        await state.clear()
        if target_user_id:
            user = await session.get(User, target_user_id)
            if user:
                await render_user_inspection_card(message, session, user)
                return
        await message.answer("❌ Операция отменена.", reply_markup=get_admin_dashboard_keyboard())
        return

    clean_text = raw_text.replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(clean_text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        await message.answer(
            "❌ <b>Некорректный формат суммы.</b>\nВведите положительное число (например, <code>500.00</code>):",
            reply_markup=get_admin_cancel_keyboard(target_user_id),
        )
        return

    if amount <= Decimal("0.00"):
        await message.answer(
            "❌ Сумма должна быть строго больше нуля (минимум 0.01 ₽). Повторите ввод:",
            reply_markup=get_admin_cancel_keyboard(target_user_id),
        )
        return

    if amount > Decimal("10000000.00"):
        await message.answer(
            "❌ Максимальная сумма единовременной корректировки — 10 000 000.00 ₽. Введите меньшую сумму:",
            reply_markup=get_admin_cancel_keyboard(target_user_id),
        )
        return

    adjust_type = data.get("adjust_type", "CREDIT")
    if adjust_type == "DEBIT":
        current_bal = Decimal(data.get("current_balance", "0.00"))
        if amount > current_bal:
            await message.answer(
                f"❌ <b>Недостаточно средств для списания!</b>\n"
                f"Текущий баланс клиента: <b>{current_bal:,.2f} ₽</b>\n"
                f"Запрошено к списанию: <b>{amount:,.2f} ₽</b>\n\n"
                f"Введите сумму не превышающую баланс или нажмите кнопку отмены:",
                reply_markup=get_admin_cancel_keyboard(target_user_id),
            )
            return

    delta = amount if adjust_type == "CREDIT" else -amount
    await state.update_data(amount=str(amount), delta=str(delta))
    await state.set_state(AdminStates.balance_reason)

    op_label = "➕ Начисление" if adjust_type == "CREDIT" else "➖ Списание"
    await message.answer(
        f"📝 <b>Укажите причину корректировки баланса</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Клиент: <b>{html.escape(data.get('target_full_name', 'Клиент'), quote=False)}</b>\n"
        f"Операция: <b>{op_label} {amount:,.2f} ₽</b>\n\n"
        f"⚠️ <i>Причина обязательна для аудита безопасности и будет указана в уведомлении клиенту.</i>\n"
        f"Введите текст причины (от 5 до 255 символов):",
        reply_markup=get_admin_cancel_keyboard(target_user_id),
    )


@admin_router.message(AdminStates.balance_reason, IsAdminFilter())
async def process_balance_reason(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    """Validate reason length (5-255 characters) and render confirmation card."""
    raw_text = (message.text or "").strip()
    data = await state.get_data()
    target_user_id = data.get("target_user_id")

    if raw_text in ("❌ Отмена", "/cancel", "Отмена"):
        await state.clear()
        if target_user_id:
            user = await session.get(User, target_user_id)
            if user:
                await render_user_inspection_card(message, session, user)
                return
        await message.answer("❌ Операция отменена.", reply_markup=get_admin_dashboard_keyboard())
        return

    if not message.text:
        await message.answer("❌ Пожалуйста, отправьте текстовое описание причины корректировки:")
        return

    reason = raw_text
    if len(reason) < 5:
        await message.answer(
            f"❌ <b>Причина слишком короткая</b> ({len(reason)} симв., минимум 5).\n"
            "Пожалуйста, опишите причину подробнее (например: <code>Компенсация задержки СБП</code>):",
            reply_markup=get_admin_cancel_keyboard(target_user_id),
        )
        return

    if len(reason) > 255:
        await message.answer(
            f"❌ <b>Причина слишком длинная</b> ({len(reason)} симв., максимум 255).\n"
            "Пожалуйста, сократите описание причины:",
            reply_markup=get_admin_cancel_keyboard(target_user_id),
        )
        return

    await state.update_data(reason=reason)

    user = await session.get(User, target_user_id)
    if not user:
        await state.clear()
        await message.answer("Пользователь не найден.")
        return

    delta = Decimal(data["delta"])
    current_bal = Decimal(data["current_balance"])
    confirm_text = format_adjustment_confirmation(
        user=user, delta=delta, reason=reason, before_balance=current_bal
    )
    await message.answer(
        confirm_text, reply_markup=get_admin_adjustment_confirm_keyboard(user.id)
    )


@admin_router.callback_query(F.data == "admin:adjust:confirm", IsAdminFilter())
async def handle_adjustment_confirm(
    query: CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot
) -> None:
    """Execute audited balance adjustment and dispatch real-time client alert."""
    data = await state.get_data()
    await state.clear()

    target_user_id = data.get("target_user_id")
    target_telegram_id = data.get("target_telegram_id")
    delta_str = data.get("delta")
    reason = data.get("reason")
    admin_id = query.from_user.id

    if not target_user_id or delta_str is None or not reason:
        await query.answer("Данные операции устарели.", show_alert=True)
        return

    delta = Decimal(delta_str)

    try:
        tx = await AdminService.adjust_balance(
            session=session,
            admin_telegram_id=admin_id,
            target_user_id=target_user_id,
            delta=delta,
            reason=reason,
        )
        await session.commit()
    except InsufficientFundsError as e:
        await session.rollback()
        await query.answer("Недостаточно средств!", show_alert=True)
        if query.message:
            await query.message.edit_text(
                f"❌ <b>Ошибка списания:</b> {html.escape(str(e))}",
                reply_markup=get_admin_cancel_keyboard(target_user_id),
            )
        return
    except Exception as e:
        await session.rollback()
        logger.exception("Failed executing balance adjustment")
        await query.answer("Ошибка выполнения!", show_alert=True)
        if query.message:
            await query.message.edit_text(
                f"❌ <b>Не удалось выполнить корректировку:</b> {html.escape(str(e))}",
                reply_markup=get_admin_cancel_keyboard(target_user_id),
            )
        return

    # Success display for administrator
    acc = await BankingService.get_user_main_account(session, target_user_id)
    new_bal = acc.balance if acc else Decimal("0.00")
    op_label = "Начисление (Credit)" if delta > 0 else "Списание (Debit)"
    escaped_reason = html.escape(reason, quote=False)
    client_name = html.escape(data.get("target_full_name", "Клиент"), quote=False)
    client_username = (
        f"@{html.escape(data.get('target_username'), quote=False)}"
        if data.get("target_username")
        else "—"
    )

    success_text = (
        f"✅ <b>Баланс клиента успешно скорректирован!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Клиент: <b>{client_name}</b> ({client_username})\n"
        f"Операция: <b>{op_label}</b>\n"
        f"Сумма: <b>{abs(delta):,.2f} ₽</b>\n"
        f"Новый баланс клиента: <b>{new_bal:,.2f} ₽</b>\n"
        f"Причина: <i>\"{escaped_reason}\"</i>\n\n"
        f"🧾 Чек операции: <code>#{tx.id[:8].upper()}</code>\n"
        f"Код авторизации: <code>{tx.auth_code}</code>\n"
        f"Фискальный признак: <code>{tx.fiscal_code}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    if query.message:
        await query.message.edit_text(
            success_text, reply_markup=get_admin_cancel_keyboard(target_user_id)
        )
    await query.answer("Операция успешно выполнена! ✅")

    # Real-time push notification to target user
    if target_telegram_id:
        if delta > 0:
            op_header = "➕ Пополнение баланса администратором"
            op_sign = "+"
            op_flavor = "Вам начислен приятный бонус от администрации Т-Банка!"
        else:
            op_header = "➖ Корректирующее списание со счета"
            op_sign = "-"
            op_flavor = "Администрация Т-Банка выполнила корректирующее списание средств."

        user_push_text = (
            f"🔔 <b>{op_header}</b>\n"
            f"<i>{op_flavor}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Сумма операции: <b>{op_sign}{abs(delta):,.2f} ₽</b>\n"
            f"Причина: <i>\"{escaped_reason}\"</i>\n\n"
            f"💰 <b>Доступный баланс:</b> <code>{new_bal:,.2f} ₽</code>\n"
            f"🧾 Чек: <code>#{tx.id[:8].upper()}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        try:
            await bot.send_message(
                chat_id=target_telegram_id, text=user_push_text, parse_mode="HTML"
            )
        except Exception as push_err:
            logger.warning(
                "Failed to dispatch push notification to user %s: %s",
                target_telegram_id,
                push_err,
            )


@admin_router.callback_query(
    F.data.in_(["admin:adjust:cancel", "action:admin_adjust_cancel"]), IsAdminFilter()
)
async def handle_adjustment_cancel(
    query: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    """Abort adjustment dialogue and return to client inspection card."""
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    await state.clear()
    await query.answer("Корректировка отменена.")

    if target_user_id:
        user = await session.get(User, target_user_id)
        if user:
            await render_user_inspection_card(query, session, user)
            return

    metrics = await AdminService.get_metrics(session)
    if query.message:
        await query.message.edit_text(
            format_admin_dashboard(metrics), reply_markup=get_admin_dashboard_keyboard()
        )


# ==============================================================================
# 6. Broadcast Announcement Wizard (FSM: BroadcastStates)
# ==============================================================================

@admin_router.message(Command("broadcast"), IsAdminFilter())
@admin_router.callback_query(
    F.data.in_(["admin:broadcast:start", "admin:broadcast"]), IsAdminFilter()
)
async def handle_start_broadcast(
    event: Message | CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    """Initiate broadcast wizard, display audience size, and prompt for announcement text."""
    recipients = await AdminService.get_all_recipient_ids(session)
    recipient_count = len(recipients)

    if recipient_count == 0:
        msg_text = "❌ В базе данных нет зарегистрированных пользователей для рассылки."
        if isinstance(event, CallbackQuery):
            await event.answer(msg_text, show_alert=True)
        else:
            await event.answer(msg_text)
        return

    await state.set_state(BroadcastStates.waiting_for_message)
    await state.update_data(recipient_count=recipient_count)

    prompt = (
        "📢 <b>Создание глобальной рассылки</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Получателей в базе: <b>{recipient_count:,}</b>\n\n"
        "Отправьте сообщение (текст, эмодзи, HTML-форматирование), которое получат все клиенты Т-Банка.\n\n"
        "<i>Поддерживаемые теги:</i> <code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>, "
        "<code>&lt;code&gt;</code>, <code>&lt;a href=\"...\"&gt;</code>.\n\n"
        "Для отмены нажмите кнопку ниже или отправьте <code>/cancel</code>."
    )
    keyboard = get_admin_cancel_keyboard()

    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.edit_text(prompt, reply_markup=keyboard)
    else:
        await event.answer(prompt, reply_markup=keyboard)


@admin_router.message(BroadcastStates.waiting_for_message, IsAdminFilter())
async def process_broadcast_message(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    """Validate broadcast text, render preview, and request confirmation."""
    text_content = (message.text or "").strip()
    if text_content in ["/cancel", "❌ Отмена", "Отмена"]:
        await state.clear()
        metrics = await AdminService.get_metrics(session)
        await message.answer(
            "❌ Создание рассылки отменено.", reply_markup=get_admin_dashboard_keyboard()
        )
        return

    raw_html = (
        message.html_text
        if hasattr(message, "html_text") and message.html_text
        else text_content
    )

    if not raw_html or not raw_html.strip():
        await message.answer(
            "❌ Текст рассылки не может быть пустым. Пожалуйста, отправьте сообщение для клиентов:",
            reply_markup=get_admin_cancel_keyboard(),
        )
        return

    if len(raw_html) > 4000:
        await message.answer(
            f"❌ Сообщение слишком длинное ({len(raw_html)} симв.). Максимальная длина — 4000 символов.\n"
            "Сократите текст и отправьте снова:",
            reply_markup=get_admin_cancel_keyboard(),
        )
        return

    data = await state.get_data()
    recipient_count = data.get("recipient_count", 0)

    await state.update_data(broadcast_text=raw_html)
    await state.set_state(BroadcastStates.confirm_broadcast)

    preview_box, confirm_text = format_broadcast_preview(raw_html, recipient_count)
    await message.answer(preview_box)
    await message.answer(confirm_text, reply_markup=get_admin_broadcast_confirm_keyboard())


@admin_router.callback_query(
    BroadcastStates.confirm_broadcast,
    F.data.in_(["admin:broadcast:cancel", "action:admin_broadcast_cancel"]),
    IsAdminFilter(),
)
async def handle_cancel_broadcast(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    """Abort broadcast and return to admin dashboard."""
    await state.clear()
    await callback.answer("Рассылка отменена.")
    if callback.message:
        await callback.message.edit_text(
            "❌ Рассылка отменена администратором.", reply_markup=get_admin_back_keyboard()
        )


@admin_router.callback_query(
    BroadcastStates.confirm_broadcast,
    F.data == "admin:broadcast:confirm",
    IsAdminFilter(),
)
async def handle_confirm_broadcast(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot
) -> None:
    """Execute rate-limited broadcast dispatch loop and report delivery summary."""
    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")

    if not broadcast_text:
        await callback.answer("Ошибка: текст рассылки утерян.", show_alert=True)
        await state.clear()
        return

    await state.clear()
    await callback.answer("🚀 Рассылка запущена!")
    if callback.message:
        await callback.message.edit_text(
            "⏳ <b>Выполняется рассылка сообщений...</b>\n"
            "Пожалуйста, подождите завершения процесса. Отчет будет отправлен по окончании.",
            reply_markup=None,
        )

    summary = await AdminService.execute_broadcast(
        bot=bot,
        session=session,
        admin_telegram_id=callback.from_user.id,
        message_text=broadcast_text,
    )
    await session.commit()

    summary_report = (
        "📢 <b>Рассылка успешно завершена!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Всего адресатов:       <b>{summary['total']:,}</b>\n"
        f"✅ Успешно доставлено:    <b>{summary['sent']:,}</b>\n"
        f"🚫 Заблокировали бота:   <b>{summary['blocked']:,}</b>\n"
        f"❌ Прочих ошибок:         <b>{max(0, summary['failed'] - summary['blocked']):,}</b>\n"
        f"⏱ Затраченное время:     <b>{summary['duration_seconds']:.1f}</b> сек.\n"
        f"⚡️ Средняя скорость:      <b>{summary['effective_speed']:.1f}</b> сообщ./сек.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    if callback.message:
        await callback.message.answer(
            summary_report, reply_markup=get_admin_dashboard_keyboard()
        )


# ==============================================================================
# 6. Admin Superpowers, Cheats, Grants, Market, and Cleanup (Authorized Admins)
# ==============================================================================

@admin_router.callback_query(F.data == "admin:cheat:menu", IsAdminFilter())
async def handle_cheat_menu_self(
    query: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    """Open cheat panel for administrator self-top-up."""
    await state.clear()
    admin_id = query.from_user.id
    user_res = await session.execute(
        select(User)
        .where(User.telegram_id == admin_id)
        .options(selectinload(User.accounts))
    )
    user = user_res.scalars().first()
    if not user:
        await query.answer("Профиль администратора не найден.", show_alert=True)
        return
    overview = await BankingService.get_user_overview(session, user.id)
    text = format_cheat_menu(user, overview.get("main_balance"))
    if query.message:
        await query.message.edit_text(text=text, reply_markup=get_admin_cheat_keyboard(user.id))
    await query.answer()


@admin_router.callback_query(F.data.regexp(r"^admin:cheat:user:(\d+)$"), IsAdminFilter())
async def handle_cheat_menu_user(
    query: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    """Open cheat panel for inspected target user."""
    await state.clear()
    match = query.data.split(":")[-1]
    target_uid = int(match)
    user_res = await session.execute(
        select(User)
        .where((User.id == target_uid) | (User.telegram_id == target_uid))
        .options(selectinload(User.accounts))
    )
    user = user_res.scalars().first()
    if not user:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    overview = await BankingService.get_user_overview(session, user.id)
    text = format_cheat_menu(user, overview.get("main_balance"))
    if query.message:
        await query.message.edit_text(text=text, reply_markup=get_admin_cheat_keyboard(user.id))
    await query.answer()


@admin_router.callback_query(F.data.regexp(r"^admin:cheat:rub:(\d+):(\d+)$"), IsAdminFilter())
async def handle_cheat_balance(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Atomically credit preset ruble balance to target user."""
    parts = query.data.split(":")
    target_uid = int(parts[3])
    amount = Decimal(parts[4])
    try:
        await AdminService.cheat_balance(
            session=session,
            admin_telegram_id=query.from_user.id,
            target_user_id=target_uid,
            amount=amount,
        )
        await session.commit()
    except Exception as e:
        await query.answer(f"Ошибка: {e}", show_alert=True)
        return

    user_res = await session.execute(
        select(User)
        .where((User.id == target_uid) | (User.telegram_id == target_uid))
        .options(selectinload(User.accounts))
    )
    user = user_res.scalars().first()
    overview = await BankingService.get_user_overview(session, user.id) if user else {}
    if user and query.message:
        text = format_cheat_menu(user, overview.get("main_balance"))
        await query.message.edit_text(text=text, reply_markup=get_admin_cheat_keyboard(user.id))
    await query.answer(f"✅ Успешно начислено +{amount:,.0f} ₽!", show_alert=True)


@admin_router.callback_query(F.data.regexp(r"^admin:cheat:cb:(\d+):(\d+)$"), IsAdminFilter())
async def handle_cheat_cashback(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Atomically credit preset cashback points to target user."""
    parts = query.data.split(":")
    target_uid = int(parts[3])
    amount = Decimal(parts[4])
    try:
        await AdminService.cheat_cashback(
            session=session,
            admin_telegram_id=query.from_user.id,
            target_user_id=target_uid,
            amount=amount,
        )
        await session.commit()
    except Exception as e:
        await query.answer(f"Ошибка: {e}", show_alert=True)
        return

    user_res = await session.execute(
        select(User)
        .where((User.id == target_uid) | (User.telegram_id == target_uid))
        .options(selectinload(User.accounts))
    )
    user = user_res.scalars().first()
    overview = await BankingService.get_user_overview(session, user.id) if user else {}
    if user and query.message:
        text = format_cheat_menu(user, overview.get("main_balance"))
        await query.message.edit_text(text=text, reply_markup=get_admin_cheat_keyboard(user.id))
    await query.answer(f"🎁 Успешно начислено +{amount:,.0f} баллов кэшбэка!", show_alert=True)


@admin_router.callback_query(F.data.regexp(r"^admin:cheat:reset_cd:(\d+)$"), IsAdminFilter())
async def handle_cheat_reset_cooldowns(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Fast-forward all timers and cooldowns for target user."""
    parts = query.data.split(":")
    target_uid = int(parts[3])
    try:
        summary = await AdminService.reset_cooldowns(
            session=session,
            admin_telegram_id=query.from_user.id,
            target_user_id=target_uid,
        )
        await session.commit()
    except Exception as e:
        await query.answer(f"Ошибка: {e}", show_alert=True)
        return

    user_res = await session.execute(
        select(User).where((User.id == target_uid) | (User.telegram_id == target_uid))
    )
    user = user_res.scalars().first()
    if user and query.message:
        text = format_cooldown_reset_summary(user, summary)
        await query.message.edit_text(text=text, reply_markup=get_admin_cheat_keyboard(user.id))
    await query.answer("⏳ Все кулдауны и таймеры успешно сброшены!", show_alert=True)


@admin_router.callback_query(F.data.regexp(r"^admin:grant:user:(\d+)$"), IsAdminFilter())
async def handle_grant_menu(
    query: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    """Open asset and stock grant catalog."""
    await state.clear()
    target_uid = int(query.data.split(":")[-1])
    user_res = await session.execute(
        select(User).where((User.id == target_uid) | (User.telegram_id == target_uid))
    )
    user = user_res.scalars().first()
    if not user:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    text = format_grant_menu(user)
    if query.message:
        await query.message.edit_text(text=text, reply_markup=get_admin_grant_keyboard(user.id))
    await query.answer()


@admin_router.callback_query(F.data.regexp(r"^admin:grant:prop:(\d+):([a-z0-9_]+)$"), IsAdminFilter())
async def handle_grant_property(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Grant elite car, real estate, or luxury item to target user."""
    parts = query.data.split(":")
    target_uid = int(parts[3])
    item_id = parts[4]
    try:
        prop = await AdminService.grant_property(
            session=session,
            admin_telegram_id=query.from_user.id,
            target_user_id=target_uid,
            item_id=item_id,
        )
        await session.commit()
    except Exception as e:
        await query.answer(f"Ошибка: {e}", show_alert=True)
        return

    user_res = await session.execute(
        select(User).where((User.id == target_uid) | (User.telegram_id == target_uid))
    )
    user = user_res.scalars().first()
    item = _PROPERTY_INDEX.get(item_id)
    item_name = item.name if item else item_id
    cat = item.category if item else prop.category
    icon = item.icon if item else "🎁"

    if user and query.message:
        text = format_grant_property_result(user, item_name, cat, icon)
        await query.message.edit_text(text=text, reply_markup=get_admin_grant_keyboard(user.id))
    await query.answer(f"🎁 Выдано: {item_name}!", show_alert=True)


async def _resolve_stock_ticker(session: AsyncSession, ticker: str) -> str:
    """Resolve stock ticker with fallback aliases for human-friendly names."""
    clean = ticker.strip().upper()
    await InvestmentService.init_stocks(session)
    res = await session.execute(
        select(Stock).where(func.upper(Stock.ticker) == clean)
    )
    if res.scalars().first():
        return clean
    aliases = {
        "SHAURMA": "SHAW",
        "SPICHKI": "MTCH",
        "BOBR": "BBRS",
        "BOBRY": "BBRS",
        "DIVAN": "DVAN",
        "GRECHKA": "GRECH",
        "SVET": "MYK",
    }
    return aliases.get(clean, clean)


@admin_router.callback_query(F.data.regexp(r"^admin:grant:stock:(\d+):([A-Za-z0-9_]+)$"), IsAdminFilter())
async def handle_grant_stocks(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Grant +1000 shares of target stock ticker to user's portfolio."""
    parts = query.data.split(":")
    target_uid = int(parts[3])
    raw_ticker = parts[4].upper()
    ticker = await _resolve_stock_ticker(session, raw_ticker)
    try:
        portfolio = await AdminService.grant_stocks(
            session=session,
            admin_telegram_id=query.from_user.id,
            target_user_id=target_uid,
            ticker=ticker,
            shares=1000,
        )
        await session.commit()
    except Exception as e:
        await query.answer(f"Ошибка: {e}", show_alert=True)
        return

    user_res = await session.execute(
        select(User).where((User.id == target_uid) | (User.telegram_id == target_uid))
    )
    user = user_res.scalars().first()
    if user and query.message:
        text = format_grant_stock_result(user, ticker, 1000, portfolio.shares)
        await query.message.edit_text(text=text, reply_markup=get_admin_grant_keyboard(user.id))
    await query.answer(f"📈 Начислено +1 000 акций {ticker}!", show_alert=True)


@admin_router.callback_query(F.data == "admin:market:menu", IsAdminFilter())
async def handle_market_menu(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Open stock market regulator dashboard."""
    await InvestmentService.init_stocks(session)
    res = await session.execute(select(Stock))
    stocks = list(res.scalars().all())
    text = format_market_regulator(stocks)
    if query.message:
        await query.message.edit_text(text=text, reply_markup=get_admin_market_keyboard(stocks))
    await query.answer()


@admin_router.callback_query(F.data.in_(["admin:market:pump", "admin:market:dump", "admin:market:crash"]), IsAdminFilter())
async def handle_market_global_action(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Execute market-wide pump, dump, or crash."""
    action = query.data.split(":")[-1].upper()
    try:
        stocks = await AdminService.manipulate_market(
            session=session,
            admin_telegram_id=query.from_user.id,
            action=action,
        )
        await session.commit()
    except Exception as e:
        await query.answer(f"Ошибка: {e}", show_alert=True)
        return

    text = format_market_manipulation_result(action, stocks)
    if query.message:
        await query.message.edit_text(text=text, reply_markup=get_admin_market_keyboard(stocks))
    action_alerts = {
        "PUMP": "🚀 Памп рынка (+50%) выполнен!",
        "DUMP": "📉 Дамп рынка (-50%) выполнен!",
        "CRASH": "💥 Крах биржи (-80%) выполнен!",
    }
    await query.answer(action_alerts.get(action, "Операция выполнена!"), show_alert=True)


@admin_router.callback_query(F.data.regexp(r"^admin:market:stock:([A-Za-z0-9_]+)$"), IsAdminFilter())
async def handle_market_stock_inspect(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Inspect and manage a specific stock ticker."""
    raw_ticker = query.data.split(":")[-1].upper()
    ticker = await _resolve_stock_ticker(session, raw_ticker)
    res = await session.execute(
        select(Stock).where(func.upper(Stock.ticker) == ticker)
    )
    stock = res.scalars().first()
    if not stock:
        await query.answer(f"Акция {raw_ticker} не найдена.", show_alert=True)
        return
    text = format_stock_manipulation_result(stock, "Готов к изменению")
    if query.message:
        await query.message.edit_text(
            text=text,
            reply_markup=get_admin_stock_detail_keyboard(stock.ticker, stock.current_price),
        )
    await query.answer()


@admin_router.callback_query(F.data.regexp(r"^admin:market:stock:([A-Za-z0-9_]+):(pump|dump)$"), IsAdminFilter())
async def handle_market_stock_action(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Execute pump (+25%) or dump (-25%) on a single stock."""
    parts = query.data.split(":")
    raw_ticker = parts[3].upper()
    action = parts[4].upper()
    ticker = await _resolve_stock_ticker(session, raw_ticker)
    try:
        stock = await AdminService.manipulate_stock(
            session=session,
            admin_telegram_id=query.from_user.id,
            ticker=ticker,
            action_or_price=action,
        )
        await session.commit()
    except Exception as e:
        await query.answer(f"Ошибка: {e}", show_alert=True)
        return

    text = format_stock_manipulation_result(stock, f"Действие: {action} ({stock.current_price:.2f} ₽)")
    if query.message:
        await query.message.edit_text(
            text=text,
            reply_markup=get_admin_stock_detail_keyboard(stock.ticker, stock.current_price),
        )
    await query.answer(f"✅ {ticker}: {action} завершен -> {stock.current_price:.2f} ₽!", show_alert=True)


@admin_router.callback_query(F.data.regexp(r"^admin:market:stock:([A-Za-z0-9_]+):p:(\d+(?:\.\d+)?)$"), IsAdminFilter())
async def handle_market_stock_set_price(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Set exact target price for a single stock."""
    parts = query.data.split(":")
    raw_ticker = parts[3].upper()
    target_price = Decimal(parts[5])
    ticker = await _resolve_stock_ticker(session, raw_ticker)
    try:
        stock = await AdminService.set_stock_price(
            session=session,
            admin_telegram_id=query.from_user.id,
            ticker=ticker,
            target_price=target_price,
        )
        await session.commit()
    except Exception as e:
        await query.answer(f"Ошибка: {e}", show_alert=True)
        return

    text = format_stock_manipulation_result(stock, f"Установлена цена: {stock.current_price:.2f} ₽")
    if query.message:
        await query.message.edit_text(
            text=text,
            reply_markup=get_admin_stock_detail_keyboard(stock.ticker, stock.current_price),
        )
    await query.answer(f"🎯 {ticker}: цена установлена в {stock.current_price:.2f} ₽!", show_alert=True)



@admin_router.callback_query(F.data == "admin:cleanup:test_users", IsAdminFilter())
async def handle_cleanup_prompt(
    query: CallbackQuery
) -> None:
    """Show database cleanup confirmation dialogue."""
    text = format_cleanup_confirmation()
    if query.message:
        await query.message.edit_text(text=text, reply_markup=get_admin_cleanup_confirm_keyboard())
    await query.answer()


@admin_router.callback_query(F.data == "admin:cleanup:confirm", IsAdminFilter())
async def handle_cleanup_execute(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Execute purge of mock test user accounts and cascade child records."""
    try:
        summary = await AdminService.cleanup_test_users(
            session=session,
            admin_telegram_id=query.from_user.id,
        )
        await session.commit()
    except Exception as e:
        await query.answer(f"Ошибка при очистке: {e}", show_alert=True)
        return

    text = format_cleanup_result(summary)
    if query.message:
        await query.message.edit_text(text=text, reply_markup=get_admin_dashboard_keyboard())
    await query.answer(f"🧹 Удалено {summary.get('users', 0)} тестовых профилей!", show_alert=True)


# ==============================================================================
# 7. Unauthorized RBAC Fallbacks (MUST BE AT THE BOTTOM)
# ==============================================================================


@admin_router.message(Command("admin"))
async def handle_unauthorized_admin_command(message: Message) -> None:
    """Catch-all for unauthorized /admin command attempts."""
    user_id = message.from_user.id if message.from_user else None
    username = message.from_user.username if message.from_user else None
    logger.warning("Unauthorized /admin attempt by user_id=%s, username=@%s", user_id, username)
    await message.answer(DENIAL_TEXT)


@admin_router.message(Command("broadcast"))
async def handle_unauthorized_broadcast_command(message: Message) -> None:
    """Catch-all for unauthorized /broadcast command attempts."""
    user_id = message.from_user.id if message.from_user else None
    username = message.from_user.username if message.from_user else None
    logger.warning("Unauthorized /broadcast attempt by user_id=%s, username=@%s", user_id, username)
    await message.answer(DENIAL_TEXT)


@admin_router.callback_query(F.data.startswith("admin:"))
async def handle_unauthorized_admin_callback(query: CallbackQuery) -> None:
    """Catch-all for unauthorized admin callback query attempts."""
    user_id = query.from_user.id if query.from_user else None
    logger.warning("Unauthorized admin callback %s by user_id=%s", query.data, user_id)
    await query.answer("⛔ Доступ запрещен. У вас нет прав администратора.", show_alert=True)
