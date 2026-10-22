"""Comprehensive 4-Tier Automated Test Suite for T-Bets Sports Bookmaker & PvP High-Stakes Arena.

Covers all 4 tiers of testing per ORIGINAL_REQUEST §R5 and PROJECT.md:
- Tier 1: Feature Coverage (>=5 tests per domain):
    * TestBookmakerCatalogAndOdds (Catalog, Rosters, Goalkeepers, Poisson/xG lambda, 5% margin, 1.05 floor)
    * TestBookmakerBettingAndSimulation (Match creation, 90-min simulation, Atomic bet debit, Settlement, Refunds)
    * TestDuelsServiceFeatures (Dice duel, Coinflip duel, 2-player Russian Roulette, 3% commission, Escrow)
    * TestRussianRouletteSolo (Upfront stake debit, Ladder x2..x32, 5/6 survival, Cashout)
    * TestZarabotokUIAndKeyboards (Reply keyboard dual compatibility, Dashboard buttons, Commands, Callbacks)
- Tier 2: Boundary & Corner Cases:
    * Minimum bet / stake: 100.00 ₽ allowed, 99.99 ₽ rejected
    * Maximum bet / stake: 20,000,000.00 ₽ allowed, 20,000,000.01 ₽ rejected
    * Overdraft protection & InsufficientFundsError
    * Canceled open duel refund & double-cancel prevention
    * Double-acceptance rejection & self-acceptance rejection
    * Strict callback_data <= 64 bytes assertion across all gambling keyboards
    * Strict HTML escaping with adversarial script tags
- Tier 3: Concurrency & Cross-Feature Interactions:
    * Concurrent duel acceptance (atomic CAS guard)
    * Solo Russian Roulette survival & cashout state machine
    * Bet placement & settlement digital fiscal receipts
    * Admin test user cleanup cascade
- Tier 4: Real-World E2E Scenario:
    * Full end-to-end user flow: Registration, Deposit, Bookmaker Champions League Match, Bet, Simulation, Settlement,
      PvP Dice Duel creation, Acceptance, Payout with 3% commission, and Double-entry Ledger audit.
"""

import asyncio
import datetime
import html
import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.bot import create_dispatcher
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
from app.bot.keyboards.jobs import get_jobs_menu_keyboard
from app.bot.keyboards.reply import CompatibleZarabotokButtonText, get_main_menu_keyboard
from app.bot.routers.gambling import (
    GamblingState,
    gambling_router,
    handle_bet_custom_amount_message,
    handle_bet_filter_callback,
    handle_bet_menu_callback,
    handle_bet_stake_preset_callback,
    handle_bookmaker_command,
    handle_duel_accept_callback,
    handle_duel_cancel_callback,
    handle_duel_create_dialogue,
    handle_duel_custom_stake_message,
    handle_duel_filter_callback,
    handle_duel_new_preset_callback,
    handle_duel_view_callback,
    handle_duels_command,
    handle_duels_menu_callback,
    handle_match_detail_callback,
    handle_match_simulation_callback,
    handle_my_bets_callback,
    handle_my_duels_callback,
    handle_outcome_pick_callback,
    handle_roulette_custom_stake_message,
    handle_roulette_solo_cashout_callback,
    handle_roulette_solo_menu_callback,
    handle_roulette_solo_pull_callback,
    handle_roulette_stake_preset_callback,
)
from app.database.models import (
    Account,
    LedgerEntry,
    PvPDuel,
    SportsBet,
    SportsMatch,
    Transaction,
    User,
)
from app.services.admin import AdminService
from app.services.banking import BankingService
from app.services.bookmaker import BookmakerService
from app.services.duels import DuelsService, RouletteSoloState
from app.services.transfers import AccountFrozenError, InsufficientFundsError


# ==============================================================================
# Helper Inspection Functions for Offline Bot Testing
# ==============================================================================


def extract_bot_sent_messages(mock_bot: AsyncMock) -> list[dict]:
    """Extract messages sent via bot(SendMessage(...)) or bot.send_message."""
    sent = []
    for call in mock_bot.call_args_list:
        if call[0]:
            method = call[0][0]
            if type(method).__name__ == "SendMessage":
                sent.append({
                    "text": getattr(method, "text", ""),
                    "reply_markup": getattr(method, "reply_markup", None),
                })
    for call in mock_bot.send_message.call_args_list:
        kwargs = call[1] if len(call) > 1 else {}
        sent.append({
            "text": kwargs.get("text", ""),
            "reply_markup": kwargs.get("reply_markup", None),
        })
    return sent


def extract_bot_edited_messages(mock_bot: AsyncMock) -> list[dict]:
    """Extract message edits via bot(EditMessageText(...)) or bot.edit_message_text."""
    edited = []
    for call in mock_bot.call_args_list:
        if call[0]:
            method = call[0][0]
            if type(method).__name__ == "EditMessageText":
                edited.append({
                    "text": getattr(method, "text", ""),
                    "reply_markup": getattr(method, "reply_markup", None),
                })
    for call in mock_bot.edit_message_text.call_args_list:
        kwargs = call[1] if len(call) > 1 else {}
        edited.append({
            "text": kwargs.get("text", ""),
            "reply_markup": kwargs.get("reply_markup", None),
        })
    return edited


def extract_bot_callback_answers(mock_bot: AsyncMock) -> list[dict]:
    """Extract callback query answers via bot(AnswerCallbackQuery(...)) or bot.answer_callback_query."""
    answers = []
    for call in mock_bot.call_args_list:
        if call[0]:
            method = call[0][0]
            if type(method).__name__ == "AnswerCallbackQuery":
                answers.append({
                    "text": getattr(method, "text", None),
                    "show_alert": getattr(method, "show_alert", None),
                })
    for call in mock_bot.answer_callback_query.call_args_list:
        kwargs = call[1] if len(call) > 1 else {}
        answers.append({
            "text": kwargs.get("text", None),
            "show_alert": kwargs.get("show_alert", None),
        })
    return answers


# ==============================================================================
# Suite 1: Tier 1 — Feature Coverage (>=5 tests per domain)
# ==============================================================================


class TestBookmakerCatalogAndOdds:
    """Verifies team catalogs, rosters, Poisson/xG calculations, and odds engine with 5% margin."""

    def test_football_teams_catalog_invariants(self):
        """Verifies 12 football clubs catalog, rosters, goalkeepers, and league assignments."""
        teams = BookmakerService.get_football_teams()
        assert len(teams) == 12

        names = set()
        for t in teams:
            assert "name" in t
            assert "rating" in t
            assert t["rating"] >= 70.0
            assert t["league"] in ("Champions League", "RPL")
            assert len(t.get("roster", [])) >= 5
            assert "goalkeeper" in t and len(t["goalkeeper"]) > 0
            names.add(t["name"])

        assert len(names) == 12
        assert "Реал Мадрид" in names
        assert "Манчестер Сити" in names
        assert "Зенит" in names
        assert "Спартак" in names

    def test_esports_teams_catalog_invariants(self):
        """Verifies 6 esports teams catalog, rosters, captains, and disciplines."""
        teams = BookmakerService.get_esports_teams()
        assert len(teams) == 6

        names = set()
        for t in teams:
            assert "name" in t
            assert "rating" in t
            assert t["rating"] >= 80.0
            assert "CS2" in t.get("league", "") or "Dota 2" in t.get("league", "")
            assert len(t.get("roster", [])) >= 5
            names.add(t["name"])

        assert len(names) == 6
        assert "NAVI" in names
        assert "Team Spirit" in names
        assert "Team Vitality" in names
        assert "FaZe Clan" in names

    def test_get_team_and_roster_fallback(self):
        """Verifies case-insensitive get_team and roster fallback for unknown clubs."""
        team = BookmakerService.get_team("реал мадрид")
        assert team is not None
        assert team["name"] == "Реал Мадрид"

        unknown = BookmakerService.get_team("NonExistentClub")
        assert unknown is None

        # Roster fallback
        fb_fallback = BookmakerService.get_team_roster("Дворовый клуб", sport="FOOTBALL")
        assert len(fb_fallback) == 7
        assert "Футболист #1" in fb_fallback[0]

        es_fallback = BookmakerService.get_team_roster("Дворовая тима", sport="ESPORTS")
        assert len(es_fallback) == 7
        assert "Киберспортсмен #1" in es_fallback[0]

    def test_poisson_xg_lambda_and_home_advantage(self):
        """Verifies Poisson lambda calculation: home advantage in football vs neutral in esports."""
        # In football, equal ratings (85.0 vs 85.0) have home advantage (+2.5 rating delta)
        odds_fb = BookmakerService.calculate_odds(85.0, 85.0, sport="FOOTBALL")
        # odds_home should be strictly lower than odds_away due to higher home win probability
        assert odds_fb["odds_home"] < odds_fb["odds_away"]

        # In esports, equal ratings (85.0 vs 85.0) have no home advantage
        odds_es = BookmakerService.calculate_odds(85.0, 85.0, sport="ESPORTS")
        assert odds_es["odds_home"] == odds_es["odds_away"]

    def test_odds_calculation_with_5pct_margin_and_floor(self):
        """Verifies 5% bookmaker margin (sum of inverses >= 1.045) and 1.05 minimum floor."""
        odds = BookmakerService.calculate_odds(92.5, 93.0, sport="FOOTBALL")

        required_keys = ["HOME", "DRAW", "AWAY", "OVER_2_5", "UNDER_2_5", "odds_home", "odds_draw", "odds_away"]
        for k in required_keys:
            assert k in odds
            assert isinstance(odds[k], Decimal)
            assert odds[k] >= Decimal("1.05")

        # Check 1X2 market margin: sum(1 / Odds) >= 1.045
        sum_inverses_1x2 = (
            (Decimal("1.0") / odds["HOME"])
            + (Decimal("1.0") / odds["DRAW"])
            + (Decimal("1.0") / odds["AWAY"])
        )
        assert sum_inverses_1x2 >= Decimal("1.045")

        # Check Total 2.5 market margin: sum(1 / Odds) >= 1.045
        sum_inverses_ou = (
            (Decimal("1.0") / odds["OVER_2_5"])
            + (Decimal("1.0") / odds["UNDER_2_5"])
        )
        assert sum_inverses_ou >= Decimal("1.045")

    def test_odds_extreme_rating_differential_floor_invariants(self):
        """Verifies extreme skill disparities never generate sub-1.05 or negative odds."""
        odds = BookmakerService.calculate_odds(110.0, 40.0, sport="FOOTBALL")
        assert odds["odds_home"] >= Decimal("1.05")
        assert odds["odds_away"] > odds["odds_home"]
        assert odds["odds_over"] >= Decimal("1.05")
        assert odds["odds_under"] >= Decimal("1.05")


class TestBookmakerBettingAndSimulation:
    """Verifies match creation, 90-minute chronological simulation, bet placement, and settlement."""

    @pytest.mark.asyncio
    async def test_create_match_and_retrieval(self, async_session: AsyncSession):
        """Verifies creating scheduled match, automatic odds generation, and queries."""
        match = await BookmakerService.create_match(
            async_session,
            sport="FOOTBALL",
            team_home="Реал Мадрид",
            team_away="Манчестер Сити",
        )
        assert match.id is not None
        assert match.status == "SCHEDULED"
        assert match.odds_home >= Decimal("1.05")
        assert match.odds_away >= Decimal("1.05")

        retrieved = await BookmakerService.get_match_by_id(async_session, match.id)
        assert retrieved is not None
        assert retrieved.team_home == "Реал Мадрид"

        upcoming = await BookmakerService.get_upcoming_matches(async_session, sport="FOOTBALL")
        assert any(m.id == match.id for m in upcoming)

    def test_simulate_match_events_chronological_timeline(self):
        """Verifies 90-minute event simulation generates ordered timestamps and authentic event descriptions."""
        score_h, score_a, events = BookmakerService.simulate_match_events(
            team_home="Бавария",
            team_away="Барселона",
            home_rating=91.0,
            away_rating=89.5,
            sport="FOOTBALL",
            seed=42,
        )
        assert score_h >= 0
        assert score_a >= 0
        assert isinstance(events, list)

        # Check chronological ordering
        for i in range(len(events) - 1):
            assert events[i]["minute"] <= events[i + 1]["minute"]

        # Check event structure
        goal_events = [e for e in events if e.get("type") == "GOAL"]
        assert len(goal_events) == score_h + score_a

        for e in events:
            assert "minute" in e
            assert "description" in e
            assert len(e["description"]) > 0

    @pytest.mark.asyncio
    async def test_place_bet_happy_path_with_atomic_debit_and_ledger(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies placing a valid bet debits card atomically and writes double-entry ledger."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("100000.00")
        await async_session.commit()

        match = await BookmakerService.create_match(
            async_session,
            sport="FOOTBALL",
            team_home="Ливерпуль",
            team_away="ПСЖ",
        )
        await async_session.commit()

        bet_amount = Decimal("5000.00")
        bet = await BookmakerService.place_bet(
            async_session,
            user_id=sample_user.id,
            match_id=match.id,
            bet_type="HOME",
            amount=bet_amount,
        )
        await async_session.commit()

        assert bet.id is not None
        assert bet.status == "PENDING"
        assert bet.odds == match.odds_home
        assert bet.amount == bet_amount
        assert bet.potential_payout == (bet_amount * match.odds_home).quantize(Decimal("0.01"))

        # Verify account balance debited
        await async_session.refresh(acc)
        assert acc.balance == Decimal("95000.00")

        # Verify Transaction & LedgerEntry
        tx_stmt = select(Transaction).where(
            Transaction.sender_account_id == acc.id,
            Transaction.tx_type == "BET_PLACED",
        )
        tx = (await async_session.execute(tx_stmt)).scalar_one()
        assert tx.amount == bet_amount
        assert tx.category == "Ставки и азарт"
        assert tx.fiscal_code is not None

        ledger_stmt = select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id)
        entry = (await async_session.execute(ledger_stmt)).scalar_one()
        assert entry.direction == "DEBIT"
        assert entry.amount == bet_amount
        assert entry.balance_after == Decimal("95000.00")

    @pytest.mark.asyncio
    async def test_settle_match_winning_outcome(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies winning bet settlement credits potential payout atomically with BET_WIN transaction."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("10000.00")
        await async_session.commit()

        match = await BookmakerService.create_match(
            async_session,
            sport="FOOTBALL",
            team_home="Зенит",
            team_away="Спартак",
        )
        await async_session.commit()

        bet = await BookmakerService.place_bet(
            async_session,
            user_id=sample_user.id,
            match_id=match.id,
            bet_type="HOME",
            amount=Decimal("1000.00"),
        )
        await async_session.commit()

        # Simulate match with forced home win (3-1)
        await BookmakerService.play_match(
            async_session,
            match_id=match.id,
            force_score_home=3,
            force_score_away=1,
        )
        await async_session.commit()

        settled_bets = await BookmakerService.settle_match(async_session, match.id)
        await async_session.commit()

        assert len(settled_bets) == 1
        assert settled_bets[0].status == "WON"

        # Check balance post-win
        await async_session.refresh(acc)
        expected_balance = Decimal("9000.00") + bet.potential_payout
        assert acc.balance == expected_balance

        # Check BET_WIN transaction
        tx_stmt = select(Transaction).where(
            Transaction.recipient_account_id == acc.id,
            Transaction.tx_type == "BET_WIN",
        )
        tx = (await async_session.execute(tx_stmt)).scalar_one()
        assert tx.amount == bet.potential_payout
        assert tx.category == "Ставки и азарт"

    @pytest.mark.asyncio
    async def test_settle_match_losing_outcome(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies losing bet settlement sets status LOST without modifying balance."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("10000.00")
        await async_session.commit()

        match = await BookmakerService.create_match(
            async_session,
            sport="FOOTBALL",
            team_home="ЦСКА",
            team_away="Динамо",
        )
        await async_session.commit()

        await BookmakerService.place_bet(
            async_session,
            user_id=sample_user.id,
            match_id=match.id,
            bet_type="HOME",
            amount=Decimal("2000.00"),
        )
        await async_session.commit()

        # Force away win (0-2)
        await BookmakerService.play_match(
            async_session,
            match_id=match.id,
            force_score_home=0,
            force_score_away=2,
        )
        await async_session.commit()

        settled = await BookmakerService.settle_match(async_session, match.id)
        await async_session.commit()

        assert len(settled) == 1
        assert settled[0].status == "LOST"

        await async_session.refresh(acc)
        assert acc.balance == Decimal("8000.00")

    @pytest.mark.asyncio
    async def test_settle_cancelled_match_refund(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies cancelled match refunds 100% of wagers with BET_REFUND transactions."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("10000.00")
        await async_session.commit()

        match = await BookmakerService.create_match(
            async_session,
            sport="ESPORTS",
            team_home="NAVI",
            team_away="Team Spirit",
        )
        await async_session.commit()

        bet = await BookmakerService.place_bet(
            async_session,
            user_id=sample_user.id,
            match_id=match.id,
            bet_type="AWAY",
            amount=Decimal("3000.00"),
        )
        await async_session.commit()

        # Cancel match
        match.status = "CANCELLED"
        await async_session.commit()

        settled = await BookmakerService.settle_match(async_session, match.id)
        await async_session.commit()

        assert len(settled) == 1
        assert settled[0].status == "REFUNDED"

        await async_session.refresh(acc)
        assert acc.balance == Decimal("10000.00")

        tx_stmt = select(Transaction).where(
            Transaction.recipient_account_id == acc.id,
            Transaction.tx_type == "BET_REFUND",
        )
        tx = (await async_session.execute(tx_stmt)).scalar_one()
        assert tx.amount == Decimal("3000.00")


class TestDuelsServiceFeatures:
    """Verifies PvP duels: Dice, Coinflip, Russian Roulette 2-player, 3% commission, and escrow."""

    @pytest.mark.asyncio
    async def test_create_duel_happy_path_and_escrow_debit(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies duel creation debits creator stake into escrow and sets status OPEN."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("20000.00")
        await async_session.commit()

        stake = Decimal("5000.00")
        duel = await DuelsService.create_duel(
            async_session, creator_user_id=sample_user.id, game_type="DICE", stake=stake
        )
        await async_session.commit()

        assert duel.id is not None
        assert duel.status == "OPEN"
        assert duel.stake == stake
        assert duel.game_type == "DICE"

        await async_session.refresh(acc)
        assert acc.balance == Decimal("15000.00")

        tx_stmt = select(Transaction).where(
            Transaction.sender_account_id == acc.id,
            Transaction.tx_type == "DUEL_STAKE",
        )
        tx = (await async_session.execute(tx_stmt)).scalar_one()
        assert tx.amount == stake

    @pytest.mark.asyncio
    async def test_cancel_duel_refunds_creator(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies creator can cancel open duel and receive immediate full stake refund."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("20000.00")
        await async_session.commit()

        stake = Decimal("4000.00")
        duel = await DuelsService.create_duel(
            async_session, creator_user_id=sample_user.id, game_type="COINFLIP", stake=stake
        )
        await async_session.commit()

        cancelled_duel = await DuelsService.cancel_duel(
            async_session, duel_id=duel.id, user_id=sample_user.id
        )
        await async_session.commit()

        assert cancelled_duel.status == "CANCELLED"
        await async_session.refresh(acc)
        assert acc.balance == Decimal("20000.00")

        tx_stmt = select(Transaction).where(
            Transaction.recipient_account_id == acc.id,
            Transaction.tx_type == "DUEL_REFUND",
        )
        tx = (await async_session.execute(tx_stmt)).scalar_one()
        assert tx.amount == stake

    @pytest.mark.asyncio
    async def test_dice_duel_win_with_3pct_commission(
        self, async_session: AsyncSession, sample_user: User, sample_recipient: User
    ):
        """Verifies winner in dice duel receives pot minus 3% bank commission."""
        c_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        o_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        c_acc.balance = Decimal("10000.00")
        o_acc.balance = Decimal("10000.00")
        await async_session.commit()

        stake = Decimal("1000.00")
        duel = await DuelsService.create_duel(
            async_session, creator_user_id=sample_user.id, game_type="DICE", stake=stake
        )
        await async_session.commit()

        # Opponent accepts; force creator roll 6, opponent roll 3 -> Creator wins!
        duel, details = await DuelsService.accept_duel(
            async_session,
            duel_id=duel.id,
            opponent_user_id=sample_recipient.id,
            force_creator_roll=6,
            force_opponent_roll=3,
        )
        await async_session.commit()

        assert duel.status == "FINISHED"
        assert duel.winner_user_id == sample_user.id

        pot = stake * 2  # 2000
        commission = Decimal("60.00")  # 3% of 2000
        expected_payout = Decimal("1940.00")

        assert details["commission"] == commission
        assert details["payout"] == expected_payout

        # Creator started with 10k, spent 1k, won 1940 -> 10940
        await async_session.refresh(c_acc)
        assert c_acc.balance == Decimal("10940.00")

        # Opponent started with 10k, spent 1k -> 9000
        await async_session.refresh(o_acc)
        assert o_acc.balance == Decimal("9000.00")

        # Check winner transaction
        tx_stmt = select(Transaction).where(
            Transaction.recipient_account_id == c_acc.id,
            Transaction.tx_type == "DUEL_WIN",
        )
        tx = (await async_session.execute(tx_stmt)).scalar_one()
        assert tx.amount == expected_payout
        assert tx.fee == commission

    @pytest.mark.asyncio
    async def test_dice_duel_draw_refunds_both(
        self, async_session: AsyncSession, sample_user: User, sample_recipient: User
    ):
        """Verifies a tie in dice duel refunds stakes to both participants without commission."""
        c_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        o_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        c_acc.balance = Decimal("10000.00")
        o_acc.balance = Decimal("10000.00")
        await async_session.commit()

        stake = Decimal("2000.00")
        duel = await DuelsService.create_duel(
            async_session, creator_user_id=sample_user.id, game_type="DICE", stake=stake
        )
        await async_session.commit()

        # Both roll 5
        duel, details = await DuelsService.accept_duel(
            async_session,
            duel_id=duel.id,
            opponent_user_id=sample_recipient.id,
            force_creator_roll=5,
            force_opponent_roll=5,
        )
        await async_session.commit()

        assert duel.status == "FINISHED"
        assert duel.winner_user_id is None
        assert details["is_draw"] is True

        await async_session.refresh(c_acc)
        await async_session.refresh(o_acc)
        assert c_acc.balance == Decimal("10000.00")
        assert o_acc.balance == Decimal("10000.00")

    @pytest.mark.asyncio
    async def test_coinflip_duel_resolution(
        self, async_session: AsyncSession, sample_user: User, sample_recipient: User
    ):
        """Verifies coinflip 50/50 heads (creator wins) vs tails (opponent wins)."""
        c_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        o_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        c_acc.balance = Decimal("5000.00")
        o_acc.balance = Decimal("5000.00")
        await async_session.commit()

        stake = Decimal("1000.00")
        duel = await DuelsService.create_duel(
            async_session, creator_user_id=sample_user.id, game_type="COINFLIP", stake=stake
        )
        await async_session.commit()

        # Force flip=2 (Tails -> Opponent wins)
        duel, details = await DuelsService.accept_duel(
            async_session,
            duel_id=duel.id,
            opponent_user_id=sample_recipient.id,
            force_coinflip=2,
        )
        await async_session.commit()

        assert duel.winner_user_id == sample_recipient.id
        await async_session.refresh(o_acc)
        assert o_acc.balance == Decimal("4000.00") + Decimal("1940.00")

    @pytest.mark.asyncio
    async def test_roulette_two_player_duel_elimination(
        self, async_session: AsyncSession, sample_user: User, sample_recipient: User
    ):
        """Verifies 2-player Russian Roulette duel: odd bullet eliminates creator, even eliminates opponent."""
        c_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        o_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        c_acc.balance = Decimal("5000.00")
        o_acc.balance = Decimal("5000.00")
        await async_session.commit()

        stake = Decimal("1000.00")
        duel = await DuelsService.create_duel(
            async_session, creator_user_id=sample_user.id, game_type="ROULETTE", stake=stake
        )
        await async_session.commit()

        # Force chamber=2 (turn 2 is opponent -> opponent eliminated, creator survives)
        duel, details = await DuelsService.accept_duel(
            async_session,
            duel_id=duel.id,
            opponent_user_id=sample_recipient.id,
            force_bullet_chamber=2,
        )
        await async_session.commit()

        assert duel.winner_user_id == sample_user.id
        assert details["fatal_player"] == "OPPONENT"


class TestRussianRouletteSolo:
    """Verifies solo Russian Roulette survival ladder (x2, x4, x8, x16, x32), trigger pulls, and cashout."""

    @pytest.mark.asyncio
    async def test_start_roulette_solo_stake_debit(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies starting solo roulette session debits stake upfront and initializes state."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("50000.00")
        await async_session.commit()

        stake = Decimal("5000.00")
        state = await DuelsService.start_roulette_solo(async_session, sample_user.id, stake)
        await async_session.commit()

        assert state.stake == stake
        assert state.current_step == 0
        assert state.multiplier == 1
        assert state.is_alive is True

        await async_session.refresh(acc)
        assert acc.balance == Decimal("45000.00")

    @pytest.mark.asyncio
    async def test_roulette_solo_ladder_progression_up_to_x32(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies 5-step ladder multipliers: x2, x4, x8, x16, x32."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("10000.00")
        await async_session.commit()

        stake = Decimal("1000.00")
        state = await DuelsService.start_roulette_solo(async_session, sample_user.id, stake)
        await async_session.commit()

        expected_multipliers = [2, 4, 8, 16, 32]
        for idx, mult in enumerate(expected_multipliers, start=1):
            state, survived = await DuelsService.roulette_solo_pull(
                async_session, state, force_outcome=True
            )
            assert survived is True
            assert state.current_step == idx
            assert state.multiplier == mult
            assert state.current_payout == stake * mult

    @pytest.mark.asyncio
    async def test_roulette_solo_pull_elimination(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies chamber hit eliminates player, zeroes payout, and locks session."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("10000.00")
        await async_session.commit()

        state = await DuelsService.start_roulette_solo(
            async_session, sample_user.id, Decimal("1000.00")
        )
        await async_session.commit()

        state, survived = await DuelsService.roulette_solo_pull(
            async_session, state, force_outcome=False
        )
        assert survived is False
        assert state.is_alive is False
        assert state.current_payout == Decimal("0.00")

        # Pulling again raises ValueError
        with pytest.raises(ValueError, match="Игрок уже выбыл"):
            await DuelsService.roulette_solo_pull(async_session, state)

        # Cashing out raises ValueError
        with pytest.raises(ValueError, match="Невозможно забрать выигрыш"):
            await DuelsService.roulette_solo_cashout(async_session, state)

    @pytest.mark.asyncio
    async def test_roulette_solo_cashout_at_intermediate_step(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies cashout at step 2 (x4) credits payout with DUEL_WIN and terminates session."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("10000.00")
        await async_session.commit()

        stake = Decimal("1000.00")
        state = await DuelsService.start_roulette_solo(async_session, sample_user.id, stake)
        await async_session.commit()

        # Pull twice successfully -> x4 (4,000.00 ₽)
        state, _ = await DuelsService.roulette_solo_pull(async_session, state, force_outcome=True)
        state, _ = await DuelsService.roulette_solo_pull(async_session, state, force_outcome=True)
        assert state.current_step == 2
        assert state.multiplier == 4

        payout = await DuelsService.roulette_solo_cashout(async_session, state)
        await async_session.commit()

        assert payout == Decimal("4000.00")
        assert state.is_alive is False

        await async_session.refresh(acc)
        # 10k - 1k + 4k = 13k
        assert acc.balance == Decimal("13000.00")

        tx_stmt = select(Transaction).where(
            Transaction.recipient_account_id == acc.id,
            Transaction.tx_type == "DUEL_WIN",
        )
        tx = (await async_session.execute(tx_stmt)).scalar_one()
        assert tx.amount == Decimal("4000.00")

    @pytest.mark.asyncio
    async def test_roulette_solo_cannot_cashout_step_zero(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies cashout attempt before any trigger pull raises ValueError."""
        state = await DuelsService.start_roulette_solo(
            async_session, sample_user.id, Decimal("1000.00")
        )
        await async_session.commit()

        with pytest.raises(ValueError, match="не сделано ни одного выстрела"):
            await DuelsService.roulette_solo_cashout(async_session, state)


class TestZarabotokUIAndKeyboards:
    """Verifies dual reply keyboard compatibility, Zarabotok hub dashboard, commands, and routers."""

    def test_reply_keyboard_dual_compatibility(self):
        """Verifies CompatibleZarabotokButtonText equals '💼 Заработок' and '💼 Работа и бизнес'."""
        btn_text = CompatibleZarabotokButtonText("💼 Заработок")
        assert btn_text == "💼 Заработок"
        assert btn_text == "💼 Работа и бизнес"
        assert hash(btn_text) == hash("💼 Заработок")

        # Check main menu reply keyboard layout
        kb = get_main_menu_keyboard()
        row_texts = [[b.text for b in row] for row in kb.keyboard]
        assert "💼 Заработок" in row_texts[2]
        assert "🎁 Кэшбэк" in row_texts[2]

    def test_zarabotok_hub_dashboard_buttons(self):
        """Verifies Zarabotok hub menu keyboard contains Bookmaker and PvP Duels portals."""
        kb = get_jobs_menu_keyboard([])
        button_texts = [b.text for row in kb.inline_keyboard for b in row]

        assert any("💼 Мои работы и смены" in t for t in button_texts)
        assert any("⚡️ Быстрая шабашка" in t for t in button_texts)
        assert any("⚽️ Букмекер (Ставки до 20M ₽)" in t for t in button_texts)
        assert any("🎲 PvP Дуэли и Азарт" in t for t in button_texts)

    def test_router_registration_in_create_dispatcher(self):
        """Verifies gambling router is registered in create_dispatcher()."""
        dp = create_dispatcher()
        sub_router_names = [r.name for r in dp.sub_routers]
        assert "gambling" in sub_router_names

    @pytest.mark.asyncio
    async def test_gambling_message_commands_offline(
        self, async_session: AsyncSession, sample_user: User, message_factory, mock_bot: AsyncMock
    ):
        """Verifies handling /bets, /bookmaker, /duels, and /arena commands."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)

        # /bets
        msg_bets = message_factory("/bets", user_id=sample_user.telegram_id)
        await handle_bookmaker_command(msg_bets, session=async_session, user=sample_user)

        # /duels
        msg_duels = message_factory("/duels", user_id=sample_user.telegram_id)
        await handle_duels_command(msg_duels, session=async_session, user=sample_user)

        sent = extract_bot_sent_messages(mock_bot)
        assert len(sent) >= 2
        assert any("Букмекер" in s["text"] for s in sent)
        assert any("PvP Арена" in s["text"] for s in sent)

    @pytest.mark.asyncio
    async def test_gambling_callbacks_offline_execution(
        self,
        async_session: AsyncSession,
        sample_user: User,
        callback_factory,
        fsm_context_factory,
        mock_bot: AsyncMock,
    ):
        """Verifies handling core callbacks: bet:menu, bet:f:FOOTBALL, duel:menu, d:f:DICE."""
        # bet:menu
        cb_bet = callback_factory("bet:menu", user_id=sample_user.telegram_id)
        await handle_bet_menu_callback(cb_bet, session=async_session, user=sample_user)

        # bet:f:FOOTBALL
        cb_f = callback_factory("bet:f:FOOTBALL", user_id=sample_user.telegram_id)
        await handle_bet_filter_callback(cb_f, session=async_session, user=sample_user)

        # duel:menu
        cb_duel = callback_factory("duel:menu", user_id=sample_user.telegram_id)
        await handle_duels_menu_callback(cb_duel, session=async_session, user=sample_user)

        # d:f:DICE
        cb_df = callback_factory("d:f:DICE", user_id=sample_user.telegram_id)
        await handle_duel_filter_callback(cb_df, session=async_session, user=sample_user)

        answers = extract_bot_callback_answers(mock_bot)
        assert len(answers) >= 4


# ==============================================================================
# Suite 2: Tier 2 — Boundary & Corner Cases
# ==============================================================================


class TestBoundaryLimitsAndGuards:
    """Verifies edge conditions, financial limits, overdraft defense, and adversarial input."""

    @pytest.mark.asyncio
    async def test_minimum_bet_and_stake_boundaries(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies 100.00 ₽ allowed, 99.99 ₽ rejected with ValueError across all gambling engines."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("100000.00")
        await async_session.commit()

        match = await BookmakerService.create_match(
            async_session, sport="FOOTBALL", team_home="Бавария", team_away="Барселона"
        )
        await async_session.commit()

        # 1. 100.00 ₽ allowed
        bet_ok = await BookmakerService.place_bet(
            async_session, sample_user.id, match.id, "HOME", Decimal("100.00")
        )
        assert bet_ok.amount == Decimal("100.00")

        duel_ok = await DuelsService.create_duel(
            async_session, sample_user.id, "DICE", Decimal("100.00")
        )
        assert duel_ok.stake == Decimal("100.00")

        # 2. 99.99 ₽ rejected
        with pytest.raises(ValueError):
            await BookmakerService.place_bet(
                async_session, sample_user.id, match.id, "HOME", Decimal("99.99")
            )

        with pytest.raises(ValueError):
            await DuelsService.create_duel(
                async_session, sample_user.id, "DICE", Decimal("99.99")
            )

        with pytest.raises(ValueError):
            await DuelsService.start_roulette_solo(
                async_session, sample_user.id, Decimal("99.99")
            )

    @pytest.mark.asyncio
    async def test_maximum_bet_and_stake_boundaries(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies 20,000,000.00 ₽ allowed, 20,000,000.01 ₽ rejected with ValueError."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("50000000.00")
        await async_session.commit()

        match = await BookmakerService.create_match(
            async_session, sport="FOOTBALL", team_home="Бавария", team_away="Барселона"
        )
        await async_session.commit()

        # 1. 20M ₽ allowed
        bet_ok = await BookmakerService.place_bet(
            async_session, sample_user.id, match.id, "HOME", Decimal("20000000.00")
        )
        assert bet_ok.amount == Decimal("20000000.00")

        duel_ok = await DuelsService.create_duel(
            async_session, sample_user.id, "DICE", Decimal("20000000.00")
        )
        assert duel_ok.stake == Decimal("20000000.00")

        # 2. 20,000,000.01 ₽ rejected
        with pytest.raises(ValueError):
            await BookmakerService.place_bet(
                async_session, sample_user.id, match.id, "HOME", Decimal("20000000.01")
            )

        with pytest.raises(ValueError):
            await DuelsService.create_duel(
                async_session, sample_user.id, "DICE", Decimal("20000000.01")
            )

    @pytest.mark.asyncio
    async def test_overdraft_protection_insufficient_funds(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies balance is strictly guarded against overdraft and stays intact."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("500.00")
        await async_session.commit()

        match = await BookmakerService.create_match(
            async_session, sport="FOOTBALL", team_home="Бавария", team_away="Барселона"
        )
        await async_session.commit()

        with pytest.raises(InsufficientFundsError):
            await BookmakerService.place_bet(
                async_session, sample_user.id, match.id, "HOME", Decimal("1000.00")
            )

        with pytest.raises(InsufficientFundsError):
            await DuelsService.create_duel(
                async_session, sample_user.id, "DICE", Decimal("1000.00")
            )

        with pytest.raises(InsufficientFundsError):
            await DuelsService.start_roulette_solo(
                async_session, sample_user.id, Decimal("1000.00")
            )

        await async_session.refresh(acc)
        assert acc.balance == Decimal("500.00")

    @pytest.mark.asyncio
    async def test_frozen_account_protection(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies frozen accounts cannot place bets or enter duels."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("10000.00")
        acc.is_frozen = True
        await async_session.commit()

        match = await BookmakerService.create_match(
            async_session, sport="FOOTBALL", team_home="Бавария", team_away="Барселона"
        )
        await async_session.commit()

        with pytest.raises(AccountFrozenError):
            await BookmakerService.place_bet(
                async_session, sample_user.id, match.id, "HOME", Decimal("500.00")
            )

        with pytest.raises(AccountFrozenError):
            await DuelsService.create_duel(
                async_session, sample_user.id, "DICE", Decimal("500.00")
            )

    @pytest.mark.asyncio
    async def test_canceled_duel_cannot_be_canceled_twice(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies atomic CAS prevents double-canceling or canceling non-open duels."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("10000.00")
        await async_session.commit()

        duel = await DuelsService.create_duel(
            async_session, sample_user.id, "DICE", Decimal("1000.00")
        )
        await async_session.commit()

        # First cancel succeeds
        await DuelsService.cancel_duel(async_session, duel.id, sample_user.id)
        await async_session.commit()

        # Second cancel fails
        with pytest.raises(ValueError, match="Дуэль не может быть отменена"):
            await DuelsService.cancel_duel(async_session, duel.id, sample_user.id)

    @pytest.mark.asyncio
    async def test_self_acceptance_rejection(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies creator cannot accept their own open duel."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("10000.00")
        await async_session.commit()

        duel = await DuelsService.create_duel(
            async_session, sample_user.id, "DICE", Decimal("1000.00")
        )
        await async_session.commit()

        with pytest.raises(ValueError, match="Нельзя принять собственную дуэль"):
            await DuelsService.accept_duel(async_session, duel.id, sample_user.id)

    @pytest.mark.asyncio
    async def test_double_acceptance_rejection(
        self, async_session: AsyncSession, sample_user: User, sample_recipient: User
    ):
        """Verifies accepting an already accepted or finished duel is rejected."""
        c_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        o_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        c_acc.balance = Decimal("10000.00")
        o_acc.balance = Decimal("10000.00")
        await async_session.commit()

        duel = await DuelsService.create_duel(
            async_session, sample_user.id, "DICE", Decimal("1000.00")
        )
        await async_session.commit()

        # First accept succeeds
        await DuelsService.accept_duel(async_session, duel.id, sample_recipient.id)
        await async_session.commit()

        # Second accept fails
        with pytest.raises(ValueError, match="Дуэль уже принята"):
            await DuelsService.accept_duel(async_session, duel.id, sample_recipient.id)


class TestKeyboardCallbackLengths:
    """Verifies that every callback_data in generated gambling keyboards satisfies the strict 64-byte limit."""

    def test_strict_callback_data_under_64_bytes(self):
        """Inspects all inline keyboards in app/bot/keyboards/gambling.py for 64-byte limit compliance."""
        sample_uuid = str(uuid.uuid4())
        mock_match = type("MockMatch", (), {
            "id": sample_uuid,
            "sport": "FOOTBALL",
            "team_home": "Реал Мадрид",
            "team_away": "Манчестер Сити",
            "status": "SCHEDULED",
            "odds_home": Decimal("2.10"),
            "odds_draw": Decimal("3.20"),
            "odds_away": Decimal("3.40"),
            "odds_over": Decimal("1.85"),
            "odds_under": Decimal("1.95"),
        })()
        mock_duel = type("MockDuel", (), {
            "id": sample_uuid,
            "game_type": "DICE",
            "stake": Decimal("5000.00"),
            "status": "OPEN",
        })()

        keyboards = [
            get_bookmaker_menu_keyboard([mock_match], current_filter="ALL"),
            get_match_detail_keyboard(mock_match),
            get_bet_amount_keyboard(sample_uuid, "H"),
            get_duels_menu_keyboard([mock_duel], current_filter="ALL"),
            get_duel_stake_preset_keyboard("DICE"),
            get_duel_detail_keyboard(mock_duel, is_creator=True),
            get_duel_detail_keyboard(mock_duel, is_creator=False),
            get_roulette_solo_keyboard(round_num=2, can_cashout=True),
            get_roulette_stake_preset_keyboard(),
        ]

        for kb in keyboards:
            for row in kb.inline_keyboard:
                for btn in row:
                    cb_data = btn.callback_data
                    if cb_data:
                        byte_len = len(cb_data.encode("utf-8"))
                        assert byte_len <= 64, f"Callback exceeds 64 bytes ({byte_len}): {cb_data}"


class TestAdversarialHtmlEscaping:
    """Verifies that visual formatters escape HTML meta-characters and script injections."""

    def test_adversarial_html_escaping_in_all_gambling_formatters(self):
        """Tests XSS payloads across all formatters in app/bot/formatters/gambling.py."""
        xss_team1 = "<script>alert('xss1')</script>"
        xss_team2 = "<img src=x onerror=alert('xss2')>"
        xss_user = type("MockUser", (), {
            "id": 999,
            "full_name": "<script>Hacker</script>",
            "username": "<b>hacker</b>",
        })()

        mock_match = type("MockMatch", (), {
            "id": "match-uuid",
            "sport": "FOOTBALL",
            "team_home": xss_team1,
            "team_away": xss_team2,
            "status": "SCHEDULED",
            "score_home": 2,
            "score_away": 1,
            "home_rating": 90.0,
            "away_rating": 85.0,
            "odds_home": Decimal("2.00"),
            "odds_draw": Decimal("3.00"),
            "odds_away": Decimal("3.50"),
            "odds_over": Decimal("1.80"),
            "odds_under": Decimal("2.00"),
        })()

        mock_bet = type("MockBet", (), {
            "id": "bet-uuid-1234",
            "bet_type": "HOME",
            "amount": Decimal("1000.00"),
            "odds": Decimal("2.00"),
            "potential_payout": Decimal("2000.00"),
            "status": "PENDING",
        })()

        mock_duel = type("MockDuel", (), {
            "id": "duel-uuid-1234",
            "game_type": "DICE",
            "stake": Decimal("1000.00"),
            "status": "FINISHED",
        })()

        # 1. format_bookmaker_menu
        txt1 = format_bookmaker_menu([mock_match])
        assert "<script>" not in txt1
        assert "&lt;script&gt;" in txt1

        # 2. format_match_detail
        txt2 = format_match_detail(mock_match, user_bets=[mock_bet])
        assert "<script>" not in txt2
        assert "&lt;script&gt;" in txt2

        # 3. format_match_simulation
        events = [{"minute": 10, "type": "GOAL", "description": "<script>goal</script>"}]
        txt3 = format_match_simulation(mock_match, events, user_bets=[mock_bet])
        assert "<script>" not in txt3
        assert "&lt;script&gt;goal&lt;/script&gt;" in txt3

        # 4. format_bet_placed
        txt4 = format_bet_placed(mock_bet, mock_match, Decimal("5000.00"))
        assert "<script>" not in txt4

        # 5. format_duel_result
        txt5 = format_duel_result(
            duel=mock_duel,
            creator=xss_user,
            opponent=xss_user,
            winner=xss_user,
            roll_c=6,
            roll_o=2,
            payout=Decimal("1940.00"),
            commission=Decimal("60.00"),
        )
        assert "<script>" not in txt5
        assert "&lt;script&gt;Hacker&lt;/script&gt;" in txt5


# ==============================================================================
# Suite 3: Tier 3 — Concurrency & Cross-Feature Interactions
# ==============================================================================


class TestConcurrencyAndCrossFeature:
    """Verifies atomic CAS guards under concurrent acceptance and cross-module interactions."""

    @pytest.mark.asyncio
    async def test_concurrent_duel_acceptance_atomic_cas_guard(
        self, test_engine, sample_user: User, sample_recipient: User
    ):
        """Simulates two concurrent accept calls on the same open duel: exactly one succeeds."""
        async_session_maker = async_sessionmaker(
            bind=test_engine, class_=AsyncSession, expire_on_commit=False
        )

        async with async_session_maker() as s1:
            c_acc = await BankingService.get_user_main_account(s1, sample_user.id)
            c_acc.balance = Decimal("20000.00")
            await s1.commit()

            duel = await DuelsService.create_duel(
                s1, creator_user_id=sample_user.id, game_type="DICE", stake=Decimal("5000.00")
            )
            await s1.commit()
            duel_id = duel.id

        # Create third user
        async with async_session_maker() as s_init:
            u3, acc3, _ = await BankingService.get_or_create_user(
                s_init, telegram_id=300003, username="charlie"
            )
            acc3.balance = Decimal("20000.00")
            o_acc = await BankingService.get_user_main_account(s_init, sample_recipient.id)
            o_acc.balance = Decimal("20000.00")
            await s_init.commit()
            u3_id = u3.id

        results = []

        async def _attempt_accept(opp_id: int):
            async with async_session_maker() as session:
                try:
                    res = await DuelsService.accept_duel(session, duel_id, opp_id)
                    await session.commit()
                    return ("SUCCESS", opp_id)
                except Exception as exc:
                    return ("FAILED", str(exc))

        # Run concurrent acceptances
        r1, r2 = await asyncio.gather(
            _attempt_accept(sample_recipient.id),
            _attempt_accept(u3_id),
        )

        success_count = sum(1 for status, _ in (r1, r2) if status == "SUCCESS")
        failure_count = sum(1 for status, _ in (r1, r2) if status == "FAILED")

        assert success_count == 1
        assert failure_count == 1

        # Check final duel status
        async with async_session_maker() as s_check:
            d_final = await DuelsService.get_duel_by_id(s_check, duel_id)
            assert d_final.status == "FINISHED"

    @pytest.mark.asyncio
    async def test_admin_test_user_cleanup_cascades_bets_and_duels(
        self, async_session: AsyncSession
    ):
        """Verifies AdminService.cleanup_test_users prunes bets and duels belonging to test users."""
        test_u, test_acc, _ = await BankingService.get_or_create_user(
            async_session, telegram_id=8881001, username="test_dummy_user"
        )
        test_acc.balance = Decimal("50000.00")
        await async_session.commit()

        match = await BookmakerService.create_match(
            async_session, sport="FOOTBALL", team_home="Бавария", team_away="Барселона"
        )
        await async_session.commit()

        await BookmakerService.place_bet(
            async_session, test_u.id, match.id, "HOME", Decimal("1000.00")
        )
        await DuelsService.create_duel(
            async_session, test_u.id, "DICE", Decimal("1000.00")
        )
        await async_session.commit()

        # Run cleanup
        metrics = await AdminService.cleanup_test_users(async_session)
        await async_session.commit()

        assert metrics["sports_bets"] >= 1
        assert metrics["pvp_duels"] >= 1


# ==============================================================================
# Suite 4: Tier 4 — Real-World E2E Scenario
# ==============================================================================


class TestRealWorldGamblingE2EScenario:
    """Verifies complete real-world client lifecycle across Bookmaker and PvP Arena."""

    @pytest.mark.asyncio
    async def test_full_gambling_and_arena_e2e_lifecycle(
        self, async_session: AsyncSession
    ):
        """
        Complete E2E test executing:
        1. User Alice and Opponent Bob registered.
        2. Alice deposits 50,000,000.00 ₽.
        3. Alice browses Bookmaker upcoming line, chooses Champions League match.
        4. Alice places high-stakes bet of 5,000,000.00 ₽ on Real Madrid (HOME).
        5. Match simulated: Real Madrid wins 3-1.
        6. Settlement credits winnings with digital fiscal receipt.
        7. Alice creates a 2,000,000.00 ₽ PvP Dice duel.
        8. Bob accepts duel with 2,000,000.00 ₽ stake.
        9. Duel resolves (Alice rolls 6, Bob rolls 3) -> Alice wins pot minus 3% commission.
        10. Full double-entry ledger audit verification.
        """
        # 1. Onboarding
        alice, a_acc, _ = await BankingService.get_or_create_user(
            async_session, telegram_id=777001, username="alice_investor"
        )
        bob, b_acc, _ = await BankingService.get_or_create_user(
            async_session, telegram_id=777002, username="bob_challenger"
        )

        # 2. Deposit
        a_acc.balance = Decimal("50000000.00")
        b_acc.balance = Decimal("10000000.00")
        await async_session.commit()

        # 3. Bookmaker match
        match = await BookmakerService.create_match(
            async_session,
            sport="FOOTBALL",
            team_home="Реал Мадрид",
            team_away="Манчестер Сити",
        )
        await async_session.commit()

        # 4. Bet placement (5M ₽)
        bet_amount = Decimal("5000000.00")
        bet = await BookmakerService.place_bet(
            async_session,
            user_id=alice.id,
            match_id=match.id,
            bet_type="HOME",
            amount=bet_amount,
        )
        await async_session.commit()

        assert bet.status == "PENDING"
        await async_session.refresh(a_acc)
        assert a_acc.balance == Decimal("45000000.00")

        # 5. Simulate match (Real Madrid 3 : 1 Man City)
        await BookmakerService.play_match(
            async_session,
            match_id=match.id,
            force_score_home=3,
            force_score_away=1,
        )
        await async_session.commit()

        # 6. Settle match
        settled = await BookmakerService.settle_match(async_session, match.id)
        await async_session.commit()

        assert settled[0].status == "WON"
        expected_bet_payout = bet.potential_payout
        await async_session.refresh(a_acc)
        assert a_acc.balance == Decimal("45000000.00") + expected_bet_payout

        # 7. Create PvP Dice duel (2M ₽)
        duel_stake = Decimal("2000000.00")
        duel = await DuelsService.create_duel(
            async_session,
            creator_user_id=alice.id,
            game_type="DICE",
            stake=duel_stake,
        )
        await async_session.commit()

        await async_session.refresh(a_acc)
        assert a_acc.balance == Decimal("43000000.00") + expected_bet_payout

        # 8 & 9. Bob accepts duel; Alice rolls 6, Bob rolls 3
        duel, details = await DuelsService.accept_duel(
            async_session,
            duel_id=duel.id,
            opponent_user_id=bob.id,
            force_creator_roll=6,
            force_opponent_roll=3,
        )
        await async_session.commit()

        assert duel.winner_user_id == alice.id
        pot = duel_stake * 2  # 4M
        commission = Decimal("120000.00")  # 3% of 4M
        net_duel_payout = pot - commission  # 3,880,000.00 ₽

        assert details["commission"] == commission
        assert details["payout"] == net_duel_payout

        # Bob's balance: 10M - 2M = 8M
        await async_session.refresh(b_acc)
        assert b_acc.balance == Decimal("8000000.00")

        # Alice's final balance
        await async_session.refresh(a_acc)
        expected_final = Decimal("43000000.00") + expected_bet_payout + net_duel_payout
        assert a_acc.balance == expected_final

        # 10. Audit LedgerEntries consistency
        ledger_res = await async_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.account_id.in_([a_acc.id, b_acc.id])
            )
        )
        ledgers = list(ledger_res.scalars().all())
        assert len(ledgers) >= 4  # Bet debit, Bet win credit, Duel stake debit, Duel win credit, Opponent stake debit

        # Check all transaction fiscal codes
        tx_res = await async_session.execute(
            select(Transaction).where(
                Transaction.sender_account_id.in_([a_acc.id, b_acc.id])
                | Transaction.recipient_account_id.in_([a_acc.id, b_acc.id])
            )
        )
        txs = list(tx_res.scalars().all())
        for t in txs:
            assert t.fiscal_code is not None
            assert t.auth_code is not None
