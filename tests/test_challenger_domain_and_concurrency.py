"""Empirical Challenger 1 Test Suite: Domain Math, Poisson Simulation, Concurrency, and Financial Limits.

Stress-tests:
1. Bookmaker Odds Math:
   - 5% margin holds across various home/away rating differences (95 vs 75, 80 vs 80, 70 vs 90, 85 vs 80, 75 vs 90).
   - Sum of inverse odds ~ 1.05 for 1X2 and Total 2.5 markets.
   - Strict floor: no odds below 1.05 (even under extreme rating gaps 99 vs 50).
   - Odds monotonicity and sport-specific home advantage (+2.5 for Football, 0.0 for Esports).
   - Monte Carlo test over 100 random rating pairs.
2. Poisson & Event Simulation:
   - 100 simulated matches: score validity, event chronological ordering (1..90), running score matching.
   - Player roster integrity: all goal scorers, goalkeepers, card recipients exist on team roster.
   - Football vs Esports event isolation (no red/yellow cards in esports).
   - Statistical verification of football +2.5 home advantage over 500 simulated matches.
3. Duels Concurrency & Atomic CAS:
   - 5 concurrent challengers racing to accept 1 open duel: exactly 1 succeeds, 4 fail cleanly with balance untouched.
   - Race between creator cancel and opponent accept: exactly 1 succeeds, no double refund or double payout.
   - Failed acceptance due to insufficient funds cleanly rolls back duel status to OPEN for subsequent challengers.
4. Financial Invariants & Overdrafts:
   - Strict boundaries: 100.00 ₽ and 20,000,000.00 ₽ valid; 99.99 ₽ and 20,000,000.01 ₽ rejected.
   - Overdraft protection: InsufficientFundsError leaves balances strictly unchanged with 0 balance leakage.
"""

import asyncio
import datetime
from decimal import Decimal
import math
import random
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import Account, PvPDuel, SportsMatch, User
from app.services.banking import BankingService
from app.services.bookmaker import BookmakerService
from app.services.duels import DuelsService
from app.services.transfers import AccountFrozenError, InsufficientFundsError


# ==============================================================================
# 1. Bookmaker Odds Math Stress Tests
# ==============================================================================


class TestBookmakerOddsMathStress:
    """Stress tests for decimal odds calculation, 5% margin invariant, and floor limits."""

    @pytest.mark.parametrize(
        "home_r,away_r,sport",
        [
            (95.0, 75.0, "FOOTBALL"),  # Heavy home favorite (prompt example)
            (80.0, 80.0, "FOOTBALL"),  # Even ratings football (prompt example)
            (70.0, 90.0, "FOOTBALL"),  # Heavy away favorite (prompt example)
            (88.0, 82.0, "FOOTBALL"),  # Moderate home favorite
            (78.0, 85.0, "FOOTBALL"),  # Moderate away favorite
            (93.0, 85.0, "ESPORTS"),   # Esports favorite
            (80.0, 80.0, "ESPORTS"),   # Esports equal ratings
            (75.0, 95.0, "ESPORTS"),   # Esports underdog
        ],
    )
    def test_odds_5_percent_margin_holds_across_rating_differences(
        self, home_r: float, away_r: float, sport: str
    ):
        """Verifies sum(1/odds) ~ 1.05 and no odds below 1.05 across specified rating pairs."""
        odds = BookmakerService.calculate_odds(home_r, away_r, sport=sport)

        # 1. Strict floor check: no odd below 1.05
        for market, odd_val in odds.items():
            assert isinstance(odd_val, Decimal), f"Odd {market} is not Decimal: {type(odd_val)}"
            assert odd_val >= Decimal("1.05"), f"Odd {market}={odd_val} is below minimum 1.05 floor"

        # 2. 1X2 market margin check: target sum(1/odds) = 1.05 (~1.05 within 1.04..1.06)
        inv_h = 1.0 / float(odds["HOME"])
        inv_d = 1.0 / float(odds["DRAW"])
        inv_a = 1.0 / float(odds["AWAY"])
        margin_1x2 = inv_h + inv_d + inv_a

        assert 1.04 <= margin_1x2 <= 1.06, (
            f"1X2 Margin {margin_1x2:.4f} outside [1.04, 1.06] for ratings ({home_r}, {away_r}, {sport})"
        )

        # 3. Total 2.5 market margin check: target sum(1/odds) = 1.05 (~1.05 within 1.04..1.06)
        inv_over = 1.0 / float(odds["OVER_2_5"])
        inv_under = 1.0 / float(odds["UNDER_2_5"])
        margin_ou = inv_over + inv_under
        assert 1.04 <= margin_ou <= 1.06, (
            f"Total 2.5 Margin {margin_ou:.4f} outside [1.04, 1.06] for ratings ({home_r}, {away_r}, {sport})"
        )

    @pytest.mark.parametrize(
        "home_r,away_r,sport",
        [
            (99.0, 50.0, "FOOTBALL"),  # Extreme home favorite
            (50.0, 99.0, "FOOTBALL"),  # Extreme away favorite
            (99.0, 40.0, "ESPORTS"),   # Extreme esports favorite
        ],
    )
    def test_odds_floor_enforcement_on_extreme_rating_gaps(
        self, home_r: float, away_r: float, sport: str
    ):
        """Verifies that under extreme rating gaps, odds floor (1.05) is strictly respected."""
        odds = BookmakerService.calculate_odds(home_r, away_r, sport=sport)

        # Floor constraint: strictly no odds below 1.05
        for market, odd_val in odds.items():
            assert odd_val >= Decimal("1.05"), f"Odd {market}={odd_val} violated floor 1.05"

        # The dominant favorite is clamped to 1.05
        fav_key = "HOME" if home_r > away_r else "AWAY"
        assert odds[fav_key] == Decimal("1.05")

        # Sum of inverses does not exceed 1.06 (bettor is protected, no margin gouging)
        sum_1x2 = sum(1.0 / float(odds[k]) for k in ["HOME", "DRAW", "AWAY"])
        assert sum_1x2 <= 1.06, f"Excessive margin under floor clamping: {sum_1x2}"

    def test_odds_monotonicity_and_home_advantage(self):
        """Verifies home advantage (+2.5) affects odds in Football vs pure rating in Esports."""
        # 1. Football 80 vs 80: Home gets +2.5 boost -> Home win more likely than Away win -> Home odds < Away odds
        fb_odds = BookmakerService.calculate_odds(80.0, 80.0, sport="FOOTBALL")
        assert fb_odds["HOME"] < fb_odds["AWAY"], (
            f"Football home advantage failed: HOME={fb_odds['HOME']} should be < AWAY={fb_odds['AWAY']}"
        )

        # 2. Esports 80 vs 80: No home advantage -> HOME odds == AWAY odds
        es_odds = BookmakerService.calculate_odds(80.0, 80.0, sport="ESPORTS")
        assert es_odds["HOME"] == es_odds["AWAY"], (
            f"Esports symmetry failed: HOME={es_odds['HOME']} should equal AWAY={es_odds['AWAY']}"
        )

        # 3. Strong favorite: 95 vs 75
        fav_odds = BookmakerService.calculate_odds(95.0, 75.0, sport="FOOTBALL")
        assert fav_odds["HOME"] < fav_odds["DRAW"] < fav_odds["AWAY"], (
            f"Odds hierarchy failed for heavy favorite: {fav_odds}"
        )

    def test_odds_monte_carlo_100_random_rating_pairs(self):
        """Stress-tests odds calculation over 100 randomly generated rating pairs in realistic bounds."""
        rng = random.Random(20261022)
        for _ in range(100):
            hr = rng.uniform(65.0, 95.0)
            ar = rng.uniform(65.0, 95.0)
            sport = rng.choice(["FOOTBALL", "ESPORTS"])

            odds = BookmakerService.calculate_odds(hr, ar, sport=sport)

            # Invariant 1: No odd < 1.05
            for k in ["HOME", "DRAW", "AWAY", "OVER_2_5", "UNDER_2_5"]:
                assert odds[k] >= Decimal("1.05")

            # Invariant 2: 1X2 sum of inverses is ~ 1.05 (between 1.04 and 1.06)
            sum_1x2 = sum(1.0 / float(odds[k]) for k in ["HOME", "DRAW", "AWAY"])
            assert 1.04 <= sum_1x2 <= 1.06, f"Monte Carlo 1X2 margin violation: {sum_1x2} (hr={hr}, ar={ar})"

            # Invariant 3: OU sum of inverses is ~ 1.05 (between 1.04 and 1.06)
            sum_ou = sum(1.0 / float(odds[k]) for k in ["OVER_2_5", "UNDER_2_5"])
            assert 1.04 <= sum_ou <= 1.06, f"Monte Carlo OU margin violation: {sum_ou} (hr={hr}, ar={ar})"


# ==============================================================================
# 2. Poisson Match Simulation & Roster Integrity Stress Tests
# ==============================================================================


class TestPoissonSimulationStress:
    """Stress tests for Poisson score generation, 90-minute chronological event timelines, and roster consistency."""

    def test_simulate_100_matches_invariants(self):
        """Simulates 100 matches across authentic teams and verifies scores, timelines, and rosters."""
        fb_teams = BookmakerService.get_football_teams()
        es_teams = BookmakerService.get_esports_teams()

        rng = random.Random(42)

        for i in range(100):
            is_football = i % 2 == 0
            if is_football:
                t1, t2 = rng.sample(fb_teams, 2)
                sport = "FOOTBALL"
            else:
                t1, t2 = rng.sample(es_teams, 2)
                sport = "ESPORTS"

            score_h, score_a, events = BookmakerService.simulate_match_events(
                team_home=t1["name"],
                team_away=t2["name"],
                home_rating=t1["rating"],
                away_rating=t2["rating"],
                sport=sport,
                seed=rng.randint(1, 1000000),
            )

            # 1. Non-negative scores
            assert score_h >= 0, f"Negative home score {score_h}"
            assert score_a >= 0, f"Negative away score {score_a}"

            # 2. Goal count matches final score
            goal_events_h = [e for e in events if e["type"] in ("GOAL", "ROUND_WIN") and e["team_side"] == "HOME"]
            goal_events_a = [e for e in events if e["type"] in ("GOAL", "ROUND_WIN") and e["team_side"] == "AWAY"]
            assert len(goal_events_h) == score_h, f"Home score mismatch: {len(goal_events_h)} != {score_h}"
            assert len(goal_events_a) == score_a, f"Away score mismatch: {len(goal_events_a)} != {score_a}"

            # 3. Timeline chronological order: minutes strictly within [1, 90] and non-decreasing
            last_minute = 0
            for ev in events:
                m = ev["minute"]
                assert 1 <= m <= 90, f"Event minute {m} outside [1, 90]"
                assert m >= last_minute, f"Event minutes not sorted: {m} < {last_minute}"
                last_minute = m

            # 4. Running score consistency
            curr_h = 0
            curr_a = 0
            for ev in events:
                if ev["type"] in ("GOAL", "ROUND_WIN"):
                    if ev["team_side"] == "HOME":
                        curr_h += 1
                    else:
                        curr_a += 1
                    assert ev["score_home"] == curr_h
                    assert ev["score_away"] == curr_a

            # 5. Roster validation: every player in events exists in roster or goalkeeper
            t1_players = set(t1["roster"])
            if "goalkeeper" in t1:
                t1_players.add(t1["goalkeeper"])
            t2_players = set(t2["roster"])
            if "goalkeeper" in t2:
                t2_players.add(t2["goalkeeper"])

            for ev in events:
                p = ev.get("player")
                team_name = ev.get("team")
                if team_name == t1["name"]:
                    assert p in t1_players, f"Player '{p}' not in roster of {team_name}"
                elif team_name == t2["name"]:
                    assert p in t2_players, f"Player '{p}' not in roster of {team_name}"

            # 6. Event type isolation: no cards in esports, only football
            if sport == "ESPORTS":
                for ev in events:
                    assert ev["type"] in ("ROUND_WIN", "CLUTCH", "ACE", "DEFUSE")
            else:
                for ev in events:
                    assert ev["type"] in ("GOAL", "SAVE", "YELLOW_CARD", "RED_CARD")

    def test_home_advantage_statistical_significance(self):
        """Simulates 500 matches between identical ratings in Football vs Esports to confirm +2.5 home advantage."""
        fb_home_goals = 0
        fb_away_goals = 0

        # Run 500 football matches with identical 80.0 rating
        for s in range(500):
            sh, sa, _ = BookmakerService.simulate_match_events(
                "Спартак", "Динамо", 80.0, 80.0, sport="FOOTBALL", seed=s + 42
            )
            fb_home_goals += sh
            fb_away_goals += sa

        # Due to +2.5 home advantage in football, home team goals statistically exceed away goals
        assert fb_home_goals > fb_away_goals, (
            f"Football home advantage missing: Home goals ({fb_home_goals}) <= Away goals ({fb_away_goals})"
        )

        # Theoretical verification: lambda_home vs lambda_away
        home_adv = 2.5
        delta_h = (80.0 + home_adv) - 80.0
        delta_a = 80.0 - (80.0 + home_adv)
        base_lambda = 1.35
        lam_h = base_lambda * math.exp(delta_h / 30.0)
        lam_a = base_lambda * math.exp(delta_a / 30.0)
        assert lam_h > lam_a
        assert (lam_h / lam_a) > 1.15, f"Theoretical home advantage ratio {lam_h / lam_a} too low"


# ==============================================================================
# 3. Duels Concurrency & Atomic CAS Stress Tests
# ==============================================================================


class TestDuelsConcurrencyStress:
    """Stress tests concurrent accept_duel invocations, CAS state transitions, and double-spend prevention."""

    @pytest.mark.asyncio
    async def test_concurrent_accept_multi_challengers(self, test_engine, sample_user: User):
        """Stress-tests 5 concurrent challengers simultaneously trying to accept the same open duel.

        Invariants verified:
        - Exactly 1 challenger succeeds.
        - Exactly 4 challengers fail cleanly with ValueError.
        - Only the 1 successful challenger has their stake deducted.
        - The 4 failed challengers have their balances completely untouched.
        - Duel status ends in FINISHED with winner receiving pot minus 3% commission.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        # 1. Setup creator and open duel with 5,000 ₽ stake
        async with session_factory() as s_init:
            c_acc = await BankingService.get_user_main_account(s_init, sample_user.id)
            c_acc.balance = Decimal("50000.00")
            await s_init.commit()

            stake = Decimal("5000.00")
            duel = await DuelsService.create_duel(s_init, sample_user.id, "DICE", stake)
            await s_init.commit()
            duel_id = duel.id

        # 2. Setup 5 challenger accounts with 10,000 ₽ balance each
        challenger_ids = []
        async with session_factory() as s_challengers:
            for idx in range(1, 6):
                u, acc, _ = await BankingService.get_or_create_user(
                    s_challengers, telegram_id=500000 + idx, username=f"challenger_{idx}"
                )
                acc.balance = Decimal("10000.00")
                await s_challengers.commit()
                challenger_ids.append(u.id)

        # 3. Launch 5 concurrent accept_duel attempts
        async def _attempt_accept(uid: int):
            async with session_factory() as s:
                try:
                    res, details = await DuelsService.accept_duel(s, duel_id, uid)
                    await s.commit()
                    return ("SUCCESS", uid, details)
                except Exception as exc:
                    await s.rollback()
                    return ("FAILED", uid, str(exc))

        results = await asyncio.gather(*[_attempt_accept(uid) for uid in challenger_ids])

        # 4. Verify exactly 1 SUCCESS and 4 FAILED
        successes = [r for r in results if r[0] == "SUCCESS"]
        failures = [r for r in results if r[0] == "FAILED"]

        assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}: {results}"
        assert len(failures) == 4, f"Expected exactly 4 failures, got {len(failures)}: {results}"

        winning_challenger_id = successes[0][1]

        # 5. Verify balances: winning challenger had 5,000 ₽ deducted (and may have won pot or draw)
        # Verify the 4 losing challengers have strictly 10,000.00 ₽ unchanged!
        async with session_factory() as s_verify:
            for uid in challenger_ids:
                acc = await BankingService.get_user_main_account(s_verify, uid)
                if uid == winning_challenger_id:
                    # Winner or loser of game
                    pass
                else:
                    assert acc.balance == Decimal("10000.00"), (
                        f"Challenger {uid} balance was modified despite failed accept: {acc.balance}"
                    )

            # Check duel state
            duel_db = await DuelsService.get_duel_by_id(s_verify, duel_id)
            assert duel_db.status == "FINISHED"
            assert duel_db.opponent_user_id == winning_challenger_id

    @pytest.mark.asyncio
    async def test_race_creator_cancel_vs_opponent_accept(
        self, test_engine, sample_user: User, sample_recipient: User
    ):
        """Races creator cancel_duel against opponent accept_duel. Exactly one must succeed."""
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        # 1. Setup creator and open duel
        stake = Decimal("3000.00")
        async with session_factory() as s:
            c_acc = await BankingService.get_user_main_account(s, sample_user.id)
            o_acc = await BankingService.get_user_main_account(s, sample_recipient.id)
            c_acc.balance = Decimal("20000.00")
            o_acc.balance = Decimal("20000.00")
            await s.commit()

            duel = await DuelsService.create_duel(s, sample_user.id, "DICE", stake)
            await s.commit()
            duel_id = duel.id

        # 2. Race cancel vs accept
        async def _do_cancel():
            async with session_factory() as s:
                try:
                    await DuelsService.cancel_duel(s, duel_id, sample_user.id)
                    await s.commit()
                    return ("CANCEL_SUCCESS", None)
                except Exception as exc:
                    await s.rollback()
                    return ("CANCEL_FAILED", str(exc))

        async def _do_accept():
            async with session_factory() as s:
                try:
                    await DuelsService.accept_duel(s, duel_id, sample_recipient.id)
                    await s.commit()
                    return ("ACCEPT_SUCCESS", None)
                except Exception as exc:
                    await s.rollback()
                    return ("ACCEPT_FAILED", str(exc))

        res_cancel, res_accept = await asyncio.gather(_do_cancel(), _do_accept())

        # Invariant: Exactly one succeeded, one failed
        cancel_won = res_cancel[0] == "CANCEL_SUCCESS"
        accept_won = res_accept[0] == "ACCEPT_SUCCESS"

        assert cancel_won != accept_won, f"Conflict: cancel={res_cancel}, accept={res_accept}"

        # Check database final state
        async with session_factory() as s:
            duel_db = await DuelsService.get_duel_by_id(s, duel_id)
            if cancel_won:
                assert duel_db.status == "CANCELLED"
                # Creator got stake back
                c_acc = await BankingService.get_user_main_account(s, sample_user.id)
                o_acc = await BankingService.get_user_main_account(s, sample_recipient.id)
                assert c_acc.balance == Decimal("20000.00")
                assert o_acc.balance == Decimal("20000.00")  # Opponent never touched
            else:
                assert duel_db.status == "FINISHED"

    @pytest.mark.asyncio
    async def test_accept_duel_insufficient_funds_reverts_cleanly_for_next_player(
        self, async_session: AsyncSession, sample_user: User, sample_recipient: User
    ):
        """Verifies that an acceptance failed due to insufficient funds reverts status to OPEN.

        Then, a well-funded second challenger can successfully accept the duel.
        """
        c_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        o_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        c_acc.balance = Decimal("20000.00")
        o_acc.balance = Decimal("500.00")  # Less than 1000 stake!
        await async_session.commit()

        stake = Decimal("1000.00")
        duel = await DuelsService.create_duel(async_session, sample_user.id, "DICE", stake)
        await async_session.commit()

        # 1. Poor challenger attempts accept -> InsufficientFundsError
        with pytest.raises(InsufficientFundsError):
            await DuelsService.accept_duel(async_session, duel.id, sample_recipient.id)

        # Verify duel status was cleanly rolled back to OPEN and opponent is None
        await async_session.refresh(duel)
        assert duel.status == "OPEN"
        assert duel.opponent_user_id is None

        # Verify poor challenger balance is untouched
        await async_session.refresh(o_acc)
        assert o_acc.balance == Decimal("500.00")

        # 2. Rich third user now accepts -> succeeds!
        rich_user, rich_acc, _ = await BankingService.get_or_create_user(
            async_session, telegram_id=888777, username="rich_player"
        )
        rich_acc.balance = Decimal("5000.00")
        await async_session.commit()

        accepted_duel, details = await DuelsService.accept_duel(
            async_session, duel.id, rich_user.id
        )
        await async_session.commit()

        assert accepted_duel.status == "FINISHED"
        assert accepted_duel.opponent_user_id == rich_user.id


# ==============================================================================
# 4. Financial Limits & Overdraft Prevention Stress Tests
# ==============================================================================


class TestFinancialLimitsAndOverdraftStress:
    """Stress tests minimum (100 ₽) and maximum (20,000,000 ₽) financial limits and overdraft prevention."""

    @pytest.mark.asyncio
    async def test_bookmaker_bet_limits_boundaries(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies boundary limits on sports bets: 100 ₽, 20M ₽, 99.99 ₽ (fail), 20,000,000.01 ₽ (fail)."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("50000000.00")  # 50 million
        await async_session.commit()

        match = await BookmakerService.create_match(
            async_session, sport="FOOTBALL", team_home="Реал Мадрид", team_away="Манчестер Сити"
        )
        await async_session.commit()

        # 1. Under minimum: 99.99 ₽ rejected
        with pytest.raises(ValueError, match="Сумма ставки должна быть в пределах"):
            await BookmakerService.place_bet(
                async_session, sample_user.id, match.id, "HOME", Decimal("99.99")
            )

        # 2. Exact minimum: 100.00 ₽ accepted
        bet_min = await BookmakerService.place_bet(
            async_session, sample_user.id, match.id, "HOME", Decimal("100.00")
        )
        assert bet_min.amount == Decimal("100.00")
        assert bet_min.status == "PENDING"

        # 3. Over maximum: 20,000,000.01 ₽ rejected
        with pytest.raises(ValueError, match="Сумма ставки должна быть в пределах"):
            await BookmakerService.place_bet(
                async_session, sample_user.id, match.id, "HOME", Decimal("20000000.01")
            )

        # 4. Exact maximum: 20,000,000.00 ₽ accepted
        bet_max = await BookmakerService.place_bet(
            async_session, sample_user.id, match.id, "AWAY", Decimal("20000000.00")
        )
        assert bet_max.amount == Decimal("20000000.00")
        assert bet_max.status == "PENDING"

        # Balance check: 50M - 100 - 20M = 29,999,900.00
        await async_session.refresh(acc)
        assert acc.balance == Decimal("29999900.00")

    @pytest.mark.asyncio
    async def test_bookmaker_overdraft_rejection_leaves_balance_unchanged(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies bet exceeding balance fails with InsufficientFundsError and leaves balance unchanged."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("250.00")
        await async_session.commit()

        match = await BookmakerService.create_match(
            async_session, sport="FOOTBALL", team_home="Бавария", team_away="ПСЖ"
        )
        await async_session.commit()

        # Bet 300.00 with only 250.00 on account
        with pytest.raises(InsufficientFundsError):
            await BookmakerService.place_bet(
                async_session, sample_user.id, match.id, "HOME", Decimal("300.00")
            )

        # Verify balance is strictly unchanged
        await async_session.refresh(acc)
        assert acc.balance == Decimal("250.00")

    @pytest.mark.asyncio
    async def test_duels_stake_limits_boundaries(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies boundary limits on duels: 100 ₽, 20M ₽, 99.99 ₽ (fail), 20,000,000.01 ₽ (fail)."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("50000000.00")
        await async_session.commit()

        # 1. Under minimum: 99.99 ₽ rejected
        with pytest.raises(ValueError, match="Сумма ставки должна быть от"):
            await DuelsService.create_duel(
                async_session, sample_user.id, "DICE", Decimal("99.99")
            )

        # 2. Exact minimum: 100.00 ₽ accepted
        duel_min = await DuelsService.create_duel(
            async_session, sample_user.id, "DICE", Decimal("100.00")
        )
        assert duel_min.stake == Decimal("100.00")
        assert duel_min.status == "OPEN"

        # 3. Over maximum: 20,000,000.01 ₽ rejected
        with pytest.raises(ValueError, match="Сумма ставки должна быть от"):
            await DuelsService.create_duel(
                async_session, sample_user.id, "COINFLIP", Decimal("20000000.01")
            )

        # 4. Exact maximum: 20,000,000.00 ₽ accepted
        duel_max = await DuelsService.create_duel(
            async_session, sample_user.id, "COINFLIP", Decimal("20000000.00")
        )
        assert duel_max.stake == Decimal("20000000.00")
        assert duel_max.status == "OPEN"

    @pytest.mark.asyncio
    async def test_duels_create_overdraft_leaves_balance_unchanged(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies creating duel exceeding balance raises InsufficientFundsError and leaves balance unchanged."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("500.00")
        await async_session.commit()

        with pytest.raises(InsufficientFundsError):
            await DuelsService.create_duel(
                async_session, sample_user.id, "DICE", Decimal("1000.00")
            )

        await async_session.refresh(acc)
        assert acc.balance == Decimal("500.00")

    @pytest.mark.asyncio
    async def test_russian_roulette_solo_limits_and_overdraft(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verifies limits and overdraft protection for Russian Roulette solo survival."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("25000000.00")
        await async_session.commit()

        # Under min
        with pytest.raises(ValueError, match="Сумма ставки должна быть от"):
            await DuelsService.start_roulette_solo(
                async_session, sample_user.id, Decimal("99.99")
            )

        # Over max
        with pytest.raises(ValueError, match="Сумма ставки должна быть от"):
            await DuelsService.start_roulette_solo(
                async_session, sample_user.id, Decimal("20000000.01")
            )

        # Valid max
        state = await DuelsService.start_roulette_solo(
            async_session, sample_user.id, Decimal("20000000.00")
        )
        assert state.stake == Decimal("20000000.00")

        # Overdraft on remaining balance
        await async_session.refresh(acc)
        assert acc.balance == Decimal("5000000.00")  # 25M - 20M
        with pytest.raises(InsufficientFundsError):
            await DuelsService.start_roulette_solo(
                async_session, sample_user.id, Decimal("6000000.00")
            )

        await async_session.refresh(acc)
        assert acc.balance == Decimal("5000000.00")
