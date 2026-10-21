"""Empirical Challenger Test Suite for Milestone 7: Jobs & Career Progression.

Adversarial verification and empirical rigor testing:
1. Boundary Conditions:
   - Concurrent jobs capacity: 0 jobs -> 1 job -> 2 jobs -> 3rd job strictly rejected (MAX_ACTIVE_JOBS = 2) -> quit frees slot.
   - Shift duration boundary: 7199s (premature rejection) vs 7200s (exact threshold acceptance) vs 7201s (excess duration acceptance).
   - Hustle cooldown boundary: 899s (premature rejection) vs 900s (exact threshold acceptance) vs 901s (excess duration acceptance).
   - Promotion shift requirements: 0 shifts, exact required shifts, excess shifts, and Level 5 MAX tier capping.
   - Balance non-negative clamping: exactly 0.00 ₽, partial balance (< penalty), and sufficient balance (>= penalty).
2. Mutation & Assertion Rigor of Worker Test Suite (tests/test_jobs_career.py):
   - Mutant 1: 3rd job permitted -> worker test must fail.
   - Mutant 2: Shift cooldown skipped -> worker test must fail.
   - Mutant 3: Hustle cooldown skipped -> worker test must fail.
   - Mutant 4: Balance clamping disabled -> worker test must fail.
   - Mutant 5: Promotion requirements bypassed -> worker test must fail.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Account, LedgerEntry, Transaction, User, UserJob
from app.services.banking import BankingService
from app.services.jobs import JobBranch, JobService
import tests.test_jobs_career as worker_module


# ==============================================================================
# Suite 1: Boundary Conditions Verification
# ==============================================================================

class TestJobsBoundaryConditions:
    """Rigorous empirical validation of mathematical and state boundaries."""

    @pytest.mark.asyncio
    async def test_boundary_concurrent_jobs_0_1_2_3(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Boundary test: 0 jobs, 1 job, 2 jobs (capacity), and 3 jobs (rejection)."""
        # 1. Boundary: 0 active jobs
        active_jobs = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(active_jobs) == 0

        # 2. Boundary: 1 active job
        job_it = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        assert job_it.is_active is True
        active_jobs = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(active_jobs) == 1

        # 3. Boundary: 2 active jobs (exact capacity limit)
        job_service = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SERVICE)
        assert job_service.is_active is True
        active_jobs = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(active_jobs) == 2

        # 4. Boundary: 3 active jobs (strictly prohibited)
        with pytest.raises(ValueError, match="совмещаете 2 работы"):
            await JobService.apply_for_job(async_session, sample_user.id, JobBranch.FINANCE)

        with pytest.raises(ValueError, match="совмещаете 2 работы"):
            await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SHADOW)

        # Confirm count remains exactly 2
        active_jobs = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(active_jobs) == 2

        # 5. Quit 1 job -> frees slot (count = 1)
        await JobService.quit_job(async_session, sample_user.id, JobBranch.IT)
        active_jobs = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(active_jobs) == 1

        # 6. Apply for previously blocked 3rd branch -> now succeeds (count = 2)
        job_fin = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.FINANCE)
        assert job_fin.is_active is True
        active_jobs = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(active_jobs) == 2

        # 7. Quit remaining 2 jobs -> back to 0 active jobs
        await JobService.quit_job(async_session, sample_user.id, JobBranch.SERVICE)
        await JobService.quit_job(async_session, sample_user.id, JobBranch.FINANCE)
        active_jobs = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(active_jobs) == 0

    @pytest.mark.asyncio
    async def test_boundary_shift_cooldown_7199s_vs_7200s_vs_7201s(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Boundary test: 7199s (rejected) vs 7200s (exact threshold accepted) vs 7201s (accepted)."""
        job = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        t0 = datetime.datetime(2026, 10, 21, 10, 0, 0)
        await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=t0)

        # Boundary A: 7199 seconds (1 second short of 2 hours) -> MUST FAIL
        t_7199 = t0 + datetime.timedelta(seconds=7199)
        with pytest.raises(ValueError, match="Смена еще не окончена! Осталось работать еще примерно 1 мин"):
            await JobService.claim_salary(async_session, sample_user.id, JobBranch.IT, now=t_7199)

        # Confirm shift is still active and 0 completed shifts
        await async_session.refresh(job)
        assert job.is_on_shift is True
        assert job.shifts_completed == 0

        # Boundary B: 7200 seconds (exact 2-hour threshold) -> MUST SUCCEED
        t_7200 = t0 + datetime.timedelta(seconds=7200)
        payout, msg = await JobService.claim_salary(
            async_session, sample_user.id, JobBranch.IT, now=t_7200, event_roll=0.5
        )
        assert payout == Decimal("1500.00")
        assert "Смена отработана на отлично" in msg

        # Confirm shift finished and 1 completed shift
        await async_session.refresh(job)
        assert job.is_on_shift is False
        assert job.shifts_completed == 1
        assert job.last_shift_completed_at == t_7200

        # Boundary C: Second shift at t1, claim at 7201 seconds (1 second over) -> MUST SUCCEED
        t1 = datetime.datetime(2026, 10, 21, 14, 0, 0)
        await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=t1)
        t_7201 = t1 + datetime.timedelta(seconds=7201)
        payout2, _ = await JobService.claim_salary(
            async_session, sample_user.id, JobBranch.IT, now=t_7201, event_roll=0.5
        )
        assert payout2 == Decimal("1500.00")
        await async_session.refresh(job)
        assert job.is_on_shift is False
        assert job.shifts_completed == 2

    @pytest.mark.asyncio
    async def test_boundary_hustle_cooldown_899s_vs_900s_vs_901s(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Boundary test: 899s (rejected) vs 900s (exact threshold accepted) vs 901s (accepted)."""
        t0 = datetime.datetime(2026, 10, 21, 12, 0, 0)

        # Initial hustle at t0 -> succeeds
        delta1, _ = await JobService.execute_hustle(
            async_session,
            sample_user.id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            now=t0,
            success_roll=0.1,
            reward_override=Decimal("1000.00"),
        )
        assert delta1 == Decimal("1000.00")

        # Boundary A: 899 seconds (1 second short of 15 minutes / 900s) -> MUST FAIL
        t_899 = t0 + datetime.timedelta(seconds=899)
        with pytest.raises(ValueError, match="Шабашкины руки должны отдохнуть! До следующей подработки: ~1 мин"):
            await JobService.execute_hustle(
                async_session,
                sample_user.id,
                hustle_id="hustle_server_crash",
                choice_idx=0,
                now=t_899,
            )

        # Boundary B: 900 seconds (exact 15 minutes threshold) -> MUST SUCCEED
        t_900 = t0 + datetime.timedelta(seconds=900)
        delta2, _ = await JobService.execute_hustle(
            async_session,
            sample_user.id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            now=t_900,
            success_roll=0.1,
            reward_override=Decimal("1500.00"),
        )
        assert delta2 == Decimal("1500.00")

        # Boundary C: 901 seconds after 2nd hustle -> MUST SUCCEED
        t_901 = t_900 + datetime.timedelta(seconds=901)
        delta3, _ = await JobService.execute_hustle(
            async_session,
            sample_user.id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            now=t_901,
            success_roll=0.1,
            reward_override=Decimal("2000.00"),
        )
        assert delta3 == Decimal("2000.00")

    @pytest.mark.asyncio
    async def test_boundary_promotion_shifts_0_exact_excess_max(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Boundary test: 0 completed shifts, exact required shifts, excess shifts, and Level 5 Max."""
        job = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SERVICE)
        assert job.level == 1  # Required shifts to promote to L2 = 3

        # 1. Boundary: 0 completed shifts -> MUST FAIL
        job.shifts_completed = 0
        await async_session.flush()
        ok, msg = await JobService.promote_job(async_session, sample_user.id, JobBranch.SERVICE)
        assert ok is False
        assert "нужно отработать еще 3 смен(ы)" in msg
        assert job.level == 1

        # 2. Boundary: 2 completed shifts (1 short of 3) -> MUST FAIL
        job.shifts_completed = 2
        await async_session.flush()
        ok, msg = await JobService.promote_job(async_session, sample_user.id, JobBranch.SERVICE)
        assert ok is False
        assert "нужно отработать еще 1 смен(ы)" in msg
        assert job.level == 1

        # 3. Boundary: exactly 3 completed shifts (exact requirement) -> MUST SUCCEED
        job.shifts_completed = 3
        await async_session.flush()
        ok, msg = await JobService.promote_job(async_session, sample_user.id, JobBranch.SERVICE)
        assert ok is True
        assert job.level == 2
        assert "Курьер на электросамокате" in msg

        # 4. Boundary: excess completed shifts
        # Level 2 requires 5 shifts to promote to Level 3.
        # User has worked 10 shifts (excess of 5).
        job.shifts_completed = 10
        await async_session.flush()
        ok, msg = await JobService.promote_job(async_session, sample_user.id, JobBranch.SERVICE)
        assert ok is True
        assert job.level == 3
        assert "Мастер шаурмы на вокзале" in msg

        # Advance to Level 5 Max tier
        job.level = 5
        job.shifts_completed = 99
        await async_session.flush()

        # 5. Boundary: Level 5 Max tier (shifts_to_promote == 0) -> MUST BE REJECTED
        ok, msg = await JobService.promote_job(async_session, sample_user.id, JobBranch.SERVICE)
        assert ok is False
        assert "максимальной должности" in msg
        assert job.level == 5

    @pytest.mark.asyncio
    async def test_boundary_balance_clamping_zero_and_partial(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Boundary test: non-negative clamping with 0.00 ₽ balance, 75.50 ₽ balance, and full debit."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)

        # 1. Exactly 0.00 ₽ balance
        main_acc.balance = Decimal("0.00")
        await async_session.commit()

        delta, msg = await JobService.execute_hustle(
            async_session,
            sample_user.id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            cooldown_seconds=0,
            success_roll=1.0,  # Force failure
            penalty_override=Decimal("3500.00"),
        )
        assert delta == Decimal("0.00")
        await async_session.refresh(main_acc)
        assert main_acc.balance == Decimal("0.00")

        # 2. Partial balance: 75.50 ₽ balance vs 3,000.00 ₽ penalty
        main_acc.balance = Decimal("75.50")
        await async_session.commit()

        delta2, _ = await JobService.execute_hustle(
            async_session,
            sample_user.id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            cooldown_seconds=0,
            success_roll=1.0,  # Force failure
            penalty_override=Decimal("3000.00"),
        )
        assert delta2 == Decimal("-75.50")
        await async_session.refresh(main_acc)
        assert main_acc.balance == Decimal("0.00")

        # 3. Sufficient balance: 5,000.00 ₽ balance vs 1,200.00 ₽ penalty
        main_acc.balance = Decimal("5000.00")
        await async_session.commit()

        delta3, _ = await JobService.execute_hustle(
            async_session,
            sample_user.id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            cooldown_seconds=0,
            success_roll=1.0,  # Force failure
            penalty_override=Decimal("1200.00"),
        )
        assert delta3 == Decimal("-1200.00")
        await async_session.refresh(main_acc)
        assert main_acc.balance == Decimal("3800.00")


# ==============================================================================
# Suite 2: Mutation & Assertion Rigor of Worker Tests
# ==============================================================================

class TestJobsCareerMutationRigor:
    """Empirically prove that tests/test_jobs_career.py assertions strictly catch broken business logic."""

    @pytest.mark.asyncio
    async def test_mutation_permitting_third_job_kills_worker_test(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Mutation 1: If 3rd job is permitted, worker's test_apply_third_job_rejected must fail."""
        worker_suite = worker_module.TestMultiEmploymentAndResignation()

        # Under normal conditions, worker test passes
        await worker_suite.test_apply_third_job_rejected(async_session, sample_user)

        # Mutate: allow 3 concurrent jobs
        with patch.object(JobService, "MAX_ACTIVE_JOBS", 3):
            # Clean up user's jobs for clean mutated run
            all_jobs = await JobService.get_user_all_jobs(async_session, sample_user.id)
            for j in all_jobs:
                await async_session.delete(j)
            await async_session.commit()

            # The worker test must fail because it expects ValueError on 3rd job
            with pytest.raises((AssertionError, pytest.fail.Exception)):
                await worker_suite.test_apply_third_job_rejected(async_session, sample_user)

    @pytest.mark.asyncio
    async def test_mutation_skipping_shift_cooldown_kills_worker_test(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Mutation 2: If shift cooldown is skipped, worker's test_premature_shift_claim_rejected must fail."""
        worker_suite = worker_module.TestShiftLifecycleAndLedger()

        # Under normal conditions, worker test passes
        await worker_suite.test_premature_shift_claim_rejected(async_session, sample_user)

        # Mutate: set shift duration to 0 seconds (cooldown skipped)
        with patch.object(JobService, "DEFAULT_SHIFT_DURATION_SECONDS", 0):
            # Clean up user's jobs
            all_jobs = await JobService.get_user_all_jobs(async_session, sample_user.id)
            for j in all_jobs:
                await async_session.delete(j)
            await async_session.commit()

            # The worker test must fail because claim at t_almost won't raise ValueError
            with pytest.raises((AssertionError, pytest.fail.Exception)):
                await worker_suite.test_premature_shift_claim_rejected(async_session, sample_user)

    @pytest.mark.asyncio
    async def test_mutation_skipping_hustle_cooldown_kills_worker_test(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Mutation 3: If hustle cooldown is skipped, worker's test_hustle_cooldown_and_execution must fail."""
        worker_suite = worker_module.TestQuickHustles()

        # Under normal conditions, worker test passes
        await worker_suite.test_hustle_cooldown_and_execution(async_session, sample_user)

        # Mutate: set hustle cooldown to 0 seconds
        with patch.object(JobService, "DEFAULT_HUSTLE_COOLDOWN_SECONDS", 0):
            # Reset user last_hustle_at
            sample_user.last_hustle_at = None
            await async_session.commit()

            # The worker test must fail because t_early won't raise ValueError
            with pytest.raises((AssertionError, pytest.fail.Exception)):
                await worker_suite.test_hustle_cooldown_and_execution(async_session, sample_user)

    @pytest.mark.asyncio
    async def test_mutation_bypassing_balance_clamping_kills_worker_test(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Mutation 4: If balance clamping is broken, worker's test_hustle_non_negative_balance_clamping must fail."""
        worker_suite = worker_module.TestQuickHustles()

        # Under normal conditions, worker test passes
        await worker_suite.test_hustle_non_negative_balance_clamping(async_session, sample_user)

        # Mutate execute_hustle to bypass clamping and return raw penalty
        orig_execute = JobService.execute_hustle

        async def broken_execute_hustle(*args, **kwargs):
            # Mutated: if penalty_override is provided, delta is un-clamped raw penalty
            if kwargs.get("penalty_override"):
                kwargs["penalty_override"] = kwargs["penalty_override"]
            delta, msg = await orig_execute(*args, **kwargs)
            # Forge unclamped delta
            if kwargs.get("penalty_override"):
                return -kwargs["penalty_override"], msg
            return delta, msg

        with patch.object(JobService, "execute_hustle", side_effect=broken_execute_hustle):
            # The worker test asserts delta == Decimal("-500.00")
            # With un-clamped delta = -2000.00, assertion must fail
            with pytest.raises(AssertionError):
                await worker_suite.test_hustle_non_negative_balance_clamping(async_session, sample_user)

    @pytest.mark.asyncio
    async def test_mutation_bypassing_promotion_requirements_kills_worker_test(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Mutation 5: If promotion requirement is bypassed, worker's test_promotion_requirements_progression must fail."""
        worker_suite = worker_module.TestCareerPromotionLifecycle()

        # Under normal conditions, worker test passes
        # Clean up user's jobs first
        all_jobs = await JobService.get_user_all_jobs(async_session, sample_user.id)
        for j in all_jobs:
            await async_session.delete(j)
        await async_session.commit()

        await worker_suite.test_promotion_requirements_progression(async_session, sample_user)

        # Mutate promote_job to always return True (bypassing shift check)
        orig_promote = JobService.promote_job

        async def broken_promote_job(session, user_id, branch):
            job = await JobService.get_user_job_by_branch(session, user_id, branch)
            job.level += 1
            await session.flush()
            return True, "ПОВЫШЕНИЕ БЕЗ СМЕН"

        with patch.object(JobService, "promote_job", side_effect=broken_promote_job):
            # Clean up user's jobs
            all_jobs = await JobService.get_user_all_jobs(async_session, sample_user.id)
            for j in all_jobs:
                await async_session.delete(j)
            await async_session.commit()

            # The worker test asserts ok is False with 2 shifts; with broken promotion it returns True
            with pytest.raises(AssertionError):
                await worker_suite.test_promotion_requirements_progression(async_session, sample_user)
