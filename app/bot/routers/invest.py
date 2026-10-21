"""Parody Stock Market Router (Т-Инвестиции): Catalog, Quotes, Buy/Sell, and Portfolio."""

from decimal import Decimal
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import (
    get_invest_catalog_keyboard,
    get_stock_detail_keyboard,
    get_trade_confirm_keyboard,
)
from app.bot.keyboards.reply import get_cancel_reply_keyboard, get_main_menu_keyboard
from app.bot.states.states import InvestStates
from app.config import settings
from app.database.models import User
from app.services.banking import BankingService
from app.services.investments import (
    InsufficientSharesError,
    InvestmentService,
)
from app.services.transfers import InsufficientFundsError

invest_router = Router(name="invest_router")


@invest_router.message(Command("invest"))
@invest_router.message(F.text == "📈 Инвестиции")
async def handle_invest_menu(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Display T-Investments stock market catalog and quotes."""
    await state.clear()
    stocks = await InvestmentService.list_stocks(session)
    text = (
        "📈 <b>Т-Инвестиции: Пародийная биржа акций</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Котировки моделируются в реальном времени (Geometric Brownian Motion + Mean Reversion).\n"
        f"Комиссия брокера: <b>{settings.BROKER_FEE_PERCENT * 100:.1f}%</b> за сделку.\n\n"
        "Выберите интересующую акцию для просмотра деталей и совершения сделки:"
    )
    await message.answer(text=text, reply_markup=get_invest_catalog_keyboard(stocks))


@invest_router.callback_query(F.data == "invest:catalog")
async def handle_invest_catalog_callback(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Display catalog of stocks."""
    stocks = await InvestmentService.list_stocks(session)
    text = (
        "📈 <b>Т-Инвестиции: Каталог акций</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите акцию для покупки или продажи:"
    )
    await query.message.edit_text(text=text, reply_markup=get_invest_catalog_keyboard(stocks))
    await query.answer()


@invest_router.callback_query(F.data == "invest:refresh")
async def handle_invest_refresh(
    query: CallbackQuery, session: AsyncSession
) -> None:
    """Fluctuate stock market prices via dynamic GBM engine and refresh catalog view."""
    stocks = await InvestmentService.tick_stock_prices(session, delta_t=1.0)
    await session.commit()
    text = (
        "📈 <b>Т-Инвестиции: Каталог акций</b> (Котировки обновлены ⚡)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите акцию для покупки или продажи:"
    )
    try:
        await query.message.edit_text(
            text=text, reply_markup=get_invest_catalog_keyboard(stocks)
        )
    except Exception:
        pass
    await query.answer("Котировки обновлены!")


@invest_router.callback_query(F.data == "invest:portfolio")
async def handle_view_portfolio(
    query: CallbackQuery, session: AsyncSession, user: User
) -> None:
    """Display user portfolio valuation and profit/loss summary."""
    portfolio = await InvestmentService.get_portfolio(session, user.id)
    holdings = portfolio["holdings"]
    total_val = portfolio["total_value"]
    total_inv = portfolio["total_invested"]
    total_pnl = portfolio["total_pnl"]
    pnl_pct = portfolio["total_pnl_percent"]

    pnl_sign = "+" if total_pnl >= Decimal("0.00") else ""
    pnl_emoji = "🟢" if total_pnl >= Decimal("0.00") else "🔴"

    lines = [
        "💼 <b>Мой инвестиционный портфель</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Текущая стоимость: <b>{total_val:.2f} ₽</b>",
        f"Вложено:           <b>{total_inv:.2f} ₽</b>",
        f"Доходность:        {pnl_emoji} <b>{pnl_sign}{total_pnl:.2f} ₽ ({pnl_sign}{pnl_pct:.2f}%)</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if not holdings:
        lines.append("<i>У вас пока нет купленных акций. Самое время прикупить пару лотов Шаурмы!</i>")
    else:
        for idx, h in enumerate(holdings, start=1):
            h_sign = "+" if h["unrealized_pnl"] >= Decimal("0.00") else ""
            h_emoji = "🟢" if h["unrealized_pnl"] >= Decimal("0.00") else "🔴"
            lines.append(
                f"{idx}. <b>{h['ticker']}</b> — {h['shares']} шт.\n"
                f"   Цена: {h['current_price']:.2f} ₽ (покупка: {h['average_buy_price']:.2f} ₽)\n"
                f"   Стоимость: {h['current_value']:.2f} ₽ | {h_emoji} {h_sign}{h['unrealized_pnl']:.2f} ₽ ({h_sign}{h['yield_percent']:.1f}%)"
            )

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« К каталогу акций", callback_data="invest:catalog")]
    ])
    await query.message.edit_text(text="\n".join(lines), reply_markup=kb)
    await query.answer()


@invest_router.callback_query(F.data.startswith("stock:view:"))
async def handle_view_stock(
    query: CallbackQuery, session: AsyncSession, user: User
) -> None:
    """Display comprehensive details for a specific stock."""
    ticker = query.data.split(":")[-1]
    stock = await InvestmentService.get_stock(session, ticker)
    if not stock:
        await query.answer("Акция не найдена.", show_alert=True)
        return

    portfolio = await InvestmentService.get_portfolio(session, user.id)
    owned_entry = next((h for h in portfolio["holdings"] if h["ticker"] == stock.ticker), None)
    owned_shares = owned_entry["shares"] if owned_entry else 0

    text = (
        f"📊 <b>{stock.name} ({stock.ticker})</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Сектор:            <b>{stock.sector}</b>\n"
        f"Текущая цена:      <b>{stock.current_price:.2f} ₽</b>\n"
        f"Базовая цена:      <b>{stock.base_price:.2f} ₽</b>\n"
        f"В вашем портфеле:  <b>{owned_shares} шт.</b>\n\n"
        f"<i>\"{stock.description}\"</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await query.message.edit_text(text=text, reply_markup=get_stock_detail_keyboard(stock.ticker))
    await query.answer()


@invest_router.callback_query(F.data.startswith("stock:buy:"))
async def handle_buy_prompt(
    query: CallbackQuery, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Prompt user for quantity of shares to purchase."""
    ticker = query.data.split(":")[-1]
    stock = await InvestmentService.get_stock(session, ticker)
    if not stock:
        await query.answer("Акция не найдена.", show_alert=True)
        return

    main_acc = await BankingService.get_user_main_account(session, user.id)
    avail_bal = main_acc.balance if main_acc else Decimal("0.00")

    await state.set_state(InvestStates.waiting_for_shares)
    await state.update_data(ticker=ticker, action="BUY")

    prompt = (
        f"🛒 <b>Покупка акций {stock.ticker}</b>\n\n"
        f"Цена за 1 акцию: <b>{stock.current_price:.2f} ₽</b>\n"
        f"Доступно на карте: <b>{avail_bal:.2f} ₽</b>\n"
        f"Комиссия брокера: <b>{settings.BROKER_FEE_PERCENT * 100:.1f}%</b>\n\n"
        "Сколько акций хотите купить? Введите целое число:"
    )
    await query.message.answer(prompt, reply_markup=get_cancel_reply_keyboard())
    await query.answer()


@invest_router.callback_query(F.data.startswith("stock:sell:"))
async def handle_sell_prompt(
    query: CallbackQuery, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Prompt user for quantity of shares to liquidate."""
    ticker = query.data.split(":")[-1]
    stock = await InvestmentService.get_stock(session, ticker)
    if not stock:
        await query.answer("Акция не найдена.", show_alert=True)
        return

    portfolio = await InvestmentService.get_portfolio(session, user.id)
    owned_entry = next((h for h in portfolio["holdings"] if h["ticker"] == stock.ticker), None)
    owned_shares = owned_entry["shares"] if owned_entry else 0

    if owned_shares <= 0:
        await query.answer("У вас нет этих акций для продажи.", show_alert=True)
        return

    await state.set_state(InvestStates.waiting_for_shares)
    await state.update_data(ticker=ticker, action="SELL")

    prompt = (
        f"💰 <b>Продажа акций {stock.ticker}</b>\n\n"
        f"Цена за 1 акцию: <b>{stock.current_price:.2f} ₽</b>\n"
        f"У вас в портфеле: <b>{owned_shares} шт.</b>\n"
        f"Комиссия брокера: <b>{settings.BROKER_FEE_PERCENT * 100:.1f}%</b>\n\n"
        f"Сколько акций хотите продать (от 1 до {owned_shares})?"
    )
    await query.message.answer(prompt, reply_markup=get_cancel_reply_keyboard())
    await query.answer()


@invest_router.message(InvestStates.waiting_for_shares)
async def process_invest_shares(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Validate requested shares and present trade confirmation inline keyboard."""
    text = (message.text or "").strip()
    if text in ["❌ Отмена", "/cancel"]:
        await state.clear()
        await message.answer("Сделка отменена.", reply_markup=get_main_menu_keyboard())
        return

    if not text.isdigit() or int(text) <= 0:
        await message.answer("❌ Количество акций должно быть положительным целым числом. Введите число:")
        return

    shares = int(text)
    data = await state.get_data()
    ticker = data.get("ticker")
    action = data.get("action")

    stock = await InvestmentService.get_stock(session, ticker)
    if not stock:
        await message.answer("Акция не найдена.", reply_markup=get_main_menu_keyboard())
        await state.clear()
        return

    subtotal = Decimal(str(shares)) * stock.current_price
    fee = (subtotal * Decimal(str(settings.BROKER_FEE_PERCENT))).quantize(Decimal("0.01"))

    if action == "BUY":
        total_cost = subtotal + fee
        main_acc = await BankingService.get_user_main_account(session, user.id)
        if not main_acc or main_acc.balance < total_cost:
            cur_bal = main_acc.balance if main_acc else Decimal("0.00")
            await message.answer(
                f"❌ Недостаточно средств для покупки. Требуется: <b>{total_cost:.2f} ₽</b> (с комиссией), доступно: <b>{cur_bal:.2f} ₽</b>.\n"
                "Введите меньшее количество акций:"
            )
            return

        confirm_text = (
            f"🛒 <b>Подтверждение покупки акций</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Акция:        <b>{stock.name} ({stock.ticker})</b>\n"
            f"Количество:   <b>{shares} шт.</b>\n"
            f"Цена за шт.:  <b>{stock.current_price:.2f} ₽</b>\n"
            f"Сумма:        <b>{subtotal:.2f} ₽</b>\n"
            f"Комиссия:     <b>{fee:.2f} ₽</b>\n"
            f"Итого к оплате: <b>{total_cost:.2f} ₽</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Подтвердите сделку:"
        )
    else:  # SELL
        portfolio = await InvestmentService.get_portfolio(session, user.id)
        owned_entry = next((h for h in portfolio["holdings"] if h["ticker"] == stock.ticker), None)
        owned_shares = owned_entry["shares"] if owned_entry else 0
        if shares > owned_shares:
            await message.answer(
                f"❌ У вас нет {shares} шт. акций {stock.ticker} (доступно для продажи: {owned_shares} шт.).\n"
                "Введите корректное число:"
            )
            return

        net_proceeds = subtotal - fee
        confirm_text = (
            f"💰 <b>Подтверждение продажи акций</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Акция:        <b>{stock.name} ({stock.ticker})</b>\n"
            f"Количество:   <b>{shares} шт.</b>\n"
            f"Цена за шт.:  <b>{stock.current_price:.2f} ₽</b>\n"
            f"Сумма:        <b>{subtotal:.2f} ₽</b>\n"
            f"Комиссия:     <b>{fee:.2f} ₽</b>\n"
            f"Итого на карту: <b>{net_proceeds:.2f} ₽</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Подтвердите сделку:"
        )

    await state.set_state(InvestStates.confirm_trade)
    await message.answer(
        text=confirm_text,
        reply_markup=get_trade_confirm_keyboard(action, ticker, shares),
    )


@invest_router.callback_query(F.data.startswith("invest:confirm:"))
async def handle_execute_trade(
    query: CallbackQuery, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Execute stock buy or sell order and update portfolio atomically."""
    parts = query.data.split(":")
    action = parts[2]
    ticker = parts[3]
    shares = int(parts[4])

    try:
        if action == "BUY":
            res = await InvestmentService.buy_stock(session, user.id, ticker, shares)
            await session.commit()
            msg = (
                f"🎉 <b>Сделка исполнена!</b>\n"
                f"Куплено: <b>{shares} шт. {res['ticker']}</b>\n"
                f"Сумма с комиссией: <b>{res['total_cost']:.2f} ₽</b>\n"
                f"Всего акций в портфеле: <b>{res['total_shares']} шт.</b>\n"
                f"Средняя цена позиции: <b>{res['average_buy_price']:.2f} ₽</b>"
            )
        else:  # SELL
            res = await InvestmentService.sell_stock(session, user.id, ticker, shares)
            await session.commit()
            pnl_sign = "+" if res["realized_pnl"] >= Decimal("0.00") else ""
            msg = (
                f"🎉 <b>Сделка исполнена!</b>\n"
                f"Продано: <b>{shares} шт. {res['ticker']}</b>\n"
                f"Зачислено на карту: <b>{res['net_proceeds']:.2f} ₽</b>\n"
                f"Реализованная прибыль/убыток: <b>{pnl_sign}{res['realized_pnl']:.2f} ₽</b>\n"
                f"Остаток в портфеле: <b>{res['remaining_shares']} шт.</b>"
            )
        await query.message.edit_text(msg)
        await query.message.answer("Главное меню:", reply_markup=get_main_menu_keyboard())
        await query.answer("Сделка успешно проведена!")
    except (InsufficientFundsError, InsufficientSharesError, ValueError) as e:
        await query.answer(str(e), show_alert=True)
        await query.message.answer(f"❌ Ошибка сделки: {e}", reply_markup=get_main_menu_keyboard())

    await state.clear()


@invest_router.callback_query(F.data == "invest:cancel")
async def handle_invest_cancel(
    query: CallbackQuery, state: FSMContext
) -> None:
    """Cancel trade and clear state."""
    await state.clear()
    await query.message.edit_text("❌ Торговая операция отменена.")
    await query.message.answer("Главное меню:", reply_markup=get_main_menu_keyboard())
    await query.answer("Отменено")
