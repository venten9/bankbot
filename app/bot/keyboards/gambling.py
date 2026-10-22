"""Inline Keyboard Markups for T-Bets Bookmaker & PvP High-Stakes Arena."""

import datetime
from typing import Any
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# ----------------------------------------------------------------------
# 1. Bookmaker Keyboards
# ----------------------------------------------------------------------


def get_bookmaker_menu_keyboard(
    matches: list[Any],
    current_filter: str = "ALL",
) -> InlineKeyboardMarkup:
    """Return Bookmaker match listing with sport filter tabs."""
    buttons: list[list[InlineKeyboardButton]] = []
    norm_filter = (current_filter or "ALL").upper()

    # Sport tabs
    buttons.append([
        InlineKeyboardButton(
            text="⚽️ Футбол" + (" •" if norm_filter == "FOOTBALL" else ""),
            callback_data="bet:f:FOOTBALL",
        ),
        InlineKeyboardButton(
            text="🎮 Киберспорт" + (" •" if norm_filter == "ESPORTS" else ""),
            callback_data="bet:f:ESPORTS",
        ),
        InlineKeyboardButton(
            text="📋 Все" + (" •" if norm_filter == "ALL" else ""),
            callback_data="bet:f:ALL",
        ),
    ])

    # Match rows (compact callback: bet:m:<match_id>)
    for match in matches[:8]:  # Up to 8 matches per view
        icon = "⚽️" if getattr(match, "sport", "FOOTBALL") == "FOOTBALL" else "🎮"
        home = getattr(match, "team_home", "Команда 1")
        away = getattr(match, "team_away", "Команда 2")
        btn_text = f"{icon} {home} vs {away}"
        mid = str(getattr(match, "id", ""))
        buttons.append([
            InlineKeyboardButton(
                text=btn_text[:45],
                callback_data=f"bet:m:{mid}",
            )
        ])

    # Action row: My bets, Refresh & Randomize line
    buttons.append([
        InlineKeyboardButton(text="📋 Мои ставки", callback_data="bet:my"),
        InlineKeyboardButton(text="🔄 Обновить", callback_data="bet:menu"),
    ])
    buttons.append([
        InlineKeyboardButton(text="🎲 Новая линия матчей", callback_data="bet:gen"),
    ])

    # Back to Zarabotok Hub
    buttons.append([
        InlineKeyboardButton(text="« В меню заработка", callback_data="jobs:menu"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_match_detail_keyboard(
    match: Any,
    has_active_bets: bool = False,
) -> InlineKeyboardMarkup:
    """Return match odds selection keyboard.

    Odds buttons:
    Row 1: П1 (odds_home), X (odds_draw), П2 (odds_away)
    Row 2: ТБ 2.5 (odds_over), ТМ 2.5 (odds_under)
    Row 3: Live match simulation trigger
    """
    buttons: list[list[InlineKeyboardButton]] = []
    mid = str(getattr(match, "id", ""))
    status = getattr(match, "status", "SCHEDULED")

    odds_h = float(getattr(match, "odds_home", 2.0))
    odds_d = float(getattr(match, "odds_draw", 3.0))
    odds_a = float(getattr(match, "odds_away", 2.0))
    odds_o = float(getattr(match, "odds_over", 1.85))
    odds_u = float(getattr(match, "odds_under", 1.85))

    if status == "SCHEDULED":
        # Odds row 1: 1X2
        buttons.append([
            InlineKeyboardButton(text=f"П1 {odds_h:.2f}", callback_data=f"b:pk:{mid}:H"),
            InlineKeyboardButton(text=f"X {odds_d:.2f}", callback_data=f"b:pk:{mid}:D"),
            InlineKeyboardButton(text=f"П2 {odds_a:.2f}", callback_data=f"b:pk:{mid}:A"),
        ])
        # Odds row 2: Over/Under 2.5
        buttons.append([
            InlineKeyboardButton(text=f"ТБ 2.5 ({odds_o:.2f})", callback_data=f"b:pk:{mid}:O"),
            InlineKeyboardButton(text=f"ТМ 2.5 ({odds_u:.2f})", callback_data=f"b:pk:{mid}:U"),
        ])
        # Live Simulation Trigger with Countdown
        now = datetime.datetime.now()
        sched = getattr(match, "scheduled_at", None)
        rem_sec = int((sched - now).total_seconds()) if sched else 0
        if rem_sec > 0:
            rem_m = rem_sec // 60
            rem_s = rem_sec % 60
            sim_title = f"⏳ Матч идет ({rem_m}м {rem_s:02d}с)"
        else:
            sim_title = "▶️ Финальный свисток (Результат)"

        buttons.append([
            InlineKeyboardButton(text=sim_title, callback_data=f"b:sim:{mid}"),
        ])
    else:
        # LIVE or FINISHED
        buttons.append([
            InlineKeyboardButton(text="📊 Хроника матча & События", callback_data=f"b:sim:{mid}"),
        ])

    # Navigation
    buttons.append([
        InlineKeyboardButton(text="« К матчам", callback_data="bet:menu"),
        InlineKeyboardButton(text="💼 Меню заработка", callback_data="jobs:menu"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_bet_amount_keyboard(match_id: str, outcome_code: str) -> InlineKeyboardMarkup:
    """Return quick bet preset buttons up to 20M ₽.

    outcome_code: H (HOME), D (DRAW), A (AWAY), O (OVER_2_5), U (UNDER_2_5)
    Presets: 10k, 100k, 1m, 5m, 20m, custom.
    """
    mid = match_id
    oc = outcome_code
    buttons = [
        [
            InlineKeyboardButton(text="10 000 ₽", callback_data=f"b:st:{mid}:{oc}:10k"),
            InlineKeyboardButton(text="100 000 ₽", callback_data=f"b:st:{mid}:{oc}:100k"),
        ],
        [
            InlineKeyboardButton(text="1 000 000 ₽", callback_data=f"b:st:{mid}:{oc}:1m"),
            InlineKeyboardButton(text="5 000 000 ₽", callback_data=f"b:st:{mid}:{oc}:5m"),
        ],
        [
            InlineKeyboardButton(text="🔥 20 000 000 ₽ (MAX)", callback_data=f"b:st:{mid}:{oc}:20m"),
            InlineKeyboardButton(text="✏️ Своя сумма", callback_data=f"b:st:{mid}:{oc}:custom"),
        ],
        [
            InlineKeyboardButton(text="« Назад к матчу", callback_data=f"bet:m:{mid}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ----------------------------------------------------------------------
# 2. PvP Duels Keyboards
# ----------------------------------------------------------------------


def get_duels_menu_keyboard(
    open_duels: list[Any] | None = None,
    current_filter: str = "ALL",
) -> InlineKeyboardMarkup:
    """Return PvP Duels & High-Stakes Arena lobby keyboard."""
    buttons: list[list[InlineKeyboardButton]] = []
    norm_filter = (current_filter or "ALL").upper()

    # Game type filter tabs
    buttons.append([
        InlineKeyboardButton(
            text="🎲 Кости" + (" •" if norm_filter == "DICE" else ""),
            callback_data="d:f:DICE",
        ),
        InlineKeyboardButton(
            text="🪙 Монетка" + (" •" if norm_filter == "COINFLIP" else ""),
            callback_data="d:f:COINFLIP",
        ),
        InlineKeyboardButton(
            text="🔫 Рулетка" + (" •" if norm_filter == "ROULETTE" else ""),
            callback_data="d:f:ROULETTE",
        ),
        InlineKeyboardButton(
            text="📋 Все" + (" •" if norm_filter == "ALL" else ""),
            callback_data="d:f:ALL",
        ),
    ])

    # Open duel items
    duels_list = open_duels or []
    for duel in duels_list[:6]:
        gt = getattr(duel, "game_type", "DICE")
        icon = "🎲" if gt == "DICE" else ("🪙" if gt == "COINFLIP" else "🔫")
        stake = float(getattr(duel, "stake", 0))
        stake_str = f"{stake:,.0f} ₽".replace(",", " ")
        did = str(getattr(duel, "id", ""))
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} Вызов на {stake_str}",
                callback_data=f"d:view:{did}",
            )
        ])

    # Action rows
    buttons.append([
        InlineKeyboardButton(text="⚔️ Создать дуэль", callback_data="d:create"),
        InlineKeyboardButton(text="🔫 Рулетка (Соло)", callback_data="d:rr:menu"),
    ])
    buttons.append([
        InlineKeyboardButton(text="📋 Мои дуэли", callback_data="d:my"),
        InlineKeyboardButton(text="🔄 Обновить", callback_data="d:menu"),
    ])
    buttons.append([
        InlineKeyboardButton(text="« В меню заработка", callback_data="jobs:menu"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_duel_stake_preset_keyboard(game_type: str) -> InlineKeyboardMarkup:
    """Return stake preset buttons for creating a new duel."""
    gt = (game_type or "DICE").upper()
    buttons = [
        [
            InlineKeyboardButton(text="1 000 ₽", callback_data=f"d:new:{gt}:1k"),
            InlineKeyboardButton(text="10 000 ₽", callback_data=f"d:new:{gt}:10k"),
        ],
        [
            InlineKeyboardButton(text="100 000 ₽", callback_data=f"d:new:{gt}:100k"),
            InlineKeyboardButton(text="1 000 000 ₽", callback_data=f"d:new:{gt}:1m"),
        ],
        [
            InlineKeyboardButton(text="5 000 000 ₽", callback_data=f"d:new:{gt}:5m"),
            InlineKeyboardButton(text="🔥 20 000 000 ₽", callback_data=f"d:new:{gt}:20m"),
        ],
        [
            InlineKeyboardButton(text="✏️ Своя сумма", callback_data=f"d:new:{gt}:custom"),
            InlineKeyboardButton(text="« Отмена", callback_data="d:menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_duel_detail_keyboard(duel: Any, is_creator: bool = False) -> InlineKeyboardMarkup:
    """Return duel action keyboard (Accept or Cancel)."""
    buttons: list[list[InlineKeyboardButton]] = []
    did = str(getattr(duel, "id", ""))
    status = getattr(duel, "status", "OPEN")

    if status == "OPEN":
        if is_creator:
            buttons.append([
                InlineKeyboardButton(text="❌ Отозвать вызов (Возврат)", callback_data=f"d:cnl:{did}")
            ])
        else:
            stake = float(getattr(duel, "stake", 0))
            stake_str = f"{stake:,.0f} ₽".replace(",", " ")
            buttons.append([
                InlineKeyboardButton(text=f"⚔️ Принять дуэль ({stake_str})", callback_data=f"d:acc:{did}")
            ])

    buttons.append([
        InlineKeyboardButton(text="« В лобби дуэлей", callback_data="d:menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_roulette_solo_keyboard(round_num: int, can_cashout: bool = True) -> InlineKeyboardMarkup:
    """Return solo Russian Roulette survival keyboard (pull trigger or cash out)."""
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="🔫 Спустить курок!", callback_data="d:rr:pull"),
        ],
    ]
    if can_cashout:
        buttons.append([
            InlineKeyboardButton(text="💰 Забрать куш и выйти", callback_data="d:rr:cash"),
        ])
    buttons.append([
        InlineKeyboardButton(text="« Назад в Арену", callback_data="d:menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_roulette_stake_preset_keyboard() -> InlineKeyboardMarkup:
    """Return stake preset buttons for solo Russian Roulette."""
    buttons = [
        [
            InlineKeyboardButton(text="1 000 ₽", callback_data="d:rr:stk:1k"),
            InlineKeyboardButton(text="10 000 ₽", callback_data="d:rr:stk:10k"),
        ],
        [
            InlineKeyboardButton(text="100 000 ₽", callback_data="d:rr:stk:100k"),
            InlineKeyboardButton(text="1 000 000 ₽", callback_data="d:rr:stk:1m"),
        ],
        [
            InlineKeyboardButton(text="5 000 000 ₽", callback_data="d:rr:stk:5m"),
            InlineKeyboardButton(text="🔥 20 000 000 ₽", callback_data="d:rr:stk:20m"),
        ],
        [
            InlineKeyboardButton(text="✏️ Своя сумма", callback_data="d:rr:stk:custom"),
            InlineKeyboardButton(text="« Отмена", callback_data="d:menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


__all__ = [
    "get_bookmaker_menu_keyboard",
    "get_match_detail_keyboard",
    "get_bet_amount_keyboard",
    "get_duels_menu_keyboard",
    "get_duel_stake_preset_keyboard",
    "get_duel_detail_keyboard",
    "get_roulette_solo_keyboard",
    "get_roulette_stake_preset_keyboard",
]
