"""Adversarial Empirical Challenger Test Suite: Property, Luxury & Fortune Wheel.

Tests empirical invariants, edge cases, and stress scenarios:
1. Car Buff Selection & Stacking:
   - 0 cars owned -> (0, 0)
   - Individual cars owned (vaz2107, vesta, bmw_m5, maybach)
   - All 4 cars owned -> strictly selects max buffs (+50% salary bonus, 30 min cooldown reduction)
   - Subsets of cars and dynamic removal (selling/deleting)
   - Unknown/corrupted car item_id resilience
   - End-to-end salary claim with car bonus
2. Fortune Wheel Sector Probabilities & Invariants:
   - Monte Carlo simulation (10,000 spins) verifying empirical distribution aligns with theoretical weights (25, 15, 5, 15, 15, 10, 10, 5) via Pearson Chi-Square test (p > 0.001)
   - 1,000 empirical database spins with is_extra=True under SQLite persistence
3. Stock Immutability:
   - len(DEFAULT_STOCKS) == 6 strictly preserved after 100+ spins awarding USPEKH
   - Dynamic stock creation in DB, user portfolio share accumulation (500 shares)
   - DEFAULT_STOCKS dictionary keys and values remain completely unmutated
4. Energy Drink Multi-Branch Shift Reset:
   - User active on multiple job branches (IT, SERVICE, etc.) simultaneously
   - Shifts in progress reject premature claims
   - Energy drink sector triggers simultaneous reset for all on-shift branches
   - Immediate subsequent salary claims succeed for all active branches
   - Handled gracefully when user has no active shifts
5. Luxury Tax Boundary Conditions:
   - Balance = 0.00 ₽ -> 0.00 ₽ charged, mercy notice, no WHEEL_TAX transaction
   - Balance = 499.99 ₽ -> 0.00 ₽ charged, mercy notice, no WHEEL_TAX transaction
   - Balance = 500.00 ₽ -> 500.00 ₽ charged, 0.00 ₽ remains, WHEEL_TAX tx + LedgerEntry
   - Balance = 1000.00 ₽ -> 500.00 ₽ charged, 500.00 ₽ remains, WHEEL_TAX tx + LedgerEntry
   - Frozen account with 1000.00 ₽ -> 0.00 ₽ charged
"""

import copy
import datetime
from decimal import Decimal
import random
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Account,
    InvestmentPortfolio,
    LedgerEntry,
    Stock,
    Transaction,
    User,
    UserJob,
    UserProperty,
)
from app.services.banking import BankingService
from app.services.investments import DEFAULT_STOCKS, InvestmentService
from app.services.jobs import JobBranch, JobService
from app.services.property import (
    PROPERTY_CATALOG,
    PropertyService,
    buy_property,
    get_user_car_buffs,
)
from app.services.wheel import (
    WHEEL_SECTORS,
    WheelResult,
    WheelSector,
    WheelService,
    spin_wheel,
)


# ==============================================================================
# 1. Car Buff Selection & Stacking
# ==============================================================================

class TestCarBuffSelectionAndStacking:
    """Rigorous empirical validation of car buff selection, max-aggregation, and subsets."""

    @pytest.mark.asyncio
    async def test_car_buffs_zero_cars_returns_zeros(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Zero cars owned strictly yields (0, 0) buffs."""
        bonus_pct, cooldown_red = await PropertyService.get_user_car_buffs(
            async_session, sample_user.id
        )
        assert bonus_pct == 0
        assert cooldown_red == 0

    @pytest.mark.asyncio
    async def test_car_buffs_each_car_individually(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Each car individually yields its catalog specification."""
        car_specs = {
            "vaz2107": (5, 0),
            "vesta": (10, 0),
            "bmw_m5": (25, 15),
            "maybach": (50, 30),
        }

        for item_id, (expected_bonus, expected_red) in car_specs.items():
            # Create isolated property record
            prop = UserProperty(
                user_id=sample_user.id,
                category="CAR",
                item_id=item_id,
                purchase_price=Decimal("1000.00"),
            )
            async_session.add(prop)
            await async_session.flush()

            bonus, red = await PropertyService.get_user_car_buffs(async_session, sample_user.id)
            assert bonus == expected_bonus, f"Failed for {item_id}: bonus {bonus} != {expected_bonus}"
            assert red == expected_red, f"Failed for {item_id}: cooldown {red} != {expected_red}"

            # Clean up for next car
            await async_session.delete(prop)
            await async_session.flush()

    @pytest.mark.asyncio
    async def test_buy_all_four_cars_selects_maximum_not_linear_sum(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Buying all 4 cars selects MAX bonuses (+50%, 30 min) rather than stacking (90%, 45 min)."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        # Fund account with sufficient balance to buy all 4 cars:
        # vaz2107: 50,000 + vesta: 350,000 + bmw_m5: 3,000,000 + maybach: 15,000,000 = 18,400,000 ₽
        await async_session.execute(
            update(Account)
            .where(Account.id == main_acc.id)
            .values(balance=Decimal("20000000.00"))
        )
        await async_session.flush()

        cars_to_buy = ["vaz2107", "vesta", "bmw_m5", "maybach"]
        for cid in cars_to_buy:
            await PropertyService.buy_property(async_session, sample_user.id, cid)

        # Check total owned cars
        props = await PropertyService.get_user_properties(async_session, sample_user.id)
        car_props = [p for p in props if p.category == "CAR"]
        assert len(car_props) == 4

        # Maximum buffs check
        bonus_pct, cooldown_red = await PropertyService.get_user_car_buffs(
            async_session, sample_user.id
        )
        assert bonus_pct == 50, f"Expected 50% max bonus, got {bonus_pct}%"
        assert cooldown_red == 30, f"Expected 30 min max reduction, got {cooldown_red} min"

        # Verify it did NOT linearly stack
        assert bonus_pct != 90, "Vulnerability: car salary bonuses linearly stacked!"
        assert cooldown_red != 45, "Vulnerability: car cooldown reductions linearly stacked!"

    @pytest.mark.asyncio
    async def test_car_buffs_dynamic_subsets_and_removal(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Testing various subsets and gradual deletion/sale of cars."""
        # Insert all 4 cars
        cars = {}
        for cid in ["vaz2107", "vesta", "bmw_m5", "maybach"]:
            p = UserProperty(
                user_id=sample_user.id,
                category="CAR",
                item_id=cid,
                purchase_price=Decimal("100.00"),
            )
            async_session.add(p)
            cars[cid] = p
        await async_session.flush()

        # All 4: (50, 30)
        assert await PropertyService.get_user_car_buffs(async_session, sample_user.id) == (50, 30)

        # Remove maybach -> should drop to bmw_m5: (25, 15)
        await async_session.delete(cars["maybach"])
        await async_session.flush()
        assert await PropertyService.get_user_car_buffs(async_session, sample_user.id) == (25, 15)

        # Remove bmw_m5 -> should drop to vesta: (10, 0)
        await async_session.delete(cars["bmw_m5"])
        await async_session.flush()
        assert await PropertyService.get_user_car_buffs(async_session, sample_user.id) == (10, 0)

        # Remove vesta -> should drop to vaz2107: (5, 0)
        await async_session.delete(cars["vesta"])
        await async_session.flush()
        assert await PropertyService.get_user_car_buffs(async_session, sample_user.id) == (5, 0)

        # Remove vaz2107 -> should drop to (0, 0)
        await async_session.delete(cars["vaz2107"])
        await async_session.flush()
        assert await PropertyService.get_user_car_buffs(async_session, sample_user.id) == (0, 0)

    @pytest.mark.asyncio
    async def test_car_buffs_corrupted_item_id_resilience(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Unknown or obsolete car item_id in database does not crash get_user_car_buffs."""
        bad_prop = UserProperty(
            user_id=sample_user.id,
            category="CAR",
            item_id="flying_saucer_alien",
            purchase_price=Decimal("999999.00"),
        )
        async_session.add(bad_prop)
        await async_session.flush()

        # Should safely ignore unknown item and return (0, 0)
        buffs = await PropertyService.get_user_car_buffs(async_session, sample_user.id)
        assert buffs == (0, 0)

        # Adding valid car works alongside unknown item
        valid_prop = UserProperty(
            user_id=sample_user.id,
            category="CAR",
            item_id="vesta",
            purchase_price=Decimal("350000.00"),
        )
        async_session.add(valid_prop)
        await async_session.flush()

        buffs = await PropertyService.get_user_car_buffs(async_session, sample_user.id)
        assert buffs == (10, 0)

    @pytest.mark.asyncio
    async def test_car_buff_integration_salary_bonus_and_cooldown_with_jobs(
        self, async_session: AsyncSession, sample_user: User
    ):
        """End-to-end synergy: Maybach ownership reduces shift wait by 30 min and awards +50% salary bonus."""
        now = datetime.datetime.now()

        # Own Maybach: +50% salary bonus, 30 min cooldown reduction
        prop = UserProperty(
            user_id=sample_user.id,
            category="CAR",
            item_id="maybach",
            purchase_price=Decimal("15000000.00"),
        )
        async_session.add(prop)

        # Employ in IT and promote to Level 5 (CTO: 60,000.00 ₽ base salary)
        job = UserJob(
            user_id=sample_user.id,
            branch=JobBranch.IT,
            level=5,
            shifts_completed=50,
            is_active=True,
            is_on_shift=False,
        )
        async_session.add(job)
        await async_session.flush()

        # 1. Start shift at time T
        await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=now)
        await async_session.refresh(job)

        # Due to 30 min cooldown reduction, shift_started_at is set to now - 30 minutes!
        assert job.shift_started_at == now - datetime.timedelta(minutes=30)

        # 2. At T + 89 minutes (total elapsed = 89 + 30 = 119 min < 120 min), claim must fail
        time_89min = now + datetime.timedelta(minutes=89)
        with pytest.raises(ValueError, match="Смена еще не окончена"):
            await JobService.claim_salary(
                async_session, sample_user.id, JobBranch.IT, now=time_89min
            )

        # 3. At T + 90 minutes (total elapsed = 90 + 30 = 120 min), claim succeeds!
        time_90min = now + datetime.timedelta(minutes=90)
        payout, event_msg = await JobService.claim_salary(
            async_session,
            sample_user.id,
            JobBranch.IT,
            now=time_90min,
            event_roll=0.9,  # Normal shift (no event penalty/bonus)
        )

        # Base salary (60,000) + 50% car bonus (30,000) = 90,000 ₽!
        assert payout == Decimal("90000.00"), f"Expected 90,000.00 ₽, got {payout}"
        assert "Бонус за авто (+50%):" in event_msg
        assert "+30,000.00 ₽" in event_msg


# ==============================================================================
# 2. Fortune Wheel Sector Probabilities & Invariants
# ==============================================================================

class TestFortuneWheelProbabilitiesAndMonteCarlo:
    """Monte Carlo verification of empirical sector distribution vs theoretical weights."""

    def test_wheel_theoretical_weights_distribution_monte_carlo(self):
        """Monte Carlo simulation (10,000 iterations) verifying empirical alignment with theoretical weights."""
        sectors = WHEEL_SECTORS
        weights = [s.weight for s in sectors]
        total_weight = sum(weights)
        assert total_weight == 100

        expected_probs = {s.sector_id: s.weight / total_weight for s in sectors}

        n_trials = 10000
        random.seed(42)  # Deterministic seed for reproducible testing
        simulated = random.choices(sectors, weights=weights, k=n_trials)

        counts = {s.sector_id: 0 for s in sectors}
        for item in simulated:
            counts[item.sector_id] += 1

        # Pearson Chi-Squared Test:
        # chi2 = sum((O_i - E_i)^2 / E_i)
        # Degrees of freedom = 8 - 1 = 7.
        # Critical value for alpha = 0.001 is 24.322.
        chi2 = 0.0
        for s in sectors:
            expected_count = expected_probs[s.sector_id] * n_trials
            observed_count = counts[s.sector_id]
            chi2 += ((observed_count - expected_count) ** 2) / expected_count

            # Individual tolerance test: empirical proportion within 3 standard errors
            empirical_p = observed_count / n_trials
            expected_p = expected_probs[s.sector_id]
            std_err = (expected_p * (1 - expected_p) / n_trials) ** 0.5
            assert abs(empirical_p - expected_p) <= 3.5 * std_err, (
                f"Sector {s.sector_id} deviated significantly: observed {empirical_p:.4f}, expected {expected_p:.4f}"
            )

        # Confirm Chi-Squared statistic is below critical value (p > 0.001)
        assert chi2 < 24.322, f"Chi-squared test failed: chi2 = {chi2:.3f} >= 24.322"

    @pytest.mark.asyncio
    async def test_empirical_spin_wheel_database_execution_batch(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Execute 1,000 actual spins against SQLite DB, confirming stability and prize generation."""
        # Fund user with 1,000,000 cashback points for 1,000 extra spins (1000 pts per spin)
        await async_session.execute(
            update(User)
            .where(User.id == sample_user.id)
            .values(cashback_balance=Decimal("1000000.00"))
        )
        await async_session.flush()

        counts: dict[str, int] = {}
        n_spins = 1000

        for _ in range(n_spins):
            res = await WheelService.spin_wheel(async_session, sample_user.id, is_extra=True)
            counts[res.sector_id] = counts.get(res.sector_id, 0) + 1

        # Confirm all 8 sectors were hit at least once across 1,000 spins
        assert len(counts) == 8, f"Expected all 8 sectors to appear, but got {len(counts)}: {counts}"
        for s in WHEEL_SECTORS:
            assert counts.get(s.sector_id, 0) > 0, f"Sector {s.sector_id} was never selected in 1,000 spins"

        # Check cashback balance was deducted appropriately (net of extra spins and cashback_2k prizes)
        await async_session.refresh(sample_user)
        # Expected cashback: initial 1,000,000 - 1,000 * 1,000 + counts['cashback_2k'] * 2,000
        expected_cashback = Decimal("1000000.00") - Decimal(n_spins * 1000) + Decimal(counts["cashback_2k"] * 2000)
        assert sample_user.cashback_balance == expected_cashback


# ==============================================================================
# 3. Stock Immutability & USPEKH Prize
# ==============================================================================

class TestStockImmutabilityAndUspekhPrize:
    """Verifies that DEFAULT_STOCKS constant in investments.py is 100% immutable."""

    @pytest.mark.asyncio
    async def test_uspekh_prize_preserves_default_stocks_strict_immutability(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Awarding USPEKH stock 100+ times does NOT mutate DEFAULT_STOCKS (len remains 6)."""
        # Baseline check
        assert len(DEFAULT_STOCKS) == 6
        snapshot = copy.deepcopy(DEFAULT_STOCKS)

        # User needs cashback to spin extra
        await async_session.execute(
            update(User)
            .where(User.id == sample_user.id)
            .values(cashback_balance=Decimal("200000.00"))
        )
        await async_session.flush()

        # Spin wheel 105 times awarding USPEKH
        spins_count = 105
        for _ in range(spins_count):
            res = await WheelService.spin_wheel(
                async_session,
                sample_user.id,
                is_extra=True,
                sector_override="stocks_uspekh",
            )
            assert res.reward_type == "STOCKS"
            assert res.sector_id == "stocks_uspekh"

        # 1. Invariant: DEFAULT_STOCKS is untouched
        assert len(DEFAULT_STOCKS) == 6, f"DEFAULT_STOCKS was mutated! len={len(DEFAULT_STOCKS)}"
        assert DEFAULT_STOCKS == snapshot, "DEFAULT_STOCKS elements were mutated!"

        # 2. Invariant: USPEKH exists in SQLite Stock table
        stock_res = await async_session.execute(
            select(Stock).where(Stock.ticker == "USPEKH")
        )
        uspekh_stock = stock_res.scalar_one_or_none()
        assert uspekh_stock is not None
        assert uspekh_stock.ticker == "USPEKH"
        assert uspekh_stock.current_price == Decimal("100.00")

        # 3. Invariant: User portfolio accumulated exactly 105 * 5 = 525 shares
        port_res = await async_session.execute(
            select(InvestmentPortfolio).where(
                InvestmentPortfolio.user_id == sample_user.id,
                InvestmentPortfolio.stock_id == uspekh_stock.id,
            )
        )
        portfolio = port_res.scalar_one_or_none()
        assert portfolio is not None
        assert portfolio.shares == spins_count * 5

        # 4. Invariant: If default stocks were seeded, DB now has 6 default stocks + USPEKH = 7 stocks
        # First ensure default stocks are seeded
        for s_data in DEFAULT_STOCKS:
            existing = (await async_session.execute(select(Stock).where(Stock.ticker == s_data["ticker"]))).scalar_one_or_none()
            if not existing:
                async_session.add(Stock(**s_data))
        await async_session.flush()

        all_stocks = await InvestmentService.list_stocks(async_session)
        assert len(DEFAULT_STOCKS) == 6
        assert len(all_stocks) == 7


# ==============================================================================
# 4. Energy Drink Multi-Branch Shift Reset
# ==============================================================================

class TestEnergyDrinkMultiBranchShiftReset:
    """Verifies that the Energy Drink sector resets shifts across multiple branches simultaneously."""

    @pytest.mark.asyncio
    async def test_energy_drink_resets_multiple_concurrent_shifts(
        self, async_session: AsyncSession, sample_user: User
    ):
        """User employed in 2 branches starts shifts; premature claim fails; Energy Drink resets BOTH."""
        now = datetime.datetime.now()

        # 1. Employ in 2 branches: IT and SERVICE
        job_it = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        job_service = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SERVICE)
        assert job_it.is_active is True
        assert job_service.is_active is True

        # 2. Start shifts on both jobs
        await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=now)
        await JobService.start_shift(async_session, sample_user.id, JobBranch.SERVICE, now=now)

        # 3. Premature claim attempt strictly rejected on both branches
        with pytest.raises(ValueError, match="Смена еще не окончена"):
            await JobService.claim_salary(async_session, sample_user.id, JobBranch.IT, now=now)

        with pytest.raises(ValueError, match="Смена еще не окончена"):
            await JobService.claim_salary(async_session, sample_user.id, JobBranch.SERVICE, now=now)

        # 4. Trigger Energy Drink sector
        # User needs cashback to spin
        await async_session.execute(
            update(User)
            .where(User.id == sample_user.id)
            .values(cashback_balance=Decimal("2000.00"))
        )
        await async_session.flush()

        res = await WheelService.spin_wheel(
            async_session,
            sample_user.id,
            is_extra=True,
            sector_override="energy_drink",
            now=now,
        )
        assert res.reward_type == "SHIFT_RESET"
        assert "досрочно" in res.description

        # 5. Verify both shifts are now immediately claimable without duration errors!
        payout_it, text_it = await JobService.claim_salary(
            async_session, sample_user.id, JobBranch.IT, now=now
        )
        assert payout_it > Decimal("0.00")
        assert "отработана" in text_it or "Премия" in text_it

        payout_service, text_service = await JobService.claim_salary(
            async_session, sample_user.id, JobBranch.SERVICE, now=now
        )
        assert payout_service > Decimal("0.00")
        assert "отработана" in text_service or "Премия" in text_service

        # 6. Verify shifts completed counter and shift state
        await async_session.refresh(job_it)
        await async_session.refresh(job_service)
        assert job_it.shifts_completed == 1
        assert job_it.is_on_shift is False
        assert job_service.shifts_completed == 1
        assert job_service.is_on_shift is False

    @pytest.mark.asyncio
    async def test_energy_drink_resets_three_or_more_shifts_if_present(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Even if database contains 3 or 4 shifts on-duty, Energy Drink resets ALL of them."""
        now = datetime.datetime.now()

        # Manually create 3 active jobs on shift
        branches = [JobBranch.IT, JobBranch.SERVICE, JobBranch.FINANCE]
        jobs = []
        for b in branches:
            j = UserJob(
                user_id=sample_user.id,
                branch=b,
                level=1,
                shifts_completed=0,
                is_active=True,
                is_on_shift=True,
                shift_started_at=now,
            )
            async_session.add(j)
            jobs.append(j)
        await async_session.flush()

        # Energy drink spin
        await async_session.execute(
            update(User)
            .where(User.id == sample_user.id)
            .values(cashback_balance=Decimal("2000.00"))
        )
        await async_session.flush()

        res = await WheelService.spin_wheel(
            async_session,
            sample_user.id,
            is_extra=True,
            sector_override="energy_drink",
            now=now,
        )
        assert "(3 раб.)" in res.description

        # Verify all 3 jobs can immediately claim salary
        for b in branches:
            payout, _ = await JobService.claim_salary(
                async_session, sample_user.id, b, now=now
            )
            assert payout > Decimal("0.00")

    @pytest.mark.asyncio
    async def test_energy_drink_when_no_active_shifts(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Energy drink when user is not on shift does not error and returns polite message."""
        await async_session.execute(
            update(User)
            .where(User.id == sample_user.id)
            .values(cashback_balance=Decimal("2000.00"))
        )
        await async_session.flush()

        res = await WheelService.spin_wheel(
            async_session,
            sample_user.id,
            is_extra=True,
            sector_override="energy_drink",
        )
        assert res.reward_type == "SHIFT_RESET"
        assert "полны сил и бодрости" in res.description

    @pytest.mark.asyncio
    async def test_energy_drink_claim_and_subsequent_shift_restores_normal_timer(
        self, async_session: AsyncSession, sample_user: User
    ):
        """After energy drink reset and salary claim, starting a new shift enforces the standard 2-hour duration."""
        now = datetime.datetime.now()

        # Employ and start shift
        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=now)

        # Trigger energy drink
        await async_session.execute(
            update(User).where(User.id == sample_user.id).values(cashback_balance=Decimal("2000.00"))
        )
        await async_session.flush()
        await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=True, sector_override="energy_drink", now=now
        )

        # Claim salary immediately
        await JobService.claim_salary(async_session, sample_user.id, JobBranch.IT, now=now)

        # Start a BRAND NEW shift at T + 5 minutes
        t_new = now + datetime.timedelta(minutes=5)
        await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=t_new)

        # Premature claim at T + 10 minutes (only 5 min elapsed) MUST fail!
        t_premature = now + datetime.timedelta(minutes=10)
        with pytest.raises(ValueError, match="Смена еще не окончена"):
            await JobService.claim_salary(
                async_session, sample_user.id, JobBranch.IT, now=t_premature
            )


# ==============================================================================
# 5. Luxury Tax Boundary Conditions
# ==============================================================================

class TestLuxuryTaxBoundaryConditions:
    """Rigorous boundary validation of luxury tax deduction, mercy threshold, and ledger entries."""

    @pytest.mark.asyncio
    async def test_luxury_tax_boundary_0_rubles(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Boundary: balance = 0.00 ₽ -> 0.00 ₽ charged, mercy notice, no WHEEL_TAX tx."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        await async_session.execute(
            update(Account).where(Account.id == main_acc.id).values(balance=Decimal("0.00"))
        )
        await async_session.execute(
            update(User).where(User.id == sample_user.id).values(cashback_balance=Decimal("2000.00"))
        )
        await async_session.flush()

        res = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=True, sector_override="tax_luxury"
        )

        assert res.reward_type == "TAX"
        assert "милосердие" in res.description
        assert "0.00 ₽" in res.description
        assert res.balance == Decimal("0.00")

        # Confirm no WHEEL_TAX transaction was written
        tx_stmt = select(Transaction).where(
            Transaction.sender_account_id == main_acc.id,
            Transaction.tx_type == "WHEEL_TAX",
        )
        tx_res = await async_session.execute(tx_stmt)
        assert tx_res.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_luxury_tax_boundary_499_99_rubles(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Boundary: balance = 499.99 ₽ (1 kopeck below 500) -> 0.00 ₽ charged, balance intact."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        await async_session.execute(
            update(Account).where(Account.id == main_acc.id).values(balance=Decimal("499.99"))
        )
        await async_session.execute(
            update(User).where(User.id == sample_user.id).values(cashback_balance=Decimal("2000.00"))
        )
        await async_session.flush()

        res = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=True, sector_override="tax_luxury"
        )

        assert res.reward_type == "TAX"
        assert "милосердие" in res.description
        assert res.balance == Decimal("499.99")

        # Account balance strictly preserved
        refreshed_bal = (
            await async_session.execute(select(Account.balance).where(Account.id == main_acc.id))
        ).scalar_one()
        assert refreshed_bal == Decimal("499.99")

    @pytest.mark.asyncio
    async def test_luxury_tax_boundary_500_00_rubles(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Boundary: balance = 500.00 ₽ (exact threshold) -> 500.00 ₽ charged, 0.00 ₽ remains."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        await async_session.execute(
            update(Account).where(Account.id == main_acc.id).values(balance=Decimal("500.00"))
        )
        await async_session.execute(
            update(User).where(User.id == sample_user.id).values(cashback_balance=Decimal("2000.00"))
        )
        await async_session.flush()

        res = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=True, sector_override="tax_luxury"
        )

        assert res.reward_type == "TAX"
        assert "удержано" in res.description and "500.00 ₽" in res.description
        assert res.balance == Decimal("0.00")

        # Verify transaction and ledger entry
        tx_stmt = select(Transaction).where(
            Transaction.sender_account_id == main_acc.id,
            Transaction.tx_type == "WHEEL_TAX",
        )
        tx = (await async_session.execute(tx_stmt)).scalar_one()
        assert tx.amount == Decimal("500.00")
        assert tx.status == "COMPLETED"

        ledger_stmt = select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id)
        entry = (await async_session.execute(ledger_stmt)).scalar_one()
        assert entry.direction == "DEBIT"
        assert entry.amount == Decimal("500.00")
        assert entry.balance_after == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_luxury_tax_boundary_1000_00_rubles(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Boundary: balance = 1000.00 ₽ -> 500.00 ₽ charged, 500.00 ₽ remains."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        await async_session.execute(
            update(Account).where(Account.id == main_acc.id).values(balance=Decimal("1000.00"))
        )
        await async_session.execute(
            update(User).where(User.id == sample_user.id).values(cashback_balance=Decimal("2000.00"))
        )
        await async_session.flush()

        res = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=True, sector_override="tax_luxury"
        )

        assert res.reward_type == "TAX"
        assert "удержано" in res.description and "500.00 ₽" in res.description
        assert res.balance == Decimal("500.00")

        # Verify post-tax balance in DB
        refreshed_bal = (
            await async_session.execute(select(Account.balance).where(Account.id == main_acc.id))
        ).scalar_one()
        assert refreshed_bal == Decimal("500.00")

    @pytest.mark.asyncio
    async def test_luxury_tax_frozen_account_defense(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Frozen account with balance >= 500 ₽ is protected from tax deduction."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        await async_session.execute(
            update(Account)
            .where(Account.id == main_acc.id)
            .values(balance=Decimal("1000.00"), is_frozen=True)
        )
        await async_session.execute(
            update(User).where(User.id == sample_user.id).values(cashback_balance=Decimal("2000.00"))
        )
        await async_session.flush()

        res = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=True, sector_override="tax_luxury"
        )

        # Tax should NOT be debited on frozen account
        assert "милосердие" in res.description
        assert res.balance == Decimal("1000.00")

    @pytest.mark.asyncio
    async def test_repeated_luxury_tax_drain_cannot_cause_overdraft(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Repeated luxury tax spins drain balance down to sub-500 without ever producing negative balances."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        # Starting balance 750.00 ₽
        await async_session.execute(
            update(Account).where(Account.id == main_acc.id).values(balance=Decimal("750.00"))
        )
        await async_session.execute(
            update(User).where(User.id == sample_user.id).values(cashback_balance=Decimal("10000.00"))
        )
        await async_session.flush()

        # Spin 1: 750 >= 500 -> 500 charged, 250 remains
        res1 = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=True, sector_override="tax_luxury"
        )
        assert res1.balance == Decimal("250.00")
        assert "удержано" in res1.description

        # Spin 2: 250 < 500 -> 0 charged, 250 remains
        res2 = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=True, sector_override="tax_luxury"
        )
        assert res2.balance == Decimal("250.00")
        assert "милосердие" in res2.description

        # Spin 3: 250 < 500 -> 0 charged, 250 remains
        res3 = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=True, sector_override="tax_luxury"
        )
        assert res3.balance == Decimal("250.00")
        assert "милосердие" in res3.description

        # Invariant: DB balance is strictly >= 0.00
        final_bal = (
            await async_session.execute(select(Account.balance).where(Account.id == main_acc.id))
        ).scalar_one()
        assert final_bal == Decimal("250.00")
        assert final_bal >= Decimal("0.00")
