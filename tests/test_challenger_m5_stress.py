"""Adversarial stress and invariant verification test suite for JobService and UserJob models.

Milestone 5 Empirical Challenge.
Verifies:
1. Catalog invariants (4 branches, 20 professions, strictly increasing salaries, promotion shifts).
2. Multi-employment stress testing (max 2 active jobs, 3rd strictly rejected, duplicate branch rejection).
3. Resignation and re-hiring progress retention across all branches and job-swap cycles.
4. Shift timers, cooldowns, premature claim rejection, atomic salary crediting and double-entry ledger.
5. Promotion progression across all 5 tiers and max level guard.
6. Quick hustles cooldown, rewards, and non-negative balance clamping under extreme penalties.
7. Database schema constraints (CheckConstraints on level 1..5, shifts >= 0, and unique branch per user).
"""

import asyncio
import datetime
import random
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Account, LedgerEntry, Transaction, User, UserJob
from app.services.banking import BankingService
from app.services.jobs import (
    HUSTLE_CATALOG,
    JOB_CATALOG,
    JobBranch,
    JobService,
)


class TestCatalogInvariants:
    """Stress-test catalog data structures, boundaries, and validation guards."""

    def test_branch_definitions(self):
        """Verify all 4 required branches are defined with proper codes and titles."""
        expected_branches = ["IT", "SERVICE", "FINANCE", "SHADOW"]
        assert sorted(JobBranch.ALL) == sorted(expected_branches)
        for b in expected_branches:
            title = JobBranch.get_title(b)
            assert isinstance(title, str) and len(title) > 0
            assert title != b  # Title must include emoji/friendly text

        assert JobBranch.get_title("UNKNOWN_BRANCH") == "UNKNOWN_BRANCH"

    def test_20_professions_matrix_completeness(self):
        """Verify exactly 4 branches, 5 levels each = 20 professions with valid schemas."""
        catalog = JobService.get_job_catalog()
        assert len(catalog) == 4

        for branch in JobBranch.ALL:
            assert branch in catalog, f"Branch {branch} missing from JOB_CATALOG"
            levels = catalog[branch]
            assert len(levels) == 5, f"Branch {branch} has {len(levels)} levels instead of 5"

            prev_salary = Decimal("0.00")
            for lvl in range(1, 6):
                assert lvl in levels, f"Level {lvl} missing in {branch}"
                info = levels[lvl]

                # Title
                assert "title" in info and isinstance(info["title"], str) and len(info["title"]) > 3
                # Salary
                assert "salary" in info and isinstance(info["salary"], Decimal)
                assert info["salary"] > Decimal("0.00")
                # Invariant: Salary strictly increases with level
                assert info["salary"] > prev_salary, (
                    f"Salary did not increase at {branch} L{lvl}: {info['salary']} <= {prev_salary}"
                )
                prev_salary = info["salary"]

                # Promotion shift requirements
                assert "shifts_to_promote" in info
                req = info["shifts_to_promote"]
                if lvl < 5:
                    assert req > 0, f"Level {lvl} must require >0 shifts for promotion"
                else:
                    assert req == 0, "Level 5 must have 0 shifts to promote (max tier)"

                # Description
                assert "desc" in info and isinstance(info["desc"], str) and len(info["desc"]) > 5

    def test_catalog_rejection_of_invalid_queries(self):
        """Verify that get_job_info rejects invalid branches or invalid levels."""
        with pytest.raises(ValueError, match="Неизвестная ветка"):
            JobService.get_job_info("MAGIC", 1)

        with pytest.raises(ValueError, match="Неизвестный уровень"):
            JobService.get_job_info(JobBranch.IT, 0)

        with pytest.raises(ValueError, match="Неизвестный уровень"):
            JobService.get_job_info(JobBranch.IT, 6)

        with pytest.raises(ValueError, match="Неизвестный уровень"):
            JobService.get_job_info(JobBranch.IT, -1)

    def test_hustle_catalog_structure(self):
        """Verify hustle catalog contains 4 dilemmas with valid options and probabilities."""
        hustles = JobService.get_hustle_catalog()
        assert len(hustles) == 4
        seen_ids = set()
        for h in hustles:
            assert h["id"] not in seen_ids
            seen_ids.add(h["id"])
            assert h["branch"] in JobBranch.ALL
            assert len(h["options"]) == 2
            for opt in h["options"]:
                assert 0.0 < opt["success_rate"] <= 1.0
                assert opt["reward_min"] >= 0
                assert opt["reward_max"] >= opt["reward_min"]
                assert opt["penalty_min"] >= 0
                assert opt["penalty_max"] >= opt["penalty_min"]
                assert "{amt}" in opt["success_msg"] or opt["reward_min"] == 0


@pytest.mark.asyncio
class TestMultiEmploymentStress:
    """Stress-test multi-employment cap (max 2 active jobs) under sequential and concurrent loads."""

    async def test_strictly_max_two_active_jobs(self, async_session: AsyncSession, sample_user: User):
        """Verify standard 1st and 2nd jobs succeed, but 3rd and 4th applications fail."""
        # 1. First job in IT
        job1 = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        assert job1.branch == JobBranch.IT
        assert job1.is_active is True
        assert job1.level == 1

        active = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(active) == 1

        # 2. Second job in SERVICE
        job2 = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SERVICE)
        assert job2.branch == JobBranch.SERVICE
        assert job2.is_active is True

        active = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(active) == 2

        # 3. Third job in FINANCE must be strictly rejected
        with pytest.raises(ValueError, match="Вы уже совмещаете 2 работы"):
            await JobService.apply_for_job(async_session, sample_user.id, JobBranch.FINANCE)

        # 4. Fourth job in SHADOW must also be strictly rejected
        with pytest.raises(ValueError, match="Вы уже совмещаете 2 работы"):
            await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SHADOW)

        # Invariant: active count remains exactly 2
        active = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(active) == 2

    async def test_duplicate_active_branch_rejected(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify applying for an already active branch is rejected with friendly error."""
        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)

        with pytest.raises(ValueError, match="Вы уже устроены в ветке"):
            await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)

    async def test_sequential_stress_100_applications_and_resignations(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Run 100 sequential job applications and resignations, ensuring active count <= 2 always."""
        branches = [JobBranch.IT, JobBranch.SERVICE, JobBranch.FINANCE, JobBranch.SHADOW]

        for step in range(100):
            active_jobs = await JobService.get_user_active_jobs(async_session, sample_user.id)
            active_branches = [j.branch for j in active_jobs]
            assert len(active_jobs) <= 2, f"Step {step}: Invariant violated! Found {len(active_jobs)} active jobs"

            action = random.choice(["apply", "quit"])
            target_branch = random.choice(branches)

            if action == "apply":
                if len(active_jobs) >= 2:
                    with pytest.raises(ValueError):
                        await JobService.apply_for_job(async_session, sample_user.id, target_branch)
                elif target_branch in active_branches:
                    with pytest.raises(ValueError):
                        await JobService.apply_for_job(async_session, sample_user.id, target_branch)
                else:
                    job = await JobService.apply_for_job(async_session, sample_user.id, target_branch)
                    assert job.is_active is True
            else:
                if target_branch in active_branches:
                    job = await JobService.quit_job(async_session, sample_user.id, target_branch)
                    assert job.is_active is False
                else:
                    with pytest.raises(ValueError):
                        await JobService.quit_job(async_session, sample_user.id, target_branch)

            active_now = await JobService.get_user_active_jobs(async_session, sample_user.id)
            assert len(active_now) <= 2


@pytest.mark.asyncio
class TestResignationAndProgressRetentionStress:
    """Stress-test career progress retention across quit, swap, and re-hire lifecycles."""

    async def test_career_progress_retained_across_all_branches(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Simulate hiring, working multiple shifts, promotion, resignation, and re-hiring."""
        branches = [JobBranch.IT, JobBranch.SERVICE, JobBranch.FINANCE, JobBranch.SHADOW]

        for branch in branches:
            # 1. Apply
            job = await JobService.apply_for_job(async_session, sample_user.id, branch)
            assert job.level == 1
            assert job.shifts_completed == 0

            # 2. Advance career: simulate 4 completed shifts and promote to level 2
            job.shifts_completed = 4
            job.level = 2
            await async_session.flush()

            # 3. Start a shift to verify quit clears shift state
            await JobService.start_shift(async_session, sample_user.id, branch)
            assert job.is_on_shift is True
            assert job.shift_started_at is not None

            # 4. Resign from job
            resigned = await JobService.quit_job(async_session, sample_user.id, branch)
            assert resigned.is_active is False
            assert resigned.is_on_shift is False
            assert resigned.shift_started_at is None
            assert resigned.level == 2
            assert resigned.shifts_completed == 4

            # Verify no active jobs
            active = await JobService.get_user_active_jobs(async_session, sample_user.id)
            assert len(active) == 0

            # 5. Re-hire in the same branch
            re_hired = await JobService.apply_for_job(async_session, sample_user.id, branch)
            assert re_hired.id == resigned.id
            assert re_hired.is_active is True
            assert re_hired.is_on_shift is False
            assert re_hired.shift_started_at is None
            # CRITICAL INVARIANT: level and completed shifts MUST be preserved
            assert re_hired.level == 2
            assert re_hired.shifts_completed == 4

            # Clean up for next branch iteration
            await JobService.quit_job(async_session, sample_user.id, branch)

    async def test_job_swap_and_cross_branch_progress_retention(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Simulate swapping between 3 different branches while verifying all states persist intact."""
        # Hire IT and SERVICE
        job_it = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        job_it.level = 3
        job_it.shifts_completed = 7
        job_srv = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SERVICE)
        job_srv.level = 2
        job_srv.shifts_completed = 4
        await async_session.flush()

        # Resign IT to make room for FINANCE
        await JobService.quit_job(async_session, sample_user.id, JobBranch.IT)
        active = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(active) == 1
        assert active[0].branch == JobBranch.SERVICE

        # Hire FINANCE
        job_fin = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.FINANCE)
        assert job_fin.level == 1
        job_fin.level = 4
        job_fin.shifts_completed = 12
        await async_session.flush()

        # Resign SERVICE to re-hire IT
        await JobService.quit_job(async_session, sample_user.id, JobBranch.SERVICE)
        re_it = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        assert re_it.level == 3
        assert re_it.shifts_completed == 7

        # Resign FINANCE to re-hire SERVICE
        await JobService.quit_job(async_session, sample_user.id, JobBranch.FINANCE)
        re_srv = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SERVICE)
        assert re_srv.level == 2
        assert re_srv.shifts_completed == 4

    async def test_quit_unemployed_or_inactive_job_fails(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify quitting a job never held or already quit raises ValueError."""
        with pytest.raises(ValueError, match="Вы не устроены"):
            await JobService.quit_job(async_session, sample_user.id, JobBranch.IT)

        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        await JobService.quit_job(async_session, sample_user.id, JobBranch.IT)

        # Quitting second time
        with pytest.raises(ValueError, match="Вы не устроены"):
            await JobService.quit_job(async_session, sample_user.id, JobBranch.IT)


@pytest.mark.asyncio
class TestShiftTimersAndSalaryClaimStress:
    """Stress-test shift lifecycle, duration timers, premature rejection, and atomic ledger."""

    async def test_cannot_start_shift_if_unemployed_or_inactive(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify start_shift requires active employment."""
        with pytest.raises(ValueError, match="Сначала устройтесь"):
            await JobService.start_shift(async_session, sample_user.id, JobBranch.IT)

        job = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        await JobService.quit_job(async_session, sample_user.id, JobBranch.IT)

        with pytest.raises(ValueError, match="Сначала устройтесь"):
            await JobService.start_shift(async_session, sample_user.id, JobBranch.IT)

    async def test_cannot_claim_salary_when_not_on_shift(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify claim_salary requires being actively on a shift."""
        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        with pytest.raises(ValueError, match="Вы сейчас не на смене"):
            await JobService.claim_salary(async_session, sample_user.id, JobBranch.IT)

    async def test_shift_timer_premature_rejections(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify premature salary claims are rejected for elapsed times < 7200 seconds."""
        job = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        start_time = datetime.datetime(2026, 10, 21, 12, 0, 0)
        await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=start_time)

        # Cannot start another shift while already on shift
        with pytest.raises(ValueError, match="Вы уже на смене"):
            await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=start_time)

        # Test premature claim at t = 0s
        with pytest.raises(ValueError, match="Смена еще не окончена! Осталось работать еще примерно 120 мин"):
            await JobService.claim_salary(
                async_session, sample_user.id, JobBranch.IT, now=start_time
            )

        # Test premature claim at t = 3600s (1 hour)
        t_1h = start_time + datetime.timedelta(seconds=3600)
        with pytest.raises(ValueError, match="Смена еще не окончена! Осталось работать еще примерно 60 мин"):
            await JobService.claim_salary(
                async_session, sample_user.id, JobBranch.IT, now=t_1h
            )

        # Test premature claim at t = 7199s (1 second before 2 hours)
        t_almost = start_time + datetime.timedelta(seconds=7199)
        with pytest.raises(ValueError, match="Смена еще не окончена"):
            await JobService.claim_salary(
                async_session, sample_user.id, JobBranch.IT, now=t_almost
            )

    async def test_shift_proper_completion_and_atomic_ledger(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify proper claim at >= 7200s credits balance atomically and generates double-entry LedgerEntry."""
        job = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        start_time = datetime.datetime(2026, 10, 21, 12, 0, 0)
        await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=start_time)

        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        balance_before = main_acc.balance

        # Claim at exact 7200s (2 hours), with deterministic normal event roll (0.50 -> normal)
        finish_time = start_time + datetime.timedelta(seconds=7200)
        payout, event_msg = await JobService.claim_salary(
            async_session,
            sample_user.id,
            JobBranch.IT,
            now=finish_time,
            event_roll=0.50,  # 75% normal bracket
        )

        base_salary = JobService.get_job_info(JobBranch.IT, 1)["salary"]
        assert payout == base_salary
        assert "Смена отработана на отлично" in event_msg

        # Verify job shift state reset
        assert job.is_on_shift is False
        assert job.shift_started_at is None
        assert job.last_shift_completed_at == finish_time
        assert job.shifts_completed == 1

        # Verify account balance atomically updated
        await async_session.refresh(main_acc)
        assert main_acc.balance == balance_before + payout

        # Verify transaction and ledger entry
        tx_res = await async_session.execute(
            select(Transaction).where(Transaction.recipient_account_id == main_acc.id)
        )
        txs = list(tx_res.scalars().all())
        salary_tx = [t for t in txs if t.tx_type == "SALARY"][-1]
        assert salary_tx.amount == payout
        assert salary_tx.fiscal_code.startswith("ФП-")
        assert len(salary_tx.auth_code) >= 6

        ledger_res = await async_session.execute(
            select(LedgerEntry).where(LedgerEntry.transaction_id == salary_tx.id)
        )
        ledger_entry = ledger_res.scalars().first()
        assert ledger_entry is not None
        assert ledger_entry.direction == "CREDIT"
        assert ledger_entry.amount == payout
        assert ledger_entry.balance_after == main_acc.balance

    async def test_workplace_events_and_shadow_raid_non_negative_payout(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify bonus, reprimand, and police raid modifying salary clamped to non-negative."""
        # 1. Non-shadow bonus (+30%)
        job_it = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        start_time = datetime.datetime(2026, 10, 21, 10, 0, 0)
        await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=start_time)
        t_finish = start_time + datetime.timedelta(seconds=7200)

        payout_bonus, msg_bonus = await JobService.claim_salary(
            async_session,
            sample_user.id,
            JobBranch.IT,
            now=t_finish,
            event_roll=0.05,  # < 0.15 => bonus
            event_pct=30,
        )
        base_it = JobService.get_job_info(JobBranch.IT, 1)["salary"]
        expected_bonus = (base_it * Decimal("30") / Decimal("100")).quantize(Decimal("0.01"))
        assert payout_bonus == base_it + expected_bonus
        assert "Премия" in msg_bonus

        # 2. Non-shadow reprimand (-15%)
        await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=t_finish)
        t_finish2 = t_finish + datetime.timedelta(seconds=7200)
        payout_rep, msg_rep = await JobService.claim_salary(
            async_session,
            sample_user.id,
            JobBranch.IT,
            now=t_finish2,
            event_roll=0.20,  # 0.15 <= roll < 0.25 => reprimand
            event_pct=15,
        )
        expected_rep = (base_it * Decimal("15") / Decimal("100")).quantize(Decimal("0.01"))
        assert payout_rep == base_it - expected_rep
        assert "Штраф" in msg_rep

        # 3. Shadow branch police raid (-50%)
        job_shadow = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SHADOW)
        start_shadow = datetime.datetime(2026, 10, 21, 14, 0, 0)
        await JobService.start_shift(async_session, sample_user.id, JobBranch.SHADOW, now=start_shadow)
        t_finish_sh = start_shadow + datetime.timedelta(seconds=7200)

        payout_raid, msg_raid = await JobService.claim_salary(
            async_session,
            sample_user.id,
            JobBranch.SHADOW,
            now=t_finish_sh,
            event_roll=0.10,  # < 0.20 => police raid
            event_pct=50,
        )
        base_shadow = JobService.get_job_info(JobBranch.SHADOW, 1)["salary"]
        expected_loss = (base_shadow * Decimal("50") / Decimal("100")).quantize(Decimal("0.01"))
        assert payout_raid == base_shadow - expected_loss
        assert "Облава" in msg_raid
        assert payout_raid >= Decimal("0.00")

    async def test_consecutive_shifts_work_flow(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify user can complete 5 consecutive shifts back to back without glitches."""
        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.FINANCE)
        base_time = datetime.datetime(2026, 10, 21, 8, 0, 0)

        for i in range(5):
            t_start = base_time + datetime.timedelta(hours=2 * i)
            t_end = t_start + datetime.timedelta(hours=2)

            await JobService.start_shift(async_session, sample_user.id, JobBranch.FINANCE, now=t_start)
            payout, msg = await JobService.claim_salary(
                async_session,
                sample_user.id,
                JobBranch.FINANCE,
                now=t_end,
                event_roll=0.5,  # Normal
            )
            assert payout > Decimal("0.00")

        job = await JobService.get_user_job_by_branch(async_session, sample_user.id, JobBranch.FINANCE)
        assert job.shifts_completed == 5
        assert job.is_on_shift is False


@pytest.mark.asyncio
class TestPromotionProgressionStress:
    """Stress-test promotion lifecycle across all 5 tiers and max level boundary guard."""

    @pytest.mark.parametrize("branch", [JobBranch.IT, JobBranch.SERVICE, JobBranch.FINANCE, JobBranch.SHADOW])
    async def test_full_tier_progression_from_level_1_to_5(
        self, async_session: AsyncSession, sample_user: User, branch: str
    ):
        """Verify strict shift requirements for levels 1 -> 2 -> 3 -> 4 -> 5 and block at level 5."""
        job = await JobService.apply_for_job(async_session, sample_user.id, branch)
        assert job.level == 1
        assert job.shifts_completed == 0

        # Tier 1 -> 2 requires 3 shifts
        for s in range(2):
            job.shifts_completed = s
            promoted, msg = await JobService.promote_job(async_session, sample_user.id, branch)
            assert promoted is False
            assert "Для повышения нужно отработать еще" in msg
            assert job.level == 1

        # Fulfill 3 shifts
        job.shifts_completed = 3
        promoted, msg = await JobService.promote_job(async_session, sample_user.id, branch)
        assert promoted is True
        assert "ПОЗДРАВЛЯЕМ С ПОВЫШЕНИЕМ" in msg
        assert job.level == 2

        # Immediate re-promote fails (Level 2 requires 5 shifts)
        promoted, msg = await JobService.promote_job(async_session, sample_user.id, branch)
        assert promoted is False
        assert job.level == 2

        # Tier 2 -> 3 requires 5 shifts
        job.shifts_completed = 4
        promoted, msg = await JobService.promote_job(async_session, sample_user.id, branch)
        assert promoted is False
        assert job.level == 2

        job.shifts_completed = 5
        promoted, msg = await JobService.promote_job(async_session, sample_user.id, branch)
        assert promoted is True
        assert job.level == 3

        # Tier 3 -> 4 requires 8 shifts
        job.shifts_completed = 7
        promoted, msg = await JobService.promote_job(async_session, sample_user.id, branch)
        assert promoted is False
        assert job.level == 3

        job.shifts_completed = 8
        promoted, msg = await JobService.promote_job(async_session, sample_user.id, branch)
        assert promoted is True
        assert job.level == 4

        # Tier 4 -> 5 requires 15 shifts
        job.shifts_completed = 14
        promoted, msg = await JobService.promote_job(async_session, sample_user.id, branch)
        assert promoted is False
        assert job.level == 4

        job.shifts_completed = 15
        promoted, msg = await JobService.promote_job(async_session, sample_user.id, branch)
        assert promoted is True
        assert job.level == 5

        # Tier 5 is MAX LEVEL. Calling promote must be strictly blocked
        for _ in range(5):
            job.shifts_completed += 10
            promoted, msg = await JobService.promote_job(async_session, sample_user.id, branch)
            assert promoted is False
            assert "максимальной должности" in msg
            assert job.level == 5

        # Cleanup for next parameterized branch
        await JobService.quit_job(async_session, sample_user.id, branch)

    async def test_promote_unemployed_or_inactive_job_fails(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify promoting a job not actively held raises ValueError."""
        with pytest.raises(ValueError, match="Вы не устроены"):
            await JobService.promote_job(async_session, sample_user.id, JobBranch.IT)


@pytest.mark.asyncio
class TestQuickHustlesStress:
    """Stress-test interactive quick hustles, 15-minute cooldown, and non-negative clamping."""

    async def test_hustle_cooldown_enforcement(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify 15-minute cooldown (900 seconds) rejects premature attempts."""
        start_time = datetime.datetime(2026, 10, 21, 15, 0, 0)

        # 1st attempt succeeds
        delta, msg = await JobService.execute_hustle(
            async_session,
            sample_user.id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            now=start_time,
            success_roll=0.1,  # Success
            reward_override=Decimal("2000.00"),
        )
        assert delta == Decimal("2000.00")
        assert sample_user.last_hustle_at == start_time

        # Attempt at t = 300s (5 min) rejected
        t_5m = start_time + datetime.timedelta(seconds=300)
        with pytest.raises(ValueError, match="Шабашкины руки должны отдохнуть"):
            await JobService.execute_hustle(
                async_session,
                sample_user.id,
                hustle_id="hustle_server_crash",
                choice_idx=0,
                now=t_5m,
            )

        # Attempt at t = 899s (1 second before 15 min) rejected
        t_almost = start_time + datetime.timedelta(seconds=899)
        with pytest.raises(ValueError, match="Шабашкины руки должны отдохнуть"):
            await JobService.execute_hustle(
                async_session,
                sample_user.id,
                hustle_id="hustle_server_crash",
                choice_idx=0,
                now=t_almost,
            )

        # Attempt at t = 900s succeeds
        t_900s = start_time + datetime.timedelta(seconds=900)
        delta2, msg2 = await JobService.execute_hustle(
            async_session,
            sample_user.id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            now=t_900s,
            success_roll=0.1,
            reward_override=Decimal("1500.00"),
        )
        assert delta2 == Decimal("1500.00")
        assert sample_user.last_hustle_at == t_900s

    async def test_non_negative_balance_clamping_under_severe_penalty(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify that when penalty exceeds user balance, balance is clamped to 0.00, never negative."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        main_acc.balance = Decimal("150.00")
        await async_session.flush()

        start_time = datetime.datetime(2026, 10, 21, 16, 0, 0)
        # Attempt hustle that fails with 5,000.00 ₽ penalty while balance is only 150.00 ₽
        delta, msg = await JobService.execute_hustle(
            async_session,
            sample_user.id,
            hustle_id="hustle_shadow_raid",
            choice_idx=0,
            now=start_time,
            success_roll=0.99,  # Force failure
            penalty_override=Decimal("5000.00"),
        )

        # Clamped penalty must equal exact available balance 150.00
        assert delta == Decimal("-150.00")
        await async_session.refresh(main_acc)
        assert main_acc.balance == Decimal("0.00")

        # Now test failure when balance is strictly 0.00 ₽
        t_next = start_time + datetime.timedelta(seconds=1000)
        delta0, msg0 = await JobService.execute_hustle(
            async_session,
            sample_user.id,
            hustle_id="hustle_shadow_raid",
            choice_idx=0,
            now=t_next,
            success_roll=0.99,  # Force failure
            penalty_override=Decimal("8000.00"),
        )

        assert delta0 == Decimal("0.00")
        await async_session.refresh(main_acc)
        assert main_acc.balance == Decimal("0.00")
        # Invariant: balance is never negative

    async def test_hustle_input_validations(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify validation of unknown hustle ID and out-of-range option index."""
        with pytest.raises(ValueError, match="не найдена"):
            await JobService.execute_hustle(
                async_session, sample_user.id, hustle_id="nonexistent_hustle", choice_idx=0
            )

        with pytest.raises(ValueError, match="Некорректный выбор"):
            await JobService.execute_hustle(
                async_session, sample_user.id, hustle_id="hustle_server_crash", choice_idx=99
            )

        with pytest.raises(ValueError, match="Некорректный выбор"):
            await JobService.execute_hustle(
                async_session, sample_user.id, hustle_id="hustle_server_crash", choice_idx=-1
            )


@pytest.mark.asyncio
class TestDatabaseSchemaConstraints:
    """Stress-test SQLite database-level CheckConstraints and UniqueConstraints."""

    async def test_check_constraint_level_boundaries(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Direct database insertion of level=0 or level=6 must raise IntegrityError."""
        user_id = sample_user.id

        job_invalid_low = UserJob(
            user_id=user_id,
            branch=JobBranch.IT,
            level=0,  # Violates level >= 1
            shifts_completed=0,
            is_active=True,
        )
        async_session.add(job_invalid_low)
        with pytest.raises(IntegrityError):
            await async_session.flush()
        await async_session.rollback()

        job_invalid_high = UserJob(
            user_id=user_id,
            branch=JobBranch.IT,
            level=6,  # Violates level <= 5
            shifts_completed=0,
            is_active=True,
        )
        async_session.add(job_invalid_high)
        with pytest.raises(IntegrityError):
            await async_session.flush()
        await async_session.rollback()

    async def test_check_constraint_shifts_completed_positive(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Direct database insertion of shifts_completed < 0 must raise IntegrityError."""
        user_id = sample_user.id

        job_invalid = UserJob(
            user_id=user_id,
            branch=JobBranch.IT,
            level=1,
            shifts_completed=-1,  # Violates shifts_completed >= 0
            is_active=True,
        )
        async_session.add(job_invalid)
        with pytest.raises(IntegrityError):
            await async_session.flush()
        await async_session.rollback()

    async def test_unique_constraint_user_and_branch(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Direct insertion of duplicate (user_id, branch) must raise IntegrityError."""
        user_id = sample_user.id

        job1 = UserJob(
            user_id=user_id,
            branch=JobBranch.SERVICE,
            level=1,
            shifts_completed=0,
            is_active=True,
        )
        async_session.add(job1)
        await async_session.flush()

        job2 = UserJob(
            user_id=user_id,
            branch=JobBranch.SERVICE,
            level=1,
            shifts_completed=0,
            is_active=True,
        )
        async_session.add(job2)
        with pytest.raises(IntegrityError):
            await async_session.flush()
        await async_session.rollback()
