"""Telegram Router for T-Bets Sports Bookmaker & PvP High-Stakes Arena."""

import datetime
import html
import json
from decimal import Decimal, InvalidOperation
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.formatters.gambling import (
    format_bet_placed,
    format_bookmaker_menu,
    format_duel_result,
    format_duels_lobby,
    format_match_detail,
    format_match_simulation,
    format_roulette_solo_round,
)
from app.bot.keyboards.gambling import (
    get_bet_amount_keyboard,
    get_bookmaker_menu_keyboard,
    get_duel_detail_keyboard,
    get_duel_stake_preset_keyboard,
    get_duels_menu_keyboard,
    get_match_detail_keyboard,
    get_roulette_solo_keyboard,
    get_roulette_stake_preset_keyboard,
)
from app.database.models import User
from app.services.banking import BankingService
from app.services.bookmaker import BookmakerService
from app.services.duels import DuelsService, RouletteSoloState
from app.services.transfers import AccountFrozenError, InsufficientFundsError

gambling_router = Router(name="gambling")


class GamblingState(StatesGroup):
    """FSM states for custom bet and duel amounts."""

    waiting_for_bet_custom_amount = State()
    waiting_for_duel_custom_stake = State()
    waiting_for_roulette_stake = State()


# Outcome code mapping
OUTCOME_MAP = {
    "H": "HOME",
    "D": "DRAW",
    "A": "AWAY",
    "O": "OVER_2_5",
    "U": "UNDER_2_5",
}

PRESET_MAP = {
    "1k": Decimal("1000.00"),
    "10k": Decimal("10000.00"),
    "100k": Decimal("100000.00"),
    "1m": Decimal("1000000.00"),
    "5m": Decimal("5000000.00"),
    "20m": Decimal("20000000.00"),
}


# ======================================================================
# 1. Message Command Entrypoints
# ======================================================================


@gambling_router.message(Command("bets"))
@gambling_router.message(Command("bookmaker"))
async def handle_bookmaker_command(
    message: Message,
    session: AsyncSession,
    user: User,
    state: FSMContext | None = None,
) -> None:
    """Open T-Bank Bookmaker match lobby."""
    if state is not None:
        await state.clear()

    matches = await BookmakerService.get_upcoming_matches(session)
    if not matches:
        matches = await BookmakerService.seed_upcoming_matches(session)

    user_bets = await BookmakerService.get_user_bets(session, user.id)
    text = format_bookmaker_menu(matches, total_bets=len(user_bets))
    markup = get_bookmaker_menu_keyboard(matches, current_filter="ALL")
    await message.answer(text=text, reply_markup=markup)


@gambling_router.message(Command("duels"))
@gambling_router.message(Command("arena"))
async def handle_duels_command(
    message: Message,
    session: AsyncSession,
    user: User,
    state: FSMContext | None = None,
) -> None:
    """Open PvP Duels and High-Stakes Arena lobby."""
    if state is not None:
        await state.clear()

    open_duels = await DuelsService.get_open_duels(session)
    total_pot = sum((d.stake for d in open_duels), Decimal("0.00"))
    text = format_duels_lobby(open_duels, total_pot=total_pot)
    markup = get_duels_menu_keyboard(open_duels, current_filter="ALL")
    await message.answer(text=text, reply_markup=markup)


# ======================================================================
# 2. Bookmaker Callbacks
# ======================================================================


@gambling_router.callback_query(F.data == "bet:menu")
async def handle_bet_menu_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext | None = None,
) -> None:
    """Return to Bookmaker match lobby in-place."""
    if state is not None:
        await state.clear()

    matches = await BookmakerService.get_upcoming_matches(session)
    if not matches:
        matches = await BookmakerService.seed_upcoming_matches(session)

    user_bets = await BookmakerService.get_user_bets(session, user.id)
    text = format_bookmaker_menu(matches, total_bets=len(user_bets))
    markup = get_bookmaker_menu_keyboard(matches, current_filter="ALL")

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=markup)
        except Exception:
            pass
    await query.answer()


@gambling_router.callback_query(F.data == "bet:gen")
async def handle_generate_random_line_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Generate randomized upcoming football and esports matches with dynamic pairings and odds."""
    await BookmakerService.seed_upcoming_matches(session)
    matches = await BookmakerService.get_upcoming_matches(session)
    user_bets = await BookmakerService.get_user_bets(session, user.id)
    text = format_bookmaker_menu(matches, total_bets=len(user_bets))
    markup = get_bookmaker_menu_keyboard(matches, current_filter="ALL")

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=markup)
        except Exception:
            pass
    await query.answer("🎲 Сформирована новая случайная линия матчей!", show_alert=False)


@gambling_router.callback_query(F.data.startswith("bet:f:"))
async def handle_bet_filter_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Filter Bookmaker matches by sport (FOOTBALL, ESPORTS, ALL)."""
    sport = query.data.split(":")[2]
    filter_arg = None if sport == "ALL" else sport
    matches = await BookmakerService.get_upcoming_matches(session, sport=filter_arg)
    if not matches and sport == "ALL":
        matches = await BookmakerService.seed_upcoming_matches(session)

    user_bets = await BookmakerService.get_user_bets(session, user.id)
    text = format_bookmaker_menu(matches, total_bets=len(user_bets))
    markup = get_bookmaker_menu_keyboard(matches, current_filter=sport)

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=markup)
        except Exception:
            pass
    await query.answer()


@gambling_router.callback_query(F.data.startswith("bet:m:"))
async def handle_match_detail_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """View detailed match card with odds and active wagers."""
    match_id = query.data.split(":")[2]
    match = await BookmakerService.get_match_by_id(session, match_id)
    if not match:
        await query.answer("Матч не найден!", show_alert=True)
        return

    all_user_bets = await BookmakerService.get_user_bets(session, user.id)
    match_bets = [b for b in all_user_bets if b.match_id == match_id]

    text = format_match_detail(match, user_bets=match_bets)
    markup = get_match_detail_keyboard(match, has_active_bets=bool(match_bets))

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=markup)
        except Exception:
            pass
    await query.answer()


@gambling_router.callback_query(F.data.startswith("b:pk:"))
async def handle_outcome_pick_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Outcome chosen (H, D, A, O, U) -> show stake amount preset buttons."""
    parts = query.data.split(":")
    match_id = parts[2]
    outcome_code = parts[3]

    match = await BookmakerService.get_match_by_id(session, match_id)
    if not match:
        await query.answer("Матч не найден!", show_alert=True)
        return

    bet_type = OUTCOME_MAP.get(outcome_code, "HOME")
    text = (
        f"🎯 <b>Выбор исхода:</b> {bet_type}\n"
        f"⚽️ <b>Матч:</b> {html.escape(match.team_home)} vs {html.escape(match.team_away)}\n\n"
        f"<i>Выберите сумму ставки (лимиты: от 100 ₽ до 20 000 000 ₽):</i>"
    )
    markup = get_bet_amount_keyboard(match_id, outcome_code)

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=markup)
        except Exception:
            pass
    await query.answer()


@gambling_router.callback_query(F.data.startswith("b:st:"))
async def handle_bet_stake_preset_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Process stake preset or trigger custom amount prompt."""
    parts = query.data.split(":")
    match_id = parts[2]
    outcome_code = parts[3]
    preset_key = parts[4]

    match = await BookmakerService.get_match_by_id(session, match_id)
    if not match:
        await query.answer("Матч не найден!", show_alert=True)
        return

    bet_type = OUTCOME_MAP.get(outcome_code, "HOME")

    if preset_key == "custom":
        await state.set_state(GamblingState.waiting_for_bet_custom_amount)
        await state.update_data(match_id=match_id, outcome_code=outcome_code)
        prompt_text = (
            f"✏️ <b>Введите желаемую сумму ставки</b> на матч "
            f"«{html.escape(match.team_home)} — {html.escape(match.team_away)}»:\n\n"
            f"<i>Сумма должна быть от 100 ₽ до 20 000 000 ₽.</i>"
        )
        if query.message:
            await query.message.answer(text=prompt_text)
        await query.answer()
        return

    amount = PRESET_MAP.get(preset_key, Decimal("10000.00"))

    main_acc = await BankingService.get_user_main_account(session, user.id)
    bal = main_acc.balance if main_acc else Decimal("0.00")

    # Responsible gaming: max 25% of balance per bet (for balances > 400 ₽)
    if bal > Decimal("400.00") and amount > (bal * Decimal("0.25")):
        max_allowed = (bal * Decimal("0.25")).quantize(Decimal("0.01"))
        await query.answer(
            f"⚠️ Ответственная игра: ставка не может превышать 25% от вашего баланса ({max_allowed:,.2f} ₽)!\n"
            f"Сделайте меньшую ставку или заработайте больше на работе.",
            show_alert=True,
        )
        return

    try:
        bet = await BookmakerService.place_bet(
            session=session,
            user_id=user.id,
            match_id=match_id,
            bet_type=bet_type,
            amount=amount,
        )
        # Match countdown timer: schedule 90 seconds into future if not already set
        now = datetime.datetime.now()
        if match.status == "SCHEDULED" and (match.scheduled_at is None or match.scheduled_at <= now):
            match.scheduled_at = now + datetime.timedelta(seconds=90)
            await session.flush()

        main_acc = await BankingService.get_user_main_account(session, user.id)
        bal = main_acc.balance if main_acc else Decimal("0.00")
        receipt_text = format_bet_placed(bet, match, bal)
        markup = get_match_detail_keyboard(match, has_active_bets=True)

        if query.message:
            try:
                await query.message.edit_text(text=receipt_text, reply_markup=markup)
            except Exception:
                await query.message.answer(text=receipt_text, reply_markup=markup)
        await query.answer("✅ Ставка успешно принята!")
    except InsufficientFundsError:
        await query.answer("❌ Недостаточно средств на счете для этой ставки!", show_alert=True)
    except AccountFrozenError:
        await query.answer("❌ Ваш банковский счет заморожен!", show_alert=True)
    except ValueError as e:
        await query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@gambling_router.message(GamblingState.waiting_for_bet_custom_amount)
async def handle_bet_custom_amount_message(
    message: Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Process custom bet amount from user text input."""
    raw_text = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(raw_text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        await message.answer("❌ Пожалуйста, введите корректное число (например: <code>50000</code>).")
        return

    data = await state.get_data()
    match_id = data.get("match_id")
    outcome_code = data.get("outcome_code", "H")
    bet_type = OUTCOME_MAP.get(outcome_code, "HOME")

    match = await BookmakerService.get_match_by_id(session, match_id) if match_id else None
    if not match:
        await message.answer("❌ Матч не найден или уже завершен.")
        await state.clear()
        return

    main_acc = await BankingService.get_user_main_account(session, user.id)
    bal = main_acc.balance if main_acc else Decimal("0.00")

    # Responsible gaming: max 25% of balance per bet (for balances > 400 ₽)
    if bal > Decimal("400.00") and amount > (bal * Decimal("0.25")):
        max_allowed = (bal * Decimal("0.25")).quantize(Decimal("0.01"))
        await message.answer(
            f"⚠️ <b>Правило ответственной игры:</b> ставка не может превышать 25% от вашего баланса "
            f"(максимум для вас: <b>{max_allowed:,.2f} ₽</b>).\n\n"
            f"<i>Это защищает от быстрого банкротства. Введите меньшую сумму или заработайте больше на карьере!</i>"
        )
        return

    try:
        bet = await BookmakerService.place_bet(
            session=session,
            user_id=user.id,
            match_id=match_id,
            bet_type=bet_type,
            amount=amount,
        )
        # Match countdown timer: schedule 90 seconds into future if not already set
        now = datetime.datetime.now()
        if match.status == "SCHEDULED" and (match.scheduled_at is None or match.scheduled_at <= now):
            match.scheduled_at = now + datetime.timedelta(seconds=90)
            await session.flush()
        await state.clear()
        main_acc = await BankingService.get_user_main_account(session, user.id)
        bal = main_acc.balance if main_acc else Decimal("0.00")
        receipt_text = format_bet_placed(bet, match, bal)
        markup = get_match_detail_keyboard(match, has_active_bets=True)
        await message.answer(text=receipt_text, reply_markup=markup)
    except InsufficientFundsError:
        await message.answer(
            f"❌ Недостаточно средств на карте для совершения ставки на сумму {amount:,.2f} ₽."
        )
    except (AccountFrozenError, ValueError) as e:
        await message.answer(f"❌ Не удалось совершить ставку: {str(e)}")


@gambling_router.callback_query(F.data.startswith("b:sim:"))
async def handle_match_simulation_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Trigger live match simulation, chronological commentary, and settlement."""
    match_id = query.data.split(":")[2]
    match = await BookmakerService.get_match_by_id(session, match_id)
    if not match:
        await query.answer("Матч не найден!", show_alert=True)
        return

    # Simulate gameplay if not finished
    if match.status not in ("FINISHED", "CANCELLED"):
        now = datetime.datetime.now()
        if match.scheduled_at and match.scheduled_at > now:
            time_left = int((match.scheduled_at - now).total_seconds())
            rem_m = time_left // 60
            rem_s = time_left % 60
            await query.answer(
                f"⏳ Матч еще идет! До финального свистка: {rem_m} мин {rem_s:02d} сек.\n"
                f"Сходите пока на смену в раздел «Заработок»!",
                show_alert=True,
            )
            return

        match = await BookmakerService.play_match(session, match_id)
        await BookmakerService.settle_match(session, match_id)

    events: list[dict[str, Any]] = []
    if match.events_log:
        try:
            events = json.loads(match.events_log)
        except Exception:
            events = []

    all_user_bets = await BookmakerService.get_user_bets(session, user.id)
    match_bets = [b for b in all_user_bets if b.match_id == match_id]

    text = format_match_simulation(match, events, user_bets=match_bets)
    markup = get_match_detail_keyboard(match, has_active_bets=bool(match_bets))

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=markup)
        except Exception:
            pass
    await query.answer("Матч завершен и ставки рассчитаны!")


@gambling_router.callback_query(F.data == "bet:my")
async def handle_my_bets_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """View list of wagers placed by the user."""
    bets = await BookmakerService.get_user_bets(session, user.id)
    lines = [
        "📋 <b>Ваша история ставок в Т-Букмекере</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not bets:
        lines.append("<i>У вас пока нет оформленных ставок. Выберите матч в линии!</i>")
    else:
        for b in bets[:10]:
            icon = "⏳" if b.status == "PENDING" else ("🟢" if b.status == "WON" else "🔴")
            amt = float(b.amount)
            odds = float(b.odds)
            pot = float(b.potential_payout)
            lines.append(
                f"{icon} <b>#{b.id[:8]}</b> | Ставка: <b>{amt:,.0f} ₽</b> (кэф {odds:.2f}) "
                f"[{b.status}] ➔ Возможный куш: {pot:,.0f} ₽"
            )

    text = "\n".join(lines)
    markup = get_bookmaker_menu_keyboard([], current_filter="ALL")
    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=markup)
        except Exception:
            pass
    await query.answer()


# ======================================================================
# 3. PvP Duels Callbacks
# ======================================================================


@gambling_router.callback_query(F.data == "duel:menu")
async def handle_duels_menu_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext | None = None,
) -> None:
    """Open PvP Duels lobby in-place."""
    if state is not None:
        await state.clear()

    open_duels = await DuelsService.get_open_duels(session)
    total_pot = sum((d.stake for d in open_duels), Decimal("0.00"))
    text = format_duels_lobby(open_duels, total_pot=total_pot)
    markup = get_duels_menu_keyboard(open_duels, current_filter="ALL")

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=markup)
        except Exception:
            pass
    await query.answer()


@gambling_router.callback_query(F.data.startswith("d:f:"))
async def handle_duel_filter_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Filter open duels by game type."""
    game_type = query.data.split(":")[2]
    filter_arg = None if game_type == "ALL" else game_type
    open_duels = await DuelsService.get_open_duels(session, game_type=filter_arg)
    total_pot = sum((d.stake for d in open_duels), Decimal("0.00"))

    text = format_duels_lobby(open_duels, total_pot=total_pot)
    markup = get_duels_menu_keyboard(open_duels, current_filter=game_type)

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=markup)
        except Exception:
            pass
    await query.answer()


@gambling_router.callback_query(F.data == "d:create")
async def handle_duel_create_dialogue(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Show stake selection for creating a new duel."""
    text = (
        "⚔️ <b>Создание нового вызова на PvP Дуэль</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите дисциплину и сумму ставки (от 100 ₽ до 20 000 000 ₽).\n"
        "Сумма будет депонирована в эскроу-сейф до принятия соперником."
    )
    markup = get_duel_stake_preset_keyboard("DICE")
    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=markup)
        except Exception:
            pass
    await query.answer()


@gambling_router.callback_query(F.data.startswith("d:new:"))
async def handle_duel_new_preset_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Process stake preset for new duel creation or prompt custom amount."""
    parts = query.data.split(":")
    game_type = parts[2]
    preset_key = parts[3]

    if preset_key == "custom":
        await state.set_state(GamblingState.waiting_for_duel_custom_stake)
        await state.update_data(game_type=game_type)
        prompt_text = (
            f"✏️ <b>Введите сумму ставки для дуэли ({game_type}):</b>\n\n"
            f"<i>Допустимо от 100 ₽ до 20 000 000 ₽.</i>"
        )
        if query.message:
            await query.message.answer(text=prompt_text)
        await query.answer()
        return

    stake = PRESET_MAP.get(preset_key, Decimal("1000.00"))

    try:
        duel = await DuelsService.create_duel(
            session=session,
            creator_user_id=user.id,
            game_type=game_type,
            stake=stake,
        )
        await query.answer(f"✅ Вызов на {stake:,.0f} ₽ создан!", show_alert=True)
        # Return to lobby
        open_duels = await DuelsService.get_open_duels(session)
        total_pot = sum((d.stake for d in open_duels), Decimal("0.00"))
        text = format_duels_lobby(open_duels, total_pot=total_pot)
        markup = get_duels_menu_keyboard(open_duels, current_filter="ALL")
        if query.message:
            await query.message.edit_text(text=text, reply_markup=markup)
    except InsufficientFundsError:
        await query.answer("❌ Недостаточно средств на карте!", show_alert=True)
    except (AccountFrozenError, ValueError) as e:
        await query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@gambling_router.message(GamblingState.waiting_for_duel_custom_stake)
async def handle_duel_custom_stake_message(
    message: Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Process custom stake text for creating a duel."""
    raw_text = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        stake = Decimal(raw_text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        await message.answer("❌ Пожалуйста, введите корректное число (например: <code>25000</code>).")
        return

    data = await state.get_data()
    game_type = data.get("game_type", "DICE")

    try:
        duel = await DuelsService.create_duel(
            session=session,
            creator_user_id=user.id,
            game_type=game_type,
            stake=stake,
        )
        await state.clear()
        open_duels = await DuelsService.get_open_duels(session)
        total_pot = sum((d.stake for d in open_duels), Decimal("0.00"))
        text = format_duels_lobby(open_duels, total_pot=total_pot)
        markup = get_duels_menu_keyboard(open_duels, current_filter="ALL")
        await message.answer(
            f"✅ Вызов на дуэль ({game_type}) со ставкой <b>{stake:,.2f} ₽</b> успешно открыт в лобби!",
            reply_markup=markup,
        )
    except InsufficientFundsError:
        await message.answer(
            f"❌ Недостаточно средств на карте для создания дуэли на сумму {stake:,.2f} ₽."
        )
    except (AccountFrozenError, ValueError) as e:
        await message.answer(f"❌ Ошибка: {str(e)}")


@gambling_router.callback_query(F.data.startswith("d:view:"))
async def handle_duel_view_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """View details of a specific open duel."""
    duel_id = query.data.split(":")[2]
    duel = await DuelsService.get_duel_by_id(session, duel_id)
    if not duel:
        await query.answer("Дуэль не найдена или уже завершена!", show_alert=True)
        return

    is_creator = duel.creator_user_id == user.id
    stake_str = f"{duel.stake:,.0f} ₽".replace(",", " ")
    gt = duel.game_type
    icon = "🎲" if gt == "DICE" else ("🪙" if gt == "COINFLIP" else "🔫")

    text = (
        f"{icon} <b>Вызов на PvP Дуэль #{duel.id[:8]}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎮 Игра: <b>{gt}</b>\n"
        f"💵 Ставка: <b>{stake_str}</b>\n"
        f"💰 Общий банк: <b>{(duel.stake * 2):,.0f} ₽</b>\n"
        f"⏱ Статус: <b>{duel.status}</b>\n\n"
    )
    if is_creator:
        text += "<i>Вы являетесь создателем этого вызова. Вы можете отозвать его с возвратом средств.</i>"
    else:
        text += "<i>Примите вызов, чтобы немедленно начать поединок!</i>"

    markup = get_duel_detail_keyboard(duel, is_creator=is_creator)
    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=markup)
        except Exception:
            pass
    await query.answer()


@gambling_router.callback_query(F.data.startswith("d:acc:"))
async def handle_duel_accept_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Opponent accepts open duel: executes CAS, runs game resolution, and awards pot."""
    duel_id = query.data.split(":")[2]

    try:
        duel, details = await DuelsService.accept_duel(session, duel_id, user.id)
    except InsufficientFundsError:
        await query.answer("❌ Недостаточно средств на карте для принятия дуэли!", show_alert=True)
        return
    except ValueError as e:
        await query.answer(f"❌ {str(e)}", show_alert=True)
        return

    # Fetch creator and opponent User records
    c_user_res = await session.execute(select(User).where(User.id == duel.creator_user_id))
    creator_user = c_user_res.scalar_one_or_none() or user

    o_user_res = await session.execute(select(User).where(User.id == duel.opponent_user_id))
    opponent_user = o_user_res.scalar_one_or_none() or user

    winner_user = None
    if duel.winner_user_id:
        w_res = await session.execute(select(User).where(User.id == duel.winner_user_id))
        winner_user = w_res.scalar_one_or_none()

    payout = details.get("payout", Decimal("0.00"))
    commission = details.get("commission", Decimal("0.00"))

    result_text = format_duel_result(
        duel=duel,
        creator=creator_user,
        opponent=opponent_user,
        winner=winner_user,
        roll_c=duel.creator_roll,
        roll_o=duel.opponent_roll,
        payout=payout,
        commission=commission,
    )
    markup = get_duels_menu_keyboard([], current_filter="ALL")

    if query.message:
        try:
            await query.message.edit_text(text=result_text, reply_markup=markup)
        except Exception:
            pass
    await query.answer("Дуэль завершена!")


@gambling_router.callback_query(F.data.startswith("d:cnl:"))
async def handle_duel_cancel_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Creator cancels open duel and receives instant stake refund."""
    duel_id = query.data.split(":")[2]
    try:
        await DuelsService.cancel_duel(session, duel_id, user.id)
        await query.answer("✅ Вызов отозван, ставка возвращена на карту!", show_alert=True)
        # Return to lobby
        open_duels = await DuelsService.get_open_duels(session)
        total_pot = sum((d.stake for d in open_duels), Decimal("0.00"))
        text = format_duels_lobby(open_duels, total_pot=total_pot)
        markup = get_duels_menu_keyboard(open_duels, current_filter="ALL")
        if query.message:
            await query.message.edit_text(text=text, reply_markup=markup)
    except Exception as e:
        await query.answer(f"❌ Не удалось отменить дуэль: {str(e)}", show_alert=True)


@gambling_router.callback_query(F.data == "d:my")
async def handle_my_duels_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """View user's active and finished duels."""
    duels = await DuelsService.get_user_duels(session, user.id)
    lines = [
        "📋 <b>Ваша история PvP Дуэлей</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not duels:
        lines.append("<i>Вы еще не участвовали в дуэлях. Создайте вызов или примите чужой!</i>")
    else:
        for d in duels[:10]:
            icon = "🎲" if d.game_type == "DICE" else ("🪙" if d.game_type == "COINFLIP" else "🔫")
            stake_val = float(d.stake)
            lines.append(
                f"{icon} #{d.id[:8]} ({d.game_type}) | Ставка: {stake_val:,.0f} ₽ | Статус: [{d.status}]"
            )

    text = "\n".join(lines)
    markup = get_duels_menu_keyboard([], current_filter="ALL")
    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=markup)
        except Exception:
            pass
    await query.answer()


# ======================================================================
# 4. Russian Roulette Solo Callbacks
# ======================================================================


@gambling_router.callback_query(F.data == "d:rr:menu")
async def handle_roulette_solo_menu_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Open solo Russian Roulette entry card with stake selection."""
    text = (
        "🔫 <b>Русская рулетка: Одиночный Экстрим</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Правила выживания:\n"
        "• В барабане револьвера 1 боевой патрон и 5 пустых камор.\n"
        "• Каждый успешный спуск курка удваивает ставку:\n"
        "  Раунд 1: <b>x2</b>\n"
        "  Раунд 2: <b>x4</b>\n"
        "  Раунд 3: <b>x8</b>\n"
        "  Раунд 4: <b>x16</b>\n"
        "  Раунд 5 (ФИНАЛ): <b>x32!</b>\n"
        "• Вы можете забрать куш после любого раунда или идти до конца!\n\n"
        "<i>Выберите ставку для начала игры (от 100 ₽ до 20 000 000 ₽):</i>"
    )
    markup = get_roulette_stake_preset_keyboard()
    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=markup)
        except Exception:
            pass
    await query.answer()


@gambling_router.callback_query(F.data.startswith("d:rr:stk:"))
async def handle_roulette_stake_preset_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Process solo Russian Roulette stake selection."""
    preset_key = query.data.split(":")[3]

    if preset_key == "custom":
        await state.set_state(GamblingState.waiting_for_roulette_stake)
        prompt_text = (
            "✏️ <b>Введите сумму ставки для Русской рулетки Solo:</b>\n\n"
            "<i>Допустимо от 100 ₽ до 20 000 000 ₽.</i>"
        )
        if query.message:
            await query.message.answer(text=prompt_text)
        await query.answer()
        return

    stake = PRESET_MAP.get(preset_key, Decimal("1000.00"))

    try:
        solo_state = await DuelsService.start_roulette_solo(session, user.id, stake)
        await state.update_data(
            rr_stake=str(stake),
            rr_step=0,
            rr_multiplier=1,
            rr_alive=True,
        )
        text = format_roulette_solo_round(
            round_num=1,
            stake=stake,
            current_pot=Decimal("0.00"),
            next_pot=stake * 2,
            multiplier=1,
        )
        markup = get_roulette_solo_keyboard(round_num=1, can_cashout=False)
        if query.message:
            try:
                await query.message.edit_text(text=text, reply_markup=markup)
            except Exception:
                pass
        await query.answer("Револьвер заряжен! Раунд 1 начался.")
    except InsufficientFundsError:
        await query.answer("❌ Недостаточно средств на карте!", show_alert=True)
    except (AccountFrozenError, ValueError) as e:
        await query.answer(f"❌ {str(e)}", show_alert=True)


@gambling_router.message(GamblingState.waiting_for_roulette_stake)
async def handle_roulette_custom_stake_message(
    message: Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Process custom stake text input for solo Russian Roulette."""
    raw_text = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        stake = Decimal(raw_text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        await message.answer("❌ Пожалуйста, введите корректное число (например: <code>10000</code>).")
        return

    try:
        solo_state = await DuelsService.start_roulette_solo(session, user.id, stake)
        await state.update_data(
            rr_stake=str(stake),
            rr_step=0,
            rr_multiplier=1,
            rr_alive=True,
        )
        text = format_roulette_solo_round(
            round_num=1,
            stake=stake,
            current_pot=Decimal("0.00"),
            next_pot=stake * 2,
            multiplier=1,
        )
        markup = get_roulette_solo_keyboard(round_num=1, can_cashout=False)
        await message.answer(text=text, reply_markup=markup)
    except InsufficientFundsError:
        await message.answer(
            f"❌ Недостаточно средств на карте для участия в игре на сумму {stake:,.2f} ₽."
        )
    except (AccountFrozenError, ValueError) as e:
        await message.answer(f"❌ Ошибка: {str(e)}")


@gambling_router.callback_query(F.data == "d:rr:pull")
async def handle_roulette_solo_pull_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Pull the trigger in solo Russian Roulette."""
    data = await state.get_data()
    raw_stake = data.get("rr_stake")
    if not raw_stake:
        await query.answer("Сессия игры не найдена. Начните заново из Арены!", show_alert=True)
        return

    stake = Decimal(raw_stake)
    current_step = int(data.get("rr_step", 0))
    multiplier = int(data.get("rr_multiplier", 1))

    st_obj = RouletteSoloState(
        user_id=user.id,
        stake=stake,
        current_step=current_step,
        multiplier=multiplier,
        is_alive=True,
        current_payout=(stake * Decimal(str(multiplier))) if current_step > 0 else Decimal("0.00"),
    )

    try:
        st_obj, survived = await DuelsService.roulette_solo_pull(session, st_obj)
    except ValueError as e:
        await query.answer(f"❌ {str(e)}", show_alert=True)
        return

    if survived:
        await state.update_data(rr_step=st_obj.current_step, rr_multiplier=st_obj.multiplier)
        if st_obj.current_step >= 5:
            # Reached step 5 (x32 maximum)! Automatic jackpot cashout!
            payout = await DuelsService.roulette_solo_cashout(session, st_obj)
            await state.clear()
            win_text = (
                "🎉 <b>НЕВЕРОЯТНО! ВЫ ВЫЖИЛИ ВО ВСЕХ 5 РАУНДАХ!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏆 Достигнут абсолютный джекпот: <b>x32</b>!\n"
                f"💰 Выигрыш <b>+{payout:,.2f} ₽</b> мгновенно зачислен на вашу карту Т-Банка!"
            )
            markup = get_duels_menu_keyboard([], current_filter="ALL")
            if query.message:
                try:
                    await query.message.edit_text(text=win_text, reply_markup=markup)
                except Exception:
                    pass
            await query.answer("ДЖЕКПОТ x32!", show_alert=True)
        else:
            # Advance to next round
            next_multiplier = st_obj.multiplier * 2
            next_pot = stake * Decimal(str(next_multiplier))
            round_text = format_roulette_solo_round(
                round_num=st_obj.current_step + 1,
                stake=stake,
                current_pot=st_obj.current_payout,
                next_pot=next_pot,
                multiplier=st_obj.multiplier,
            )
            markup = get_roulette_solo_keyboard(round_num=st_obj.current_step + 1, can_cashout=True)
            if query.message:
                try:
                    await query.message.edit_text(text=round_text, reply_markup=markup)
                except Exception:
                    pass
            await query.answer("Щелчок! Вы выжили.")
    else:
        # Fired! Player eliminated!
        await state.clear()
        lost_text = (
            "💥 <b>БАБАХ! ВЫСТРЕЛ!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Патрон оказался в каморе. Вы выбыли из раунда.\n"
            f"Ставка <b>{stake:,.2f} ₽</b> сгорела в пользу банка Т-Банк.\n\n"
            "<i>Удача любит смелых, но фортуна капризна. Попробуйте еще раз!</i>"
        )
        markup = get_duels_menu_keyboard([], current_filter="ALL")
        if query.message:
            try:
                await query.message.edit_text(text=lost_text, reply_markup=markup)
            except Exception:
                pass
        await query.answer("💥 ВЫСТРЕЛ!", show_alert=True)


@gambling_router.callback_query(F.data == "d:rr:cash")
async def handle_roulette_solo_cashout_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Cash out accumulated multiplier in solo Russian Roulette."""
    data = await state.get_data()
    raw_stake = data.get("rr_stake")
    if not raw_stake:
        await query.answer("Сессия не найдена!", show_alert=True)
        return

    stake = Decimal(raw_stake)
    step = int(data.get("rr_step", 0))
    multiplier = int(data.get("rr_multiplier", 1))

    if step == 0:
        await query.answer("Не сделано ни одного выстрела!", show_alert=True)
        return

    st_obj = RouletteSoloState(
        user_id=user.id,
        stake=stake,
        current_step=step,
        multiplier=multiplier,
        is_alive=True,
    )

    try:
        payout = await DuelsService.roulette_solo_cashout(session, st_obj)
        await state.clear()
        cashout_text = (
            "💰 <b>КУШ УСПЕШНО ЗАБРАН!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 Вы вовремя остановились на шаге {step} (множитель <b>x{multiplier}</b>).\n"
            f"💵 Выигрыш <b>+{payout:,.2f} ₽</b> зачислен на вашу карту Т-Банка!"
        )
        markup = get_duels_menu_keyboard([], current_filter="ALL")
        if query.message:
            try:
                await query.message.edit_text(text=cashout_text, reply_markup=markup)
            except Exception:
                pass
        await query.answer(f"Куш +{payout:,.2f} ₽ получен!")
    except Exception as e:
        await query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


__all__ = [
    "gambling_router",
    "GamblingState",
]
