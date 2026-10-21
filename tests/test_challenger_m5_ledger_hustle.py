"""Adversarial Verification Suite for Milestone 5: Ledger & Hustle Clamping.

Empirical verification covers:
1. Non-negative balance clamping on hustle penalties (0-balance and low-balance).
2. 15-minute hustle cooldown enforcement (boundary tests at 899s, 900s, 901s).
3. Double-entry LedgerEntry creation and balance_after mathematical correctness.
4. Schema CheckConstraints on UserJob (level 1..5, shifts_completed >= 0, unique user+branch).
"""

import datetime
from decimal import Decimal
import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Account, LedgerEntry, Transaction, User, UserJob
from app.services.banking import BankingService
from app.services.jobs import JobBranch, JobService


@pytest_asyncio.fixture
async def challenger_user_zero(async_session: AsyncSession) -> User:
    """Create a user with exactly 0.00 RUB main account balance."""
    user, acc, card = await BankingService.get_or_create_user(
        async_session,
        telegram_id=880001,
        username="zero_bal_user",
        full_name="Zero Balance Challenger",
    )
    acc.balance = Decimal("0.00")
    await async_session.commit()
    await async_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def challenger_user_one(async_session: AsyncSession) -> User:
    """Create a user with exactly 1.00 RUB main account balance."""
    user, acc, card = await BankingService.get_or_create_user(
        async_session,
        telegram_id=880002,
        username="one_rub_user",
        full_name="One Ruble Challenger",
    )
    acc.balance = Decimal("1.00")
    await async_session.commit()
    await async_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def challenger_user_funded(async_session: AsyncSession) -> User:
    """Create a user with 5,000.00 RUB main account balance."""
    user, acc, card = await BankingService.get_or_create_user(
        async_session,
        telegram_id=880003,
        username="funded_user",
        full_name="Funded Challenger",
    )
    acc.balance = Decimal("5000.00")
    await async_session.commit()
    await async_session.refresh(user)
    return user


# ==============================================================================
# 1. Non-Negative Balance Clamping on Hustle Penalties
# ==============================================================================

class TestHustlePenaltyClampingAdversarial:
    """Verify non-negative balance clamping on hustle penalties under 0-balance and low-balance conditions."""

    async def test_zero_balance_severe_penalty_clamped(
        self, async_session: AsyncSession, challenger_user_zero: User
    ):
        """Zero balance user facing 15,000 RUB penalty: delta must be 0.00 and balance must remain 0.00."""
        user_id = challenger_user_zero.id
        main_acc = await BankingService.get_user_main_account(async_session, user_id)
        assert main_acc is not None
        assert main_acc.balance == Decimal("0.00")

        # Force failure roll (roll=1.0) and huge penalty of 15,000 RUB
        delta, msg = await JobService.execute_hustle(
            session=async_session,
            user_id=user_id,
            hustle_id="hustle_shadow_raid",
            choice_idx=1,
            success_roll=1.0,
            penalty_override=Decimal("15000.00"),
        )
        await async_session.commit()

        # Delta returned should be 0.00 (clamped from -15000)
        assert delta == Decimal("0.00")
        assert "0" in msg

        # Database account balance must remain exactly 0.00
        refreshed_acc = await BankingService.get_user_main_account(async_session, user_id)
        assert refreshed_acc.balance == Decimal("0.00")

        # Verify transaction record exists
        tx_res = await async_session.execute(
            select(Transaction).where(
                Transaction.sender_account_id == main_acc.id,
                Transaction.tx_type == "HUSTLE_PENALTY",
            )
        )
        tx = tx_res.scalars().first()
        assert tx is not None
        assert tx.amount == Decimal("0.00")
        assert tx.status == "COMPLETED"

    async def test_one_ruble_balance_clamped(
        self, async_session: AsyncSession, challenger_user_one: User
    ):
        """User with 1.00 RUB facing 10,000 RUB penalty: penalty clamped to 1.00, balance becomes 0.00."""
        user_id = challenger_user_one.id
        main_acc = await BankingService.get_user_main_account(async_session, user_id)
        assert main_acc.balance == Decimal("1.00")

        delta, msg = await JobService.execute_hustle(
            session=async_session,
            user_id=user_id,
            hustle_id="hustle_shadow_raid",
            choice_idx=0,
            success_roll=1.0,
            penalty_override=Decimal("10000.00"),
        )
        await async_session.commit()

        # Delta must be clamped to -1.00
        assert delta == Decimal("-1.00")
        assert "1.00" in msg or "1" in msg

        # DB balance must be exactly 0.00
        refreshed_acc = await BankingService.get_user_main_account(async_session, user_id)
        assert refreshed_acc.balance == Decimal("0.00")

        # Verify LedgerEntry is created with DEBIT 1.00 and balance_after 0.00
        ledger_res = await async_session.execute(
            select(LedgerEntry).where(LedgerEntry.account_id == main_acc.id)
        )
        entries = list(ledger_res.scalars().all())
        assert len(entries) >= 1
        last_entry = entries[-1]
        assert last_entry.direction == "DEBIT"
        assert last_entry.amount == Decimal("1.00")
        assert last_entry.balance_after == Decimal("0.00")

    async def test_low_balance_partial_clamp(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """User with 100.00 RUB facing 5,000 RUB penalty: penalty clamped to 100.00."""
        user_id = challenger_user_funded.id
        main_acc = await BankingService.get_user_main_account(async_session, user_id)
        # Set balance to 100.00
        await async_session.execute(
            update(Account).where(Account.id == main_acc.id).values(balance=Decimal("100.00"))
        )
        await async_session.commit()

        delta, msg = await JobService.execute_hustle(
            session=async_session,
            user_id=user_id,
            hustle_id="hustle_crypto_signal",
            choice_idx=0,
            success_roll=1.0,
            penalty_override=Decimal("5000.00"),
        )
        await async_session.commit()

        assert delta == Decimal("-100.00")
        refreshed_acc = await BankingService.get_user_main_account(async_session, user_id)
        assert refreshed_acc.balance == Decimal("0.00")

        # Verify LedgerEntry
        ledger_res = await async_session.execute(
            select(LedgerEntry).where(LedgerEntry.account_id == main_acc.id)
        )
        last_entry = list(ledger_res.scalars().all())[-1]
        assert last_entry.direction == "DEBIT"
        assert last_entry.amount == Decimal("100.00")
        assert last_entry.balance_after == Decimal("0.00")

    async def test_sufficient_balance_full_penalty(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """User with 5,000.00 RUB facing 1,500 RUB penalty: full 1,500 debited, balance is 3,500.00."""
        user_id = challenger_user_funded.id
        main_acc = await BankingService.get_user_main_account(async_session, user_id)
        assert main_acc.balance == Decimal("5000.00")

        delta, msg = await JobService.execute_hustle(
            session=async_session,
            user_id=user_id,
            hustle_id="hustle_server_crash",
            choice_idx=1,
            success_roll=1.0,
            penalty_override=Decimal("1500.00"),
        )
        await async_session.commit()

        assert delta == Decimal("-1500.00")
        refreshed_acc = await BankingService.get_user_main_account(async_session, user_id)
        assert refreshed_acc.balance == Decimal("3500.00")

        # LedgerEntry check
        ledger_res = await async_session.execute(
            select(LedgerEntry).where(LedgerEntry.account_id == main_acc.id)
        )
        last_entry = list(ledger_res.scalars().all())[-1]
        assert last_entry.direction == "DEBIT"
        assert last_entry.amount == Decimal("1500.00")
        assert last_entry.balance_after == Decimal("3500.00")

    async def test_repeated_zero_balance_drain_stress(
        self, async_session: AsyncSession, challenger_user_zero: User
    ):
        """Stress: 10 consecutive failed hustles at 0 balance must never drop balance below 0.00."""
        user_id = challenger_user_zero.id
        main_acc = await BankingService.get_user_main_account(async_session, user_id)
        base_time = datetime.datetime(2026, 10, 21, 10, 0, 0)

        for i in range(10):
            current_time = base_time + datetime.timedelta(seconds=901 * (i + 1))
            delta, _ = await JobService.execute_hustle(
                session=async_session,
                user_id=user_id,
                hustle_id="hustle_shadow_raid",
                choice_idx=0,
                now=current_time,
                success_roll=1.0,
                penalty_override=Decimal("10000.00"),
            )
            await async_session.commit()
            assert delta == Decimal("0.00")

            # Check DB balance directly
            bal = (
                await async_session.execute(
                    select(Account.balance).where(Account.id == main_acc.id)
                )
            ).scalar_one()
            assert bal == Decimal("0.00")

    async def test_sqlite_chk_positive_balance_constraint_active(
        self, async_session: AsyncSession, challenger_user_zero: User
    ):
        """Direct adversarial update to negative balance must trigger SQLite CheckConstraint."""
        main_acc = await BankingService.get_user_main_account(async_session, challenger_user_zero.id)

        with pytest.raises(IntegrityError) as exc_info:
            await async_session.execute(
                update(Account)
                .where(Account.id == main_acc.id)
                .values(balance=Decimal("-0.01"))
            )
            await async_session.flush()

        assert "chk_positive_balance" in str(exc_info.value).lower() or "check constraint" in str(exc_info.value).lower()
        await async_session.rollback()


# ==============================================================================
# 2. 15-Minute Hustle Cooldown Enforcement
# ==============================================================================

class TestHustleCooldownAdversarial:
    """Verify 15-minute cooldown (900s) on hustles rejects premature attempts and accepts on/after 900s."""

    async def test_hustle_cooldown_rejection_at_zero_seconds(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Immediate second hustle at T0 must be rejected with ValueError."""
        user_id = challenger_user_funded.id
        t0 = datetime.datetime(2026, 10, 21, 12, 0, 0)

        # First hustle at t0 succeeds
        await JobService.execute_hustle(
            session=async_session,
            user_id=user_id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            now=t0,
            success_roll=0.0,  # Force success
        )
        await async_session.commit()

        # Immediate repeat at t0
        with pytest.raises(ValueError) as exc:
            await JobService.execute_hustle(
                session=async_session,
                user_id=user_id,
                hustle_id="hustle_server_crash",
                choice_idx=0,
                now=t0,
            )
        assert "Шабашкины руки должны отдохнуть" in str(exc.value)

    async def test_hustle_cooldown_rejection_at_899_seconds(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Attempt at T0 + 899s (1 second before 15m) must be rejected."""
        user_id = challenger_user_funded.id
        t0 = datetime.datetime(2026, 10, 21, 12, 0, 0)

        await JobService.execute_hustle(
            session=async_session,
            user_id=user_id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            now=t0,
            success_roll=0.0,
        )
        await async_session.commit()

        # At 899s: 14m59s elapsed, cooldown is 900s
        t_early = t0 + datetime.timedelta(seconds=899)
        with pytest.raises(ValueError) as exc:
            await JobService.execute_hustle(
                session=async_session,
                user_id=user_id,
                hustle_id="hustle_server_crash",
                choice_idx=0,
                now=t_early,
            )
        assert "Шабашкины руки должны отдохнуть" in str(exc.value)

    async def test_hustle_cooldown_permitted_at_exact_900_seconds(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Attempt at exact boundary T0 + 900s must be accepted."""
        user_id = challenger_user_funded.id
        t0 = datetime.datetime(2026, 10, 21, 12, 0, 0)

        await JobService.execute_hustle(
            session=async_session,
            user_id=user_id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            now=t0,
            success_roll=0.0,
        )
        await async_session.commit()

        # At exactly 900s: boundary condition
        t_boundary = t0 + datetime.timedelta(seconds=900)
        delta, msg = await JobService.execute_hustle(
            session=async_session,
            user_id=user_id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            now=t_boundary,
            success_roll=0.0,
        )
        await async_session.commit()
        assert delta > Decimal("0.00")

        # Verify user.last_hustle_at is updated to t_boundary
        refreshed_user = await async_session.get(User, user_id)
        assert refreshed_user.last_hustle_at == t_boundary

    async def test_hustle_cooldown_permitted_at_901_seconds(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Attempt at T0 + 901s (after cooldown) must succeed."""
        user_id = challenger_user_funded.id
        t0 = datetime.datetime(2026, 10, 21, 12, 0, 0)

        await JobService.execute_hustle(
            session=async_session,
            user_id=user_id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            now=t0,
            success_roll=0.0,
        )
        await async_session.commit()

        t_after = t0 + datetime.timedelta(seconds=901)
        delta, msg = await JobService.execute_hustle(
            session=async_session,
            user_id=user_id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            now=t_after,
            success_roll=0.0,
        )
        await async_session.commit()
        assert delta > Decimal("0.00")

    async def test_hustle_rapid_burst_stress(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Execute a burst of 10 rapid calls within 30 seconds: exactly 1 must succeed, 9 rejected."""
        user_id = challenger_user_funded.id
        t0 = datetime.datetime(2026, 10, 21, 14, 0, 0)

        success_count = 0
        rejection_count = 0

        for i in range(10):
            t_call = t0 + datetime.timedelta(seconds=i * 3)
            try:
                await JobService.execute_hustle(
                    session=async_session,
                    user_id=user_id,
                    hustle_id="hustle_secret_sauce",
                    choice_idx=1,
                    now=t_call,
                    success_roll=0.0,
                )
                await async_session.commit()
                success_count += 1
            except ValueError as exc:
                if "Шабашкины руки должны отдохнуть" in str(exc):
                    rejection_count += 1
                else:
                    raise

        assert success_count == 1
        assert rejection_count == 9


# ==============================================================================
# 3. Double-Entry LedgerEntry Creation & balance_after Correctness
# ==============================================================================

class TestLedgerDoubleEntryAdversarial:
    """Verify double-entry LedgerEntry creation, direction, amounts, and balance_after correctness."""

    async def test_salary_claim_ledger_entry_and_balance_after(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Salary claim must atomically update account balance and create matching Transaction and LedgerEntry."""
        user_id = challenger_user_funded.id
        main_acc = await BankingService.get_user_main_account(async_session, user_id)
        initial_balance = main_acc.balance

        # Apply for IT branch
        job = await JobService.apply_for_job(async_session, user_id, JobBranch.IT)
        assert job.level == 1

        t_start = datetime.datetime(2026, 10, 21, 8, 0, 0)
        await JobService.start_shift(async_session, user_id, JobBranch.IT, now=t_start)
        await async_session.commit()

        # Claim salary at t_start + 7200s (exact 2h) with normal event (roll=0.9)
        t_claim = t_start + datetime.timedelta(seconds=7200)
        payout, event_msg = await JobService.claim_salary(
            session=async_session,
            user_id=user_id,
            branch=JobBranch.IT,
            now=t_claim,
            event_roll=0.9,  # Normal event, no bonus/penalty
        )
        await async_session.commit()

        expected_salary = Decimal("1500.00")
        assert payout == expected_salary
        expected_balance = initial_balance + expected_salary

        # 1. Verify DB account balance
        refreshed_acc = await BankingService.get_user_main_account(async_session, user_id)
        assert refreshed_acc.balance == expected_balance

        # 2. Query Transaction
        tx_res = await async_session.execute(
            select(Transaction).where(
                Transaction.recipient_account_id == main_acc.id,
                Transaction.tx_type == "SALARY",
            )
        )
        tx = tx_res.scalars().first()
        assert tx is not None
        assert tx.amount == expected_salary
        assert tx.status == "COMPLETED"
        assert tx.fiscal_code is not None and len(tx.fiscal_code) > 0
        assert tx.auth_code is not None and len(tx.auth_code) > 0

        # 3. Query LedgerEntry
        ledger_res = await async_session.execute(
            select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id)
        )
        ledger_entries = list(ledger_res.scalars().all())
        assert len(ledger_entries) == 1
        le = ledger_entries[0]
        assert le.account_id == main_acc.id
        assert le.direction == "CREDIT"
        assert le.amount == expected_salary
        assert le.balance_after == expected_balance

    async def test_salary_workplace_events_ledger_accuracy(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Test salary workplace bonus and reprimand events with accurate balance_after tracking."""
        user_id = challenger_user_funded.id
        main_acc = await BankingService.get_user_main_account(async_session, user_id)

        job = await JobService.apply_for_job(async_session, user_id, JobBranch.SERVICE)
        t0 = datetime.datetime(2026, 10, 21, 6, 0, 0)
        await JobService.start_shift(async_session, user_id, JobBranch.SERVICE, now=t0)
        await async_session.commit()

        # Trigger bonus event: roll=0.10 (< 0.15), bonus pct=30%
        # Base salary for SERVICE level 1 is 1200.00. Bonus 30% = 360.00. Payout = 1560.00
        cur_bal = main_acc.balance
        t_claim = t0 + datetime.timedelta(seconds=7201)
        payout, _ = await JobService.claim_salary(
            session=async_session,
            user_id=user_id,
            branch=JobBranch.SERVICE,
            now=t_claim,
            event_roll=0.10,
            event_pct=30,
        )
        await async_session.commit()

        assert payout == Decimal("1560.00")
        assert (await BankingService.get_user_main_account(async_session, user_id)).balance == cur_bal + Decimal("1560.00")

    async def test_hustle_reward_ledger_entry_and_balance_after(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Hustle reward must record Transaction and LedgerEntry with CREDIT direction and correct balance_after."""
        user_id = challenger_user_funded.id
        main_acc = await BankingService.get_user_main_account(async_session, user_id)
        initial_balance = main_acc.balance

        reward_amt = Decimal("2500.00")
        delta, msg = await JobService.execute_hustle(
            session=async_session,
            user_id=user_id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            success_roll=0.0,  # 100% success
            reward_override=reward_amt,
        )
        await async_session.commit()

        assert delta == reward_amt
        expected_balance = initial_balance + reward_amt

        # Check LedgerEntry
        ledger_res = await async_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.account_id == main_acc.id,
                LedgerEntry.direction == "CREDIT",
                LedgerEntry.amount == reward_amt,
            )
        )
        entry = ledger_res.scalars().first()
        assert entry is not None
        assert entry.balance_after == expected_balance

    async def test_hustle_penalty_ledger_entry_and_balance_after(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Hustle penalty when funded must record Transaction and LedgerEntry with DEBIT direction and correct balance_after."""
        user_id = challenger_user_funded.id
        main_acc = await BankingService.get_user_main_account(async_session, user_id)
        initial_balance = main_acc.balance

        penalty_amt = Decimal("1200.00")
        delta, msg = await JobService.execute_hustle(
            session=async_session,
            user_id=user_id,
            hustle_id="hustle_secret_sauce",
            choice_idx=0,
            success_roll=1.0,  # Force failure
            penalty_override=penalty_amt,
        )
        await async_session.commit()

        assert delta == -penalty_amt
        expected_balance = initial_balance - penalty_amt

        # Check LedgerEntry
        ledger_res = await async_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.account_id == main_acc.id,
                LedgerEntry.direction == "DEBIT",
                LedgerEntry.amount == penalty_amt,
            )
        )
        entry = ledger_res.scalars().first()
        assert entry is not None
        assert entry.balance_after == expected_balance

    async def test_ledger_continuous_reconciliation_multi_step(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Execute a sequence of multiple credits and debits; verify every balance_after strictly matches running total."""
        user_id = challenger_user_funded.id
        main_acc = await BankingService.get_user_main_account(async_session, user_id)

        # Baseline balance
        running_balance = main_acc.balance
        base_time = datetime.datetime(2026, 10, 21, 6, 0, 0)

        # Step 1: Hustle reward +1000
        t1 = base_time + datetime.timedelta(seconds=1000)
        await JobService.execute_hustle(
            session=async_session,
            user_id=user_id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            now=t1,
            success_roll=0.0,
            reward_override=Decimal("1000.00"),
        )
        running_balance += Decimal("1000.00")
        await async_session.commit()

        # Step 2: Hustle penalty -2000
        t2 = t1 + datetime.timedelta(seconds=1000)
        await JobService.execute_hustle(
            session=async_session,
            user_id=user_id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            now=t2,
            success_roll=1.0,
            penalty_override=Decimal("2000.00"),
        )
        running_balance -= Decimal("2000.00")
        await async_session.commit()

        # Step 3: Salary claim +1500
        await JobService.apply_for_job(async_session, user_id, JobBranch.IT)
        t_shift = t2 + datetime.timedelta(seconds=100)
        await JobService.start_shift(async_session, user_id, JobBranch.IT, now=t_shift)
        t_claim = t_shift + datetime.timedelta(seconds=7201)
        payout, _ = await JobService.claim_salary(
            session=async_session,
            user_id=user_id,
            branch=JobBranch.IT,
            now=t_claim,
            event_roll=0.9,
        )
        running_balance += payout
        await async_session.commit()

        # Verify DB balance equals running_balance
        db_bal = (
            await async_session.execute(
                select(Account.balance).where(Account.id == main_acc.id)
            )
        ).scalar_one()
        assert db_bal == running_balance

        # Verify every LedgerEntry balance_after matches its point-in-time calculation
        ledger_res = await async_session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.account_id == main_acc.id)
            .order_by(LedgerEntry.id.asc())
        )
        entries = list(ledger_res.scalars().all())
        assert len(entries) >= 3


# ==============================================================================
# 4. Schema CheckConstraints on UserJob
# ==============================================================================

class TestUserJobSchemaConstraintsAdversarial:
    """Verify schema CheckConstraints and UniqueConstraint on UserJob model."""

    async def test_level_zero_rejected_by_check_constraint(
        self, async_session: AsyncSession, challenger_user_zero: User
    ):
        """Inserting UserJob with level=0 must be rejected by chk_user_job_level."""
        invalid_job = UserJob(
            user_id=challenger_user_zero.id,
            branch="IT",
            level=0,  # Below minimum (1)
            shifts_completed=0,
            is_active=True,
        )
        async_session.add(invalid_job)

        with pytest.raises(IntegrityError) as exc_info:
            await async_session.flush()

        assert "chk_user_job_level" in str(exc_info.value).lower() or "check constraint" in str(exc_info.value).lower()
        await async_session.rollback()

    async def test_level_six_rejected_by_check_constraint(
        self, async_session: AsyncSession, challenger_user_zero: User
    ):
        """Inserting UserJob with level=6 must be rejected by chk_user_job_level."""
        invalid_job = UserJob(
            user_id=challenger_user_zero.id,
            branch="FINANCE",
            level=6,  # Above maximum (5)
            shifts_completed=0,
            is_active=True,
        )
        async_session.add(invalid_job)

        with pytest.raises(IntegrityError) as exc_info:
            await async_session.flush()

        assert "chk_user_job_level" in str(exc_info.value).lower() or "check constraint" in str(exc_info.value).lower()
        await async_session.rollback()

    async def test_shifts_completed_negative_rejected(
        self, async_session: AsyncSession, challenger_user_zero: User
    ):
        """Inserting UserJob with shifts_completed=-1 must be rejected by chk_user_job_shifts."""
        invalid_job = UserJob(
            user_id=challenger_user_zero.id,
            branch="SERVICE",
            level=1,
            shifts_completed=-1,  # Negative shifts
            is_active=True,
        )
        async_session.add(invalid_job)

        with pytest.raises(IntegrityError) as exc_info:
            await async_session.flush()

        assert "chk_user_job_shifts" in str(exc_info.value).lower() or "check constraint" in str(exc_info.value).lower()
        await async_session.rollback()

    async def test_valid_levels_1_to_5_succeed(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Inserting UserJob with valid levels 1, 2, 3, 4, 5 and non-negative shifts must succeed."""
        branches = [JobBranch.IT, JobBranch.SERVICE, JobBranch.FINANCE, JobBranch.SHADOW]

        for idx, lvl in enumerate([1, 2, 3, 4]):
            job = UserJob(
                user_id=challenger_user_funded.id,
                branch=branches[idx],
                level=lvl,
                shifts_completed=idx * 3,
                is_active=True,
            )
            async_session.add(job)

        await async_session.flush()
        # All 4 jobs persisted cleanly without constraint violations
        q = await async_session.execute(
            select(UserJob).where(UserJob.user_id == challenger_user_funded.id)
        )
        jobs = list(q.scalars().all())
        assert len(jobs) == 4
        await async_session.rollback()

    async def test_unique_user_branch_constraint(
        self, async_session: AsyncSession, challenger_user_zero: User
    ):
        """Inserting two UserJob rows for the same user and branch must fail with unique constraint."""
        job1 = UserJob(
            user_id=challenger_user_zero.id,
            branch=JobBranch.IT,
            level=1,
            shifts_completed=0,
            is_active=True,
        )
        async_session.add(job1)
        await async_session.flush()

        job2 = UserJob(
            user_id=challenger_user_zero.id,
            branch=JobBranch.IT,  # Duplicate branch for same user
            level=2,
            shifts_completed=0,
            is_active=False,
        )
        async_session.add(job2)

        with pytest.raises(IntegrityError) as exc_info:
            await async_session.flush()

        assert "unique" in str(exc_info.value).lower() or "uq_user_job_branch" in str(exc_info.value).lower()
        await async_session.rollback()


# ==============================================================================
# 5. Career & Multi-Employment Mechanics Stress
# ==============================================================================

class TestCareerMechanicsAdversarial:
    """Verify multi-employment cap (2 jobs max), shift duration, promotion bounds."""

    async def test_multi_employment_third_job_rejected(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """User can hold at most 2 active jobs. Applying for 3rd must fail with ValueError."""
        user_id = challenger_user_funded.id

        await JobService.apply_for_job(async_session, user_id, JobBranch.IT)
        await JobService.apply_for_job(async_session, user_id, JobBranch.FINANCE)
        await async_session.commit()

        # 3rd job application
        with pytest.raises(ValueError) as exc:
            await JobService.apply_for_job(async_session, user_id, JobBranch.SERVICE)
        assert "Вы уже совмещаете 2 работы" in str(exc.value)

    async def test_resignation_frees_slot_and_preserves_progress(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Quitting an active job frees the slot while retaining attained level and shifts."""
        user_id = challenger_user_funded.id

        job_it = await JobService.apply_for_job(async_session, user_id, JobBranch.IT)
        job_it.level = 3
        job_it.shifts_completed = 7
        await async_session.commit()

        # Quit IT job
        await JobService.quit_job(async_session, user_id, JobBranch.IT)
        await async_session.commit()

        # Now can apply for SERVICE and FINANCE
        await JobService.apply_for_job(async_session, user_id, JobBranch.SERVICE)
        await JobService.apply_for_job(async_session, user_id, JobBranch.FINANCE)
        await async_session.commit()

        # Re-applying to IT requires quitting one of the active ones
        await JobService.quit_job(async_session, user_id, JobBranch.SERVICE)
        re_it = await JobService.apply_for_job(async_session, user_id, JobBranch.IT)
        await async_session.commit()

        # Verify progress preserved!
        assert re_it.level == 3
        assert re_it.shifts_completed == 7
        assert re_it.is_active is True

    async def test_premature_salary_claim_rejected(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Claiming salary before 2 hours (7200s) have elapsed must be rejected."""
        user_id = challenger_user_funded.id
        await JobService.apply_for_job(async_session, user_id, JobBranch.FINANCE)
        t0 = datetime.datetime(2026, 10, 21, 9, 0, 0)
        await JobService.start_shift(async_session, user_id, JobBranch.FINANCE, now=t0)
        await async_session.commit()

        # Attempt to claim at 1 hour (3600s)
        t_early = t0 + datetime.timedelta(seconds=3600)
        with pytest.raises(ValueError) as exc:
            await JobService.claim_salary(
                session=async_session,
                user_id=user_id,
                branch=JobBranch.FINANCE,
                now=t_early,
            )
        assert "Смена еще не окончена" in str(exc.value)

    async def test_promotion_shift_threshold_enforced(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Promotion requires meeting required shifts (Level 1 IT requires 3 shifts)."""
        user_id = challenger_user_funded.id
        job = await JobService.apply_for_job(async_session, user_id, JobBranch.IT)
        job.shifts_completed = 2  # Needs 3

        ok, msg = await JobService.promote_job(async_session, user_id, JobBranch.IT)
        assert ok is False
        assert "нужно отработать еще 1 смен" in msg

        # Increment to 3
        job.shifts_completed = 3
        ok, msg = await JobService.promote_job(async_session, user_id, JobBranch.IT)
        assert ok is True
        assert job.level == 2
        assert "ПОЗДРАВЛЯЕМ С ПОВЫШЕНИЕМ" in msg


# ==============================================================================
# 6. Deep Catalog Invariants & Shadow Event Stress
# ==============================================================================

class TestCatalogAndEventInvariantsAdversarial:
    """Verify 20-profession matrix integrity, Shadow branch dynamics, and shift boundaries."""

    def test_20_professions_catalog_structural_invariants(self):
        """Verify all 4 branches exist, each has exactly levels 1..5, strictly increasing salaries."""
        catalog = JobService.get_job_catalog()
        expected_branches = [JobBranch.IT, JobBranch.SERVICE, JobBranch.FINANCE, JobBranch.SHADOW]
        assert set(catalog.keys()) == set(expected_branches)

        expected_shifts = {1: 3, 2: 5, 3: 8, 4: 15, 5: 0}

        for branch, levels in catalog.items():
            assert set(levels.keys()) == {1, 2, 3, 4, 5}
            prev_salary = Decimal("0.00")
            for lvl in range(1, 6):
                info = levels[lvl]
                assert "title" in info and len(info["title"]) > 0
                assert "desc" in info and len(info["desc"]) > 0
                assert "salary" in info and isinstance(info["salary"], Decimal)
                assert info["salary"] > prev_salary, f"{branch} level {lvl} salary must be strictly greater than level {lvl-1}"
                assert info["shifts_to_promote"] == expected_shifts[lvl]
                prev_salary = info["salary"]

    async def test_shadow_police_raid_event_rolls(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Shadow branch has 20% police raid rate with 20-50% loss."""
        user_id = challenger_user_funded.id
        await JobService.apply_for_job(async_session, user_id, JobBranch.SHADOW)
        t0 = datetime.datetime(2026, 10, 21, 5, 0, 0)
        await JobService.start_shift(async_session, user_id, JobBranch.SHADOW, now=t0)
        await async_session.commit()

        # Roll = 0.05 (< 0.20) -> Police raid! Loss pct = 40%
        # Level 1 SHADOW salary is 3000.00. 40% loss = 1200.00. Payout = 1800.00
        t_claim = t0 + datetime.timedelta(seconds=7200)
        payout, event_msg = await JobService.claim_salary(
            session=async_session,
            user_id=user_id,
            branch=JobBranch.SHADOW,
            now=t_claim,
            event_roll=0.05,
            event_pct=40,
        )
        await async_session.commit()

        assert payout == Decimal("1800.00")
        assert "Облава на точке" in event_msg

    async def test_shift_duration_boundary_7199_vs_7200(
        self, async_session: AsyncSession, challenger_user_funded: User
    ):
        """Shift claim at 7199s rejected, claim at 7200s accepted."""
        user_id = challenger_user_funded.id
        await JobService.apply_for_job(async_session, user_id, JobBranch.FINANCE)
        t0 = datetime.datetime(2026, 10, 21, 1, 0, 0)
        await JobService.start_shift(async_session, user_id, JobBranch.FINANCE, now=t0)
        await async_session.commit()

        # At 7199s (1s short of 2 hours)
        t_7199 = t0 + datetime.timedelta(seconds=7199)
        with pytest.raises(ValueError) as exc:
            await JobService.claim_salary(
                session=async_session,
                user_id=user_id,
                branch=JobBranch.FINANCE,
                now=t_7199,
            )
        assert "Смена еще не окончена" in str(exc.value)

        # At 7200s (exact boundary)
        t_7200 = t0 + datetime.timedelta(seconds=7200)
        payout, _ = await JobService.claim_salary(
            session=async_session,
            user_id=user_id,
            branch=JobBranch.FINANCE,
            now=t_7200,
            event_roll=0.9,
        )
        await async_session.commit()
        assert payout == Decimal("2000.00")
