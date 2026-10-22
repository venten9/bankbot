"""Visual Formatters for T-Bets Bookmaker and PvP High-Stakes Arena."""

import html
from decimal import Decimal
from typing import Any


def _get_bet_type_label(bet_type: str, team_home: str = "Хозяева", team_away: str = "Гости") -> str:
    """Format human-readable label for bet outcome type."""
    bt = (bet_type or "").upper()
    if bt == "HOME":
        return f"Победа «{team_home}» (П1)"
    elif bt == "DRAW":
        return "Ничья (X)"
    elif bt == "AWAY":
        return f"Победа «{team_away}» (П2)"
    elif bt == "OVER_2_5":
        return "Тотал больше 2.5 голов (ТБ 2.5)"
    elif bt == "UNDER_2_5":
        return "Тотал меньше 2.5 голов (ТМ 2.5)"
    return bt


def format_bookmaker_menu(matches: list[Any], total_bets: int = 0) -> str:
    """Format Bookmaker main lobby card."""
    lines = [
        "⚽️ <b>Т-Банк Букмекер: Линия ставок</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📌 Доступно матчей: <b>{len(matches)}</b>",
        f"📊 Ваших активных ставок: <b>{total_bets}</b>",
        "💰 Лимиты: от <b>100 ₽</b> до <b>20 000 000 ₽</b>",
        "📈 Маржа букмекера: <b>5.0%</b> (справедливые коэффициенты)\n",
    ]

    if not matches:
        lines.append("<i>В данный момент нет запланированных матчей. Загляните позже!</i>\n")
    else:
        lines.append("🔥 <b>Ближайшие топовые события:</b>")
        for match in matches[:5]:
            icon = "⚽️" if getattr(match, "sport", "FOOTBALL") == "FOOTBALL" else "🎮"
            home = html.escape(str(getattr(match, "team_home", "Команда 1")), quote=False)
            away = html.escape(str(getattr(match, "team_away", "Команда 2")), quote=False)
            odds_h = float(getattr(match, "odds_home", 2.0))
            odds_a = float(getattr(match, "odds_away", 2.0))
            lines.append(f"• {icon} <b>{home}</b> ({odds_h:.2f}) vs <b>{away}</b> ({odds_a:.2f})")
        lines.append("")

    lines.append("<i>Выберите матч ниже, чтобы сделать ставку или посмотреть Live-трансляцию!</i>")
    return "\n".join(lines)


def format_match_detail(match: Any, user_bets: list[Any] | None = None) -> str:
    """Format detailed match card with odds grid and user bets."""
    icon = "⚽️" if getattr(match, "sport", "FOOTBALL") == "FOOTBALL" else "🎮"
    home = html.escape(str(getattr(match, "team_home", "Команда 1")), quote=False)
    away = html.escape(str(getattr(match, "team_away", "Команда 2")), quote=False)
    status = getattr(match, "status", "SCHEDULED")

    home_rating = float(getattr(match, "home_rating", 85.0))
    away_rating = float(getattr(match, "away_rating", 85.0))
    odds_h = float(getattr(match, "odds_home", 2.0))
    odds_d = float(getattr(match, "odds_draw", 3.0))
    odds_a = float(getattr(match, "odds_away", 2.0))
    odds_o = float(getattr(match, "odds_over", 1.85))
    odds_u = float(getattr(match, "odds_under", 1.85))

    status_str = "⏳ Запланирован"
    if status == "LIVE":
        score_h = getattr(match, "score_home", 0)
        score_a = getattr(match, "score_away", 0)
        status_str = f"🔴 <b>LIVE ({score_h} : {score_a})</b>"
    elif status == "FINISHED":
        score_h = getattr(match, "score_home", 0)
        score_a = getattr(match, "score_away", 0)
        status_str = f"🏁 <b>Завершен ({score_h} : {score_a})</b>"
    elif status == "CANCELLED":
        status_str = "🚫 <b>Отменен</b>"

    lines = [
        f"{icon} <b>{home} — {away}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"⏱ Статус: {status_str}",
        f"⚔️ Рейтинг команд: <b>{home_rating:.1f}</b> vs <b>{away_rating:.1f}</b>\n",
        "📊 <b>Коэффициенты букмекера:</b>",
        f"├ П1 ({home}): <b>{odds_h:.2f}</b>",
        f"├ X (Ничья): <b>{odds_d:.2f}</b>",
        f"├ П2 ({away}): <b>{odds_a:.2f}</b>",
        f"├ ТБ 2.5 (Больше 2.5 голов): <b>{odds_o:.2f}</b>",
        f"└ ТМ 2.5 (Меньше 2.5 голов): <b>{odds_u:.2f}</b>\n",
    ]

    bets_list = user_bets or []
    if bets_list:
        lines.append(f"📋 <b>Ваши ставки на этот матч ({len(bets_list)}):</b>")
        for b in bets_list:
            b_type = _get_bet_type_label(getattr(b, "bet_type", ""), home, away)
            b_amt = float(getattr(b, "amount", 0))
            b_odds = float(getattr(b, "odds", 1.0))
            b_status = getattr(b, "status", "PENDING")
            b_status_icon = "⏳" if b_status == "PENDING" else ("🏆" if b_status == "WON" else "❌")
            lines.append(f"• {b_status_icon} {b_type} — <b>{b_amt:,.2f} ₽</b> (кэф {b_odds:.2f}) [{b_status}]")
        lines.append("")

    if status == "SCHEDULED":
        lines.append("<i>Выберите исход ниже, затем укажите сумму ставки (до 20 000 000 ₽).</i>")
    return "\n".join(lines)


def format_match_simulation(
    match: Any,
    events: list[dict[str, Any]],
    user_bets: list[Any] | None = None,
) -> str:
    """Format 90-minute chronological match simulation commentary and settlement."""
    icon = "⚽️" if getattr(match, "sport", "FOOTBALL") == "FOOTBALL" else "🎮"
    home = html.escape(str(getattr(match, "team_home", "Команда 1")), quote=False)
    away = html.escape(str(getattr(match, "team_away", "Команда 2")), quote=False)
    score_h = getattr(match, "score_home", 0)
    score_a = getattr(match, "score_away", 0)

    lines = [
        f"{icon} <b>Матч-центр: {home} vs {away}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🏁 Итоговый счет: <b>{home} {score_h} : {score_a} {away}</b>\n",
        "📜 <b>Хроника ключевых событий:</b>",
    ]

    if not events:
        lines.append("• ⏱ Спокойный матч без ярких моментов.")
    else:
        for ev in events[:12]:  # Display up to 12 most important events
            min_str = f"{ev.get('minute', 0)}'"
            desc = html.escape(str(ev.get("description", "")), quote=False)
            ev_type = str(ev.get("type", ev.get("event_type", ""))).upper()
            if "GOAL" in ev_type:
                ev_icon = "⚽️"
            elif "CARD" in ev_type or "YELLOW" in ev_type:
                ev_icon = "🟨"
            elif "RED" in ev_type:
                ev_icon = "🟥"
            elif "SAVE" in ev_type:
                ev_icon = "🧤"
            elif "CLUTCH" in ev_type or "ACE" in ev_type:
                ev_icon = "💣"
            elif "WHISTLE" in ev_type:
                ev_icon = "🏁"
            else:
                ev_icon = "⏱"
            lines.append(f"• {ev_icon} <b>{min_str}</b> {desc}")

    lines.append("")

    bets_list = user_bets or []
    if bets_list:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🏆 <b>Итоги ваших ставок:</b>")
        for b in bets_list:
            b_type = _get_bet_type_label(getattr(b, "bet_type", ""), home, away)
            b_amt = float(getattr(b, "amount", 0))
            b_status = getattr(b, "status", "PENDING")
            b_payout = float(getattr(b, "potential_payout", 0))
            if b_status == "WON":
                lines.append(f"• 🟢 <b>ВЫИГРЫШ:</b> {b_type} — <b>+{b_payout:,.2f} ₽</b> зачислено на карту!")
            elif b_status == "LOST":
                lines.append(f"• 🔴 <b>ПРОИГРЫШ:</b> {b_type} — ставка <b>{b_amt:,.2f} ₽</b> сгорела.")
            else:
                lines.append(f"• ⚪️ {b_type} — статус {b_status}.")

    return "\n".join(lines)


def format_bet_placed(bet: Any, match: Any, new_balance: Decimal | float) -> str:
    """Format official digital betting receipt with fiscal verification."""
    home = html.escape(str(getattr(match, "team_home", "Команда 1")), quote=False)
    away = html.escape(str(getattr(match, "team_away", "Команда 2")), quote=False)
    bet_type_label = _get_bet_type_label(getattr(bet, "bet_type", ""), home, away)
    bet_id = str(getattr(bet, "id", ""))[:8]
    amount = float(getattr(bet, "amount", 0))
    odds = float(getattr(bet, "odds", 1.0))
    potential = float(getattr(bet, "potential_payout", 0))
    bal = float(new_balance)

    lines = [
        "🎫 <b>Т-Банк Букмекер: Ставка принята!</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔢 Номер билета: <code>#{bet_id}</code>",
        f"⚽️ Событие: <b>{home} vs {away}</b>",
        f"🎯 Выбранный исход: <b>{bet_type_label}</b>",
        f"📈 Коэффициент: <b>{odds:.2f}</b>",
        f"💵 Сумма ставки: <b>{amount:,.2f} ₽</b>",
        f"🏆 Возможный выигрыш: <b>{potential:,.2f} ₽</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"💳 Остаток на карте: <b>{bal:,.2f} ₽</b>",
        "🔒 <i>Электронный чек зарегистрирован в реестре Т-Банка.</i>",
    ]
    return "\n".join(lines)


def format_duels_lobby(
    duels: list[Any],
    total_pot: Decimal | float = Decimal("0.00"),
) -> str:
    """Format PvP Duels lobby card."""
    pot_val = float(total_pot)
    lines = [
        "🎲 <b>Т-Банк PvP Арена & Азартные Игры</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"⚔️ Открытых дуэлей: <b>{len(duels)}</b>",
        f"💰 Банк открытых ставок: <b>{pot_val:,.2f} ₽</b>",
        "🏛 Комиссия Т-Банка с победителя: <b>3%</b>",
        "🛡 Гарантия честности: средства депонируются в эскроу-сейф.\n",
        "🕹 <b>Доступные дисциплины:</b>",
        "• 🎲 <b>Кости</b> — бросок кубиков (1-6), побеждает старший бросок.",
        "• 🪙 <b>Монетка</b> — честный 50/50 бросок на выбывание.",
        "• 🔫 <b>Русская рулетка</b> — одиночная лестница x2..x32 с выходом в любой момент!\n",
    ]

    if not duels:
        lines.append("<i>В лобби пока нет вызовов. Создайте первую дуэль и ждите соперника!</i>")
    else:
        lines.append("🔥 <b>Ожидают соперника:</b>")
        for d in duels[:5]:
            gt = getattr(d, "game_type", "DICE")
            icon = "🎲" if gt == "DICE" else ("🪙" if gt == "COINFLIP" else "🔫")
            stake = float(getattr(d, "stake", 0))
            lines.append(f"• {icon} Дуэль ({gt}) на ставку <b>{stake:,.0f} ₽</b>")

    return "\n".join(lines)


def format_duel_result(
    duel: Any,
    creator: Any,
    opponent: Any,
    winner: Any | None,
    roll_c: int | None,
    roll_o: int | None,
    payout: Decimal | float,
    commission: Decimal | float,
) -> str:
    """Format high-tension duel result resolution card."""
    c_name = html.escape(
        getattr(creator, "full_name", None)
        or getattr(creator, "username", None)
        or f"Игрок #{getattr(creator, 'id', 1)}",
        quote=False,
    )
    o_name = html.escape(
        getattr(opponent, "full_name", None)
        or getattr(opponent, "username", None)
        or f"Игрок #{getattr(opponent, 'id', 2)}",
        quote=False,
    )
    gt = getattr(duel, "game_type", "DICE")
    icon = "🎲" if gt == "DICE" else ("🪙" if gt == "COINFLIP" else "🔫")
    stake = float(getattr(duel, "stake", 0))
    pot = stake * 2
    pay_val = float(payout)
    comm_val = float(commission)

    lines = [
        f"{icon} <b>Результат PvP Дуэли ({gt})</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"👤 Создатель: <b>{c_name}</b> (ставка {stake:,.0f} ₽)",
        f"👤 Соперник: <b>{o_name}</b> (ставка {stake:,.0f} ₽)",
        f"💰 Общий банк: <b>{pot:,.0f} ₽</b>\n",
    ]

    if gt == "DICE":
        lines.append(f"🎲 <b>Бросок {c_name}</b>: [{roll_c}]")
        lines.append(f"🎲 <b>Бросок {o_name}</b>: [{roll_o}]\n")
    elif gt == "COINFLIP":
        lines.append(f"🪙 <b>Орел/Решка:</b> {c_name} ставил на Орла, {o_name} на Решку.\n")
    elif gt == "ROULETTE":
        lines.append(f"🔫 <b>Барабан револьвера:</b> 1 патрон, 6 камор.\n")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    if winner is not None:
        w_name = html.escape(
            getattr(winner, "full_name", None)
            or getattr(winner, "username", None)
            or f"Игрок #{getattr(winner, 'id', 0)}",
            quote=False,
        )
        lines.append(f"🏆 <b>Победу одержал: {w_name}!</b>")
        lines.append(f"💵 Зачислено победителю: <b>+{pay_val:,.2f} ₽</b>")
        lines.append(f"🏛 Банковская комиссия (3%): <b>{comm_val:,.2f} ₽</b>")
    else:
        lines.append("🤝 <b>Боевая ничья!</b>")
        lines.append(f"💵 Ставки ({stake:,.0f} ₽) возвращены обоим участникам в полном объеме.")

    return "\n".join(lines)


def format_roulette_solo_round(
    round_num: int,
    stake: Decimal | float,
    current_pot: Decimal | float,
    next_pot: Decimal | float,
    multiplier: int,
) -> str:
    """Format solo Russian Roulette survival round card."""
    stk_val = float(stake)
    cur_val = float(current_pot)
    nxt_val = float(next_pot)

    lines = [
        f"🔫 <b>Русская рулетка: Раунд {round_num}/5</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "🎯 В барабане 1 боевой патрон и 5 пустых камор (шанс выжить 83.3%).",
        f"⚡️ Текущий множитель: <b>x{multiplier}</b>",
        f"💵 Исходная ставка: <b>{stk_val:,.2f} ₽</b>",
        f"💰 Текущий куш на кону: <b>{cur_val:,.2f} ₽</b>",
        f"🔥 Следующий шаг принесет: <b>{nxt_val:,.2f} ₽ (x{multiplier * 2})</b>\n",
        "<i>Спустите курок ради умножения куша или заберите деньги прямо сейчас!</i>",
    ]
    return "\n".join(lines)


__all__ = [
    "format_bookmaker_menu",
    "format_match_detail",
    "format_match_simulation",
    "format_bet_placed",
    "format_duels_lobby",
    "format_duel_result",
    "format_roulette_solo_round",
]
