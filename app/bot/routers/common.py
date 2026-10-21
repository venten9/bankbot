"""Common Navigation Router: /start, /help, /cancel, and Multi-Account Overview."""

import html
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.formatters.analytics import format_expense_analytics
from app.bot.keyboards.reply import get_main_menu_keyboard
from app.database.models import User
from app.services.banking import BankingService

common_router = Router(name="common_router")


@common_router.message(CommandStart())
async def handle_start(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Handle /start: onboard user, display account balances, and attach persistent keyboard."""
    await state.clear()
    overview = await BankingService.get_user_overview(session, user.id)

    full_name = html.escape(user.full_name or "Клиент", quote=False)
    main_bal = overview["main_balance"]
    savings_bal = overview["savings_balance"]
    cb_bal = overview["cashback_balance"]
    port_val = overview["portfolio_value"]
    net_worth = overview["total_net_worth"]

    text = (
        f"🟡 <b>Добро пожаловать в Т-Банк Онлайн!</b>\n\n"
        f"Привет, <b>{full_name}</b>! Ваша дебетовая карта <b>Т-Банк Black</b> активна и готова к операциям.\n\n"
        f"💰 <b>Счета и балансы:</b>\n"
        f"• Карта Black: <b>{main_bal:.2f} ₽</b>\n"
        f"• Накопительный сейф (18%): <b>{savings_bal:.2f} ₽</b>\n"
        f"• Бонусный кэшбэк: <b>{cb_bal:.2f} ₽</b>\n"
        f"• Портфель Т-Инвестиций: <b>{port_val:.2f} ₽</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>Общий капитал:</b> <b>{net_worth:.2f} ₽</b>\n\n"
        f"Выберите нужный раздел в меню ниже:"
    )

    await message.answer(
        text=text,
        reply_markup=get_main_menu_keyboard(),
    )


@common_router.message(Command("help"))
async def handle_help(message: Message) -> None:
    """Handle /help: display banking capabilities and usage guide."""
    text = (
        "🟡 <b>Справка Т-Банк Онлайн</b>\n\n"
        "💳 <b>Моя карта</b> — просмотр реквизитов карты, баланса и кнопка заморозки.\n"
        "💸 <b>Переводы</b> — мгновенные переводы по @username, телефону (+7...), номеру карты или Telegram ID.\n"
        "💰 <b>Сейф 18%</b> — накопительный счет с ежедневным начислением 18% годовых и выплатой процентов.\n"
        "📈 <b>Инвестиции</b> — пародийная биржа с акциями (Шаурма, Завод спичек), динамическими ценами и портфелем.\n"
        "🎁 <b>Кэшбэк</b> — выбор 4 категорий месяца (до 15%) и вывод накопленных бонусов на карту.\n"
        "📊 <b>Аналитика</b> — визуальная шкала расходов по категориям за текущий месяц."
    )
    await message.answer(text=text, reply_markup=get_main_menu_keyboard())


@common_router.message(Command("cancel"))
@common_router.message(F.text == "❌ Отмена")
async def handle_cancel(message: Message, state: FSMContext) -> None:
    """Cancel any active FSM dialogue state and return to main menu."""
    await state.clear()
    await message.answer(
        "❌ Действие отменено. Вы вернулись в главное меню.",
        reply_markup=get_main_menu_keyboard(),
    )


@common_router.message(Command("analytics"))
@common_router.message(F.text == "📊 Аналитика")
async def handle_analytics(
    message: Message, session: AsyncSession, user: User
) -> None:
    """Display monthly spending breakdown with visual progress bars."""
    report = await format_expense_analytics(session, user.id)
    await message.answer(text=report, reply_markup=get_main_menu_keyboard())
