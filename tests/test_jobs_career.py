"""Comprehensive automated test suite for Jobs, Careers & Hustles (💼 Работа и бизнес).

Covers all requirements from Milestones 5-7:
1. Catalog Invariants: 4 branches, 20 professions (5 levels each), salaries, shifts, descriptions.
2. Multi-employment: holding up to 2 active jobs concurrently; 3rd job rejection; duplicate branch rejection.
3. Resignation & Progress Retention: quitting deactivates job and frees up slot; re-hiring preserves level and shifts.
4. Shift Lifecycle & Ledger Settlement: start shift, premature claim rejection (<2h / 7200s), valid claim crediting salary,
   double-entry Transaction(tx_type="SALARY") and LedgerEntry, atomic CAS guard on is_on_shift.
5. Workplace Events: Bonus, Reprimand, Shadow Police Raid deterministic and probabilistic verification.
6. Promotion Lifecycle: promotion when shifts_completed >= shifts_to_promote, rejection if insufficient, capped at Level 5.
7. Quick Hustles: 15-minute cooldown (user.last_hustle_at), decision dilemmas, probabilistic calculation, non-negative balance clamping.
8. Job Formatters: HTML escaping, Unicode progress bars (clamped 0-100%, handling Level 5 MAX without ZeroDivisionError), time countdowns.
9. Offline Telegram Bot Handlers & UI: /jobs, /work, reply button "💼 Работа и бизнес", callback matrix, and show_alert modal alerts.
"""

import datetime
import html
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.bot import create_dispatcher
from app.bot.formatters.jobs import (
    format_branch_catalog,
    format_branch_detail,
    format_career_dashboard,
    format_hustle_quest,
    format_hustle_result,
    format_market_catalog,
    format_progress_bar,
    format_promotion_progress_bar,
    format_shift_claimed,
    format_time_remaining,
)
from app.bot.keyboards.jobs import (
    get_branch_detail_keyboard,
    get_hustle_keyboard,
    get_job_market_keyboard,
    get_jobs_menu_keyboard,
)
from app.bot.keyboards.reply import get_main_menu_keyboard
from app.bot.routers.jobs import (
    handle_jobs_apply,
    handle_jobs_hustle,
    handle_jobs_hustle_choose,
    handle_jobs_market,
    handle_jobs_market_branch,
    handle_jobs_menu,
    handle_jobs_menu_callback,
    handle_jobs_promote,
    handle_jobs_quit,
    handle_jobs_shift_claim,
    handle_jobs_shift_start,
    handle_main_menu,
    jobs_router,
)
from app.database.models import Account, LedgerEntry, Transaction, User, UserJob
from app.services.banking import BankingService
from app.services.jobs import (
    HUSTLE_CATALOG,
    JOB_CATALOG,
    JobBranch,
    JobService,
)


# ==============================================================================
# Helper Inspection Functions
# ==============================================================================

def get_sent_messages(mock_bot: AsyncMock) -> list[dict]:
    """Extract messages sent via mock_bot."""
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


def get_edited_messages(mock_bot: AsyncMock) -> list[dict]:
    """Extract message edits via mock_bot."""
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


def get_callback_answers(mock_bot: AsyncMock) -> list[dict]:
    """Extract callback query answers via mock_bot."""
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
# Suite 1: Catalog Invariants & Data Integrity
# ==============================================================================

class TestJobCatalogInvariants:
    """Verify completeness and mathematical consistency of the 4 branches, 20 professions, and hustles."""

    def test_four_career_branches_exist(self):
        """Verify that exactly 4 career branches are defined."""
        assert len(JobBranch.ALL) == 4
        assert set(JobBranch.ALL) == {"IT", "SERVICE", "FINANCE", "SHADOW"}
        for branch in JobBranch.ALL:
            title = JobBranch.get_title(branch)
            assert isinstance(title, str)
            assert len(title) > 0
            assert title != branch  # Should return formatted string with emoji

    def test_unknown_branch_title_fallback(self):
        """Verify get_title falls back to raw branch string for unknown branches."""
        assert JobBranch.get_title("NONEXISTENT") == "NONEXISTENT"

    def test_catalog_contains_20_professions(self):
        """Verify 4 branches * 5 levels = 20 distinct professions."""
        catalog = JobService.get_job_catalog()
        assert len(catalog) == 4

        total_professions = 0
        expected_shifts = {1: 3, 2: 5, 3: 8, 4: 15, 5: 0}

        for branch in JobBranch.ALL:
            assert branch in catalog
            branch_jobs = catalog[branch]
            assert len(branch_jobs) == 5
            total_professions += len(branch_jobs)

            last_salary = Decimal("0.00")
            for level in range(1, 6):
                assert level in branch_jobs
                job = branch_jobs[level]
                assert "title" in job and isinstance(job["title"], str) and len(job["title"]) > 0
                assert "salary" in job and isinstance(job["salary"], Decimal)
                assert job["salary"] > last_salary, "Salaries must be strictly monotonically increasing with level"
                last_salary = job["salary"]

                assert "shifts_to_promote" in job
                assert job["shifts_to_promote"] == expected_shifts[level]

                assert "desc" in job and isinstance(job["desc"], str) and len(job["desc"]) > 0

        assert total_professions == 20

    def test_get_job_info_retrieval_and_validation(self):
        """Verify JobService.get_job_info returns valid data and raises ValueError on bad inputs."""
        info = JobService.get_job_info(JobBranch.IT, 1)
        assert info["title"] == "Стажер техподдержки"
        assert info["salary"] == Decimal("1500.00")

        # Unknown branch
        with pytest.raises(ValueError, match="Неизвестная ветка"):
            JobService.get_job_info("ASTRONOMY", 1)

        # Invalid level
        with pytest.raises(ValueError, match="Неизвестный уровень"):
            JobService.get_job_info(JobBranch.IT, 0)
        with pytest.raises(ValueError, match="Неизвестный уровень"):
            JobService.get_job_info(JobBranch.IT, 6)

    def test_hustle_catalog_integrity(self):
        """Verify hustle dilemmas catalog contains valid options and probabilities."""
        hustles = JobService.get_hustle_catalog()
        assert len(hustles) >= 4

        for h in hustles:
            assert "id" in h and isinstance(h["id"], str)
            assert "branch" in h and h["branch"] in JobBranch.ALL
            assert "title" in h and len(h["title"]) > 0
            assert "text" in h and len(h["text"]) > 0
            assert "options" in h and len(h["options"]) == 2

            for opt in h["options"]:
                assert "text" in opt and len(opt["text"]) > 0
                assert 0.0 < opt["success_rate"] <= 1.0
                assert opt["reward_min"] >= 0
                assert opt["reward_max"] >= opt["reward_min"]
                assert opt["penalty_min"] >= 0
                assert opt["penalty_max"] >= opt["penalty_min"]
                assert "{amt}" in opt["success_msg"]
                assert "fail_msg" in opt

    def test_get_hustle_scenario_by_id_and_random(self):
        """Verify get_hustle_scenario retrieves by specific ID or returns a random one."""
        first = HUSTLE_CATALOG[0]
        retrieved = JobService.get_hustle_scenario(first["id"])
        assert retrieved["id"] == first["id"]

        random_hustle = JobService.get_hustle_scenario()
        assert random_hustle["id"] in [h["id"] for h in HUSTLE_CATALOG]

        with pytest.raises(ValueError, match="не найдена"):
            JobService.get_hustle_scenario("non_existent_hustle_xyz")


# ==============================================================================
# Suite 2: Multi-Employment & Resignation
# ==============================================================================

class TestMultiEmploymentAndResignation:
    """Verify 2-job maximum, duplicate prevention, and career progress retention upon quitting."""

    @pytest.mark.asyncio
    async def test_apply_first_and_second_job(self, async_session: AsyncSession, sample_user: User):
        """Verify applying for 1st and 2nd job succeeds and updates active jobs list."""
        # Initial: 0 jobs
        jobs = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(jobs) == 0

        # Apply for IT
        job1 = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        assert job1.branch == JobBranch.IT
        assert job1.level == 1
        assert job1.shifts_completed == 0
        assert job1.is_active is True
        assert job1.is_on_shift is False

        # Apply for SERVICE
        job2 = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SERVICE)
        assert job2.branch == JobBranch.SERVICE
        assert job2.level == 1
        assert job2.is_active is True

        active_jobs = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(active_jobs) == 2
        assert {j.branch for j in active_jobs} == {JobBranch.IT, JobBranch.SERVICE}

    @pytest.mark.asyncio
    async def test_apply_third_job_rejected(self, async_session: AsyncSession, sample_user: User):
        """Verify applying for a 3rd concurrent job is strictly rejected."""
        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SERVICE)

        # 3rd job application must raise ValueError
        with pytest.raises(ValueError, match="совмещаете 2 работы"):
            await JobService.apply_for_job(async_session, sample_user.id, JobBranch.FINANCE)

        with pytest.raises(ValueError, match="совмещаете 2 работы"):
            await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SHADOW)

        # Still exactly 2 active jobs
        active_jobs = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(active_jobs) == 2

    @pytest.mark.asyncio
    async def test_duplicate_branch_application_rejected(self, async_session: AsyncSession, sample_user: User):
        """Verify applying for the same branch twice is rejected."""
        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)

        with pytest.raises(ValueError, match="уже устроены в ветке"):
            await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)

    @pytest.mark.asyncio
    async def test_apply_unknown_branch_rejected(self, async_session: AsyncSession, sample_user: User):
        """Verify applying for an unknown branch raises ValueError."""
        with pytest.raises(ValueError, match="Неизвестная ветка"):
            await JobService.apply_for_job(async_session, sample_user.id, "MAGIC_SECTOR")

    @pytest.mark.asyncio
    async def test_resignation_frees_slot_and_preserves_progress(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify quitting sets is_active=False, allows new job, and re-hiring preserves level and shifts."""
        # Hire in IT and advance shifts
        job_it = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        job_it.level = 3
        job_it.shifts_completed = 7
        await async_session.flush()

        # Hire in SERVICE
        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SERVICE)
        assert len(await JobService.get_user_active_jobs(async_session, sample_user.id)) == 2

        # Quit IT
        resigned = await JobService.quit_job(async_session, sample_user.id, JobBranch.IT)
        assert resigned.is_active is False
        assert resigned.is_on_shift is False

        # Slot is now free: 1 active job left
        active = await JobService.get_user_active_jobs(async_session, sample_user.id)
        assert len(active) == 1
        assert active[0].branch == JobBranch.SERVICE

        # Can now apply for FINANCE
        job_fin = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.FINANCE)
        assert job_fin.is_active is True
        assert len(await JobService.get_user_active_jobs(async_session, sample_user.id)) == 2

        # Quit SERVICE and re-apply for IT
        await JobService.quit_job(async_session, sample_user.id, JobBranch.SERVICE)
        re_it = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)

        # Level and shifts completed must be preserved
        assert re_it.is_active is True
        assert re_it.level == 3
        assert re_it.shifts_completed == 7

    @pytest.mark.asyncio
    async def test_quit_when_not_employed_raises(self, async_session: AsyncSession, sample_user: User):
        """Verify quitting a job when not employed raises ValueError."""
        with pytest.raises(ValueError, match="Вы не устроены"):
            await JobService.quit_job(async_session, sample_user.id, JobBranch.FINANCE)


# ==============================================================================
# Suite 3: Shift Lifecycle & Atomic Ledger Settlement
# ==============================================================================

class TestShiftLifecycleAndLedger:
    """Verify shift start, premature claim rejection (<2h), valid claim, atomic credit, and Transaction/LedgerEntry."""

    @pytest.mark.asyncio
    async def test_shift_start_and_double_start_prevention(self, async_session: AsyncSession, sample_user: User):
        """Verify start_shift sets is_on_shift=True and prevents starting twice."""
        job = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        assert job.is_on_shift is False

        # Start shift
        t0 = datetime.datetime(2026, 10, 21, 10, 0, 0)
        started_job = await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=t0)
        assert started_job.is_on_shift is True
        assert started_job.shift_started_at == t0

        # Attempt to start again
        with pytest.raises(ValueError, match="Вы уже на смене"):
            await JobService.start_shift(async_session, sample_user.id, JobBranch.IT)

    @pytest.mark.asyncio
    async def test_shift_start_unemployed_raises(self, async_session: AsyncSession, sample_user: User):
        """Verify starting shift when not employed raises ValueError."""
        with pytest.raises(ValueError, match="Сначала устройтесь"):
            await JobService.start_shift(async_session, sample_user.id, JobBranch.SHADOW)

    @pytest.mark.asyncio
    async def test_premature_shift_claim_rejected(self, async_session: AsyncSession, sample_user: User):
        """Verify claim before 7200 seconds (2 hours) is rejected with remaining minutes."""
        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        t_start = datetime.datetime(2026, 10, 21, 12, 0, 0)
        await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=t_start)

        # 1 minute in
        t_1m = t_start + datetime.timedelta(minutes=1)
        with pytest.raises(ValueError, match="Смена еще не окончена! Осталось работать еще примерно 119 мин"):
            await JobService.claim_salary(async_session, sample_user.id, JobBranch.IT, now=t_1m)

        # 1 hour 59 minutes in (7140 seconds)
        t_almost = t_start + datetime.timedelta(seconds=7140)
        with pytest.raises(ValueError, match="Смена еще не окончена! Осталось работать еще примерно 1 мин"):
            await JobService.claim_salary(async_session, sample_user.id, JobBranch.IT, now=t_almost)

    @pytest.mark.asyncio
    async def test_claim_when_not_on_shift_or_unemployed_raises(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify claiming salary when not on shift or not employed raises ValueError."""
        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SERVICE)

        # Employed but not on shift
        with pytest.raises(ValueError, match="Вы сейчас не на смене"):
            await JobService.claim_salary(async_session, sample_user.id, JobBranch.SERVICE)

        # Not employed
        with pytest.raises(ValueError, match="Вы не устроены"):
            await JobService.claim_salary(async_session, sample_user.id, JobBranch.SHADOW)

    @pytest.mark.asyncio
    async def test_valid_shift_claim_atomic_settlement_and_ledger(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify valid shift claim credits salary, creates Transaction(tx_type='SALARY') and LedgerEntry."""
        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        t_start = datetime.datetime(2026, 10, 21, 8, 0, 0)
        await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=t_start)

        # Initial balance
        overview_before = await BankingService.get_user_overview(async_session, sample_user.id)
        balance_before = overview_before["main_balance"]

        # Shift completed: exactly 7200 seconds later, normal event (roll=0.5)
        t_end = t_start + datetime.timedelta(seconds=7200)
        payout, event_msg = await JobService.claim_salary(
            async_session,
            sample_user.id,
            JobBranch.IT,
            now=t_end,
            event_roll=0.50,
        )

        assert payout == Decimal("1500.00")  # Level 1 IT base salary
        assert "Смена отработана на отлично" in event_msg

        # Verify UserJob state
        job = await JobService.get_user_job_by_branch(async_session, sample_user.id, JobBranch.IT)
        assert job.is_on_shift is False
        assert job.shift_started_at is None
        assert job.shifts_completed == 1
        assert job.last_shift_completed_at == t_end

        # Verify Account balance
        overview_after = await BankingService.get_user_overview(async_session, sample_user.id)
        assert overview_after["main_balance"] == balance_before + payout

        # Verify Transaction record
        txs = (
            await async_session.execute(
                select(Transaction).where(Transaction.tx_type == "SALARY")
            )
        ).scalars().all()
        assert len(txs) == 1
        tx = txs[0]
        assert tx.amount == payout
        assert tx.status == "COMPLETED"
        assert tx.category == "Зарплата и карьера"
        assert "Стажер техподдержки" in tx.description
        assert tx.sender_account_id is None
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        assert tx.recipient_account_id == main_acc.id

        # Verify LedgerEntry record
        entries = (
            await async_session.execute(
                select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id)
            )
        ).scalars().all()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.direction == "CREDIT"
        assert entry.amount == payout
        assert entry.balance_after == overview_after["main_balance"]


# ==============================================================================
# Suite 4: Workplace Events Simulation
# ==============================================================================

class TestWorkplaceEvents:
    """Verify workplace event probability branches for both standard and shadow sectors."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "branch,roll,pct,expected_salary,msg_fragment",
        [
            # IT Branch (Level 1 base = 1500.00)
            (JobBranch.IT, 0.10, 30, Decimal("1950.00"), "Премия за усердие"),  # 1500 + 30% = 1950
            (JobBranch.IT, 0.20, 15, Decimal("1275.00"), "Штраф"),               # 1500 - 15% = 1275
            (JobBranch.IT, 0.60, 0, Decimal("1500.00"), "Смена отработана на отлично"),
            # Shadow Branch (Level 1 base = 3000.00)
            (JobBranch.SHADOW, 0.10, 40, Decimal("1800.00"), "Облава на точке"), # 3000 - 40% = 1800
            (JobBranch.SHADOW, 0.25, 30, Decimal("3900.00"), "Удачный куш"),     # 3000 + 30% = 3900
            (JobBranch.SHADOW, 0.40, 15, Decimal("2550.00"), "Недостача в общаке"),# 3000 - 15% = 2550
            (JobBranch.SHADOW, 0.70, 0, Decimal("3000.00"), "Смена отработана без происшествий"),
        ],
    )
    async def test_workplace_events_math_and_messages(
        self,
        async_session: AsyncSession,
        sample_user: User,
        branch,
        roll,
        pct,
        expected_salary,
        msg_fragment,
    ):
        """Verify deterministic rolls for Bonus, Reprimand, and Shadow Police Raid."""
        job = await JobService.apply_for_job(async_session, sample_user.id, branch)
        t0 = datetime.datetime.now()
        await JobService.start_shift(async_session, sample_user.id, branch, now=t0)

        t_end = t0 + datetime.timedelta(seconds=7200)
        payout, msg = await JobService.claim_salary(
            async_session,
            sample_user.id,
            branch,
            now=t_end,
            event_roll=roll,
            event_pct=pct,
        )

        assert payout == expected_salary
        assert msg_fragment in msg

        # Clean up job for next parametrize test
        await JobService.quit_job(async_session, sample_user.id, branch)


# ==============================================================================
# Suite 5: Promotion Lifecycle
# ==============================================================================

class TestCareerPromotionLifecycle:
    """Verify advancing from Level 1 to 5, shift requirement checks, and max tier capping."""

    @pytest.mark.asyncio
    async def test_promotion_requirements_progression(self, async_session: AsyncSession, sample_user: User):
        """Verify full career progression across all 5 tiers."""
        job = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        assert job.level == 1
        assert job.shifts_completed == 0

        # Tier 1 requires 3 shifts. With 2 shifts -> fails
        job.shifts_completed = 2
        await async_session.flush()
        ok, msg = await JobService.promote_job(async_session, sample_user.id, JobBranch.IT)
        assert ok is False
        assert "нужно отработать еще 1 смен" in msg
        assert job.level == 1

        # With 3 shifts -> promotes to Level 2 (Тестировщик)
        job.shifts_completed = 3
        await async_session.flush()
        ok, msg = await JobService.promote_job(async_session, sample_user.id, JobBranch.IT)
        assert ok is True
        assert job.level == 2
        assert "Тестировщик" in msg

        # Tier 2 requires 5 shifts. With 4 shifts -> fails
        job.shifts_completed = 4
        await async_session.flush()
        ok, _ = await JobService.promote_job(async_session, sample_user.id, JobBranch.IT)
        assert ok is False

        # With 5 shifts -> promotes to Level 3 (Джуниор)
        job.shifts_completed = 5
        await async_session.flush()
        ok, msg = await JobService.promote_job(async_session, sample_user.id, JobBranch.IT)
        assert ok is True
        assert job.level == 3
        assert "Джуниор" in msg

        # Tier 3 requires 8 shifts -> Level 4 (Тимлид)
        job.shifts_completed = 8
        await async_session.flush()
        ok, msg = await JobService.promote_job(async_session, sample_user.id, JobBranch.IT)
        assert ok is True
        assert job.level == 4
        assert "Тимлид" in msg

        # Tier 4 requires 15 shifts -> Level 5 (CTO)
        job.shifts_completed = 15
        await async_session.flush()
        ok, msg = await JobService.promote_job(async_session, sample_user.id, JobBranch.IT)
        assert ok is True
        assert job.level == 5
        assert "CTO" in msg

        # Tier 5 is Max -> cannot promote further
        ok, msg = await JobService.promote_job(async_session, sample_user.id, JobBranch.IT)
        assert ok is False
        assert "максимальной должности" in msg
        assert job.level == 5

    @pytest.mark.asyncio
    async def test_promote_unemployed_raises(self, async_session: AsyncSession, sample_user: User):
        """Verify promoting a job when unemployed raises ValueError."""
        with pytest.raises(ValueError, match="Вы не устроены"):
            await JobService.promote_job(async_session, sample_user.id, JobBranch.FINANCE)


# ==============================================================================
# Suite 6: Quick Hustles & Balance Clamping
# ==============================================================================

class TestQuickHustles:
    """Verify hustle cooldowns, probabilistic rewards/penalties, and balance clamping."""

    @pytest.mark.asyncio
    async def test_hustle_cooldown_and_execution(self, async_session: AsyncSession, sample_user: User):
        """Verify 15-minute cooldown enforcement between hustle attempts."""
        t0 = datetime.datetime(2026, 10, 21, 14, 0, 0)
        hustle_id = "hustle_server_crash"

        # 1. First hustle succeeds
        delta, msg = await JobService.execute_hustle(
            async_session,
            sample_user.id,
            hustle_id=hustle_id,
            choice_idx=0,
            now=t0,
            success_roll=0.1,  # Guaranteed success
            reward_override=Decimal("2000.00"),
        )
        assert delta == Decimal("2000.00")
        assert "Бэкап встал идеально" in msg

        # Verify last_hustle_at is updated
        await async_session.refresh(sample_user)
        assert sample_user.last_hustle_at == t0

        # 2. Second hustle at t0 + 5 minutes is rejected (cooldown active)
        t_early = t0 + datetime.timedelta(minutes=5)
        with pytest.raises(ValueError, match="Шабашкины руки должны отдохнуть"):
            await JobService.execute_hustle(
                async_session,
                sample_user.id,
                hustle_id=hustle_id,
                choice_idx=0,
                now=t_early,
            )

        # 3. Third hustle at t0 + 15 minutes (900s) succeeds
        t_ready = t0 + datetime.timedelta(seconds=900)
        delta2, _ = await JobService.execute_hustle(
            async_session,
            sample_user.id,
            hustle_id=hustle_id,
            choice_idx=1,
            now=t_ready,
            success_roll=0.1,
            reward_override=Decimal("5000.00"),
        )
        assert delta2 == Decimal("5000.00")

    @pytest.mark.asyncio
    async def test_hustle_invalid_choice_and_hustle_id(self, async_session: AsyncSession, sample_user: User):
        """Verify invalid choice index or non-existent hustle ID raises ValueError."""
        # Non-existent hustle
        with pytest.raises(ValueError, match="не найдена"):
            await JobService.execute_hustle(
                async_session, sample_user.id, "ghost_hustle", 0
            )

        # Invalid choice index
        with pytest.raises(ValueError, match="Некорректный выбор"):
            await JobService.execute_hustle(
                async_session, sample_user.id, "hustle_server_crash", 99
            )

    @pytest.mark.asyncio
    async def test_hustle_non_negative_balance_clamping(self, async_session: AsyncSession, sample_user: User):
        """Verify penalties cannot reduce balance below zero under any circumstances."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)

        # 1. Set balance to 500.00 ₽ and inflict 2000.00 ₽ penalty
        main_acc.balance = Decimal("500.00")
        await async_session.commit()

        delta, msg = await JobService.execute_hustle(
            async_session,
            sample_user.id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            cooldown_seconds=0,
            success_roll=0.99,  # Guaranteed failure
            penalty_override=Decimal("2000.00"),
        )

        # Clamped to existing balance 500.00
        assert delta == Decimal("-500.00")
        await async_session.refresh(main_acc)
        assert main_acc.balance == Decimal("0.00")

        # 2. Inflict penalty on zero balance (0.00 ₽)
        delta_zero, _ = await JobService.execute_hustle(
            async_session,
            sample_user.id,
            hustle_id="hustle_server_crash",
            choice_idx=0,
            cooldown_seconds=0,
            success_roll=0.99,
            penalty_override=Decimal("1500.00"),
        )

        assert delta_zero == Decimal("0.00")
        await async_session.refresh(main_acc)
        assert main_acc.balance == Decimal("0.00")


# ==============================================================================
# Suite 7: Formatters and UI Rendering
# ==============================================================================

class TestJobFormatters:
    """Verify HTML escaping, Unicode progress bars, time countdowns, and dashboard rendering."""

    def test_format_progress_bar_clamps(self):
        """Verify format_progress_bar clamps 0-100% and produces 10 blocks."""
        assert format_progress_bar(0) == "░░░░░░░░░░"
        assert format_progress_bar(50) == "█████░░░░░"
        assert format_progress_bar(100) == "██████████"
        assert format_progress_bar(-20) == "░░░░░░░░░░"
        assert format_progress_bar(150) == "██████████"

    def test_format_promotion_progress_bar_max_tier_zero_division(self):
        """Verify format_promotion_progress_bar cleanly handles max level without ZeroDivisionError."""
        # Level 5 / Max level (required = 0)
        max_str = format_promotion_progress_bar(completed=10, required=0)
        assert "MAX" in max_str
        assert "██████████" in max_str

        # Standard levels
        bar_partial = format_promotion_progress_bar(completed=2, required=5)
        assert "████░░░░░░" in bar_partial
        assert "2/5" in bar_partial

    def test_format_time_remaining_countdown(self):
        """Verify format_time_remaining formats hours and minutes."""
        assert format_time_remaining(7200) == "2 ч 0 мин"
        assert format_time_remaining(5430) == "1 ч 30 мин"
        assert format_time_remaining(1800) == "30 мин 0 сек"
        assert format_time_remaining(50) == "50 сек"
        assert format_time_remaining(0) == "Готово"
        assert format_time_remaining(-100) == "Готово"

    def test_format_career_dashboard_html_escaping(self):
        """Verify career dashboard safely escapes malicious HTML in user display names."""
        malicious_user = User(
            id=999,
            telegram_id=999,
            username="evil_user",
            full_name="<script>alert('xss')</script>",
            created_at=datetime.datetime.now(),
        )
        dashboard = format_career_dashboard(malicious_user, active_jobs=[])
        assert "<script>" not in dashboard
        assert "&lt;script&gt;alert('xss')&lt;/script&gt;" in dashboard
        assert "У вас пока нет активной работы" in dashboard

    def test_format_market_catalog_and_branch_detail(self):
        """Verify market catalog displays all 5 tiers and branch detail renders correctly."""
        catalog_text = format_market_catalog(JobBranch.IT, JOB_CATALOG[JobBranch.IT])
        assert "IT / Офис" in catalog_text
        assert "Стажер техподдержки" in catalog_text
        assert "CTO / Технический директор" in catalog_text
        assert "60,000.00 ₽" in catalog_text

        branch_text = format_branch_detail(JobBranch.SERVICE)
        assert "Общепит и Услуги" in branch_text
        assert "Промоутер в костюме сосиски" in branch_text

    def test_format_hustle_quest_and_result(self):
        """Verify hustle quest formatting and outcome cards."""
        quest = HUSTLE_CATALOG[0]
        quest_text = format_hustle_quest(quest)
        assert quest["title"] in quest_text
        assert quest["text"] in quest_text

        # Win result
        win_text = format_hustle_result(
            delta=Decimal("2500.00"), outcome_msg="Куш взят!", new_balance=Decimal("12500.00")
        )
        assert "Успешная подработка" in win_text
        assert "+2,500.00 ₽" in win_text
        assert "12,500.00 ₽" in win_text

        # Loss result
        loss_text = format_hustle_result(
            delta=Decimal("-500.00"), outcome_msg="Штраф!", new_balance=Decimal("9500.00")
        )
        assert "Неудача на шабашке" in loss_text
        assert "-500.00 ₽" in loss_text


# ==============================================================================
# Suite 8: Offline Telegram Bot Handlers & UI Integration
# ==============================================================================

class TestOfflineTelegramBotJobHandlers:
    """Verify commands (/jobs, /work), reply button, callbacks matrix, and show_alert feedback."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cmd", ["/jobs", "/work", "💼 Работа и бизнес"])
    async def test_career_menu_message_entrypoints(self, cmd, test_engine, mock_bot):
        """Verify /jobs, /work, and reply button open the career menu."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 900101
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="CareerUser", username="career_user")
        msg = Message(
            message_id=501,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text=cmd,
        ).as_(mock_bot)

        await dp.feed_update(mock_bot, Update(update_id=101, message=msg))

        sent = get_sent_messages(mock_bot)
        assert len(sent) >= 1
        last = sent[-1]
        assert "Т-Банк Карьера и Бизнес" in last["text"]
        assert last["reply_markup"] is not None

        # Check inline buttons
        btn_data = [b.callback_data for row in last["reply_markup"].inline_keyboard for b in row]
        assert "jobs:market" in btn_data
        assert "jobs:hustle" in btn_data
        assert "main_menu" in btn_data

    @pytest.mark.asyncio
    async def test_callback_navigation_matrix(self, test_engine, mock_bot):
        """Verify full callback query navigation across market, branch, apply, shift, and quit."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 900201
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="NavUser", username="nav_user")
        base_msg = Message(
            message_id=601,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        # 1. jobs:market -> opens labor market
        cb_market = CallbackQuery(
            id="cb_1", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:market"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=201, callback_query=cb_market))
        edited = get_edited_messages(mock_bot)
        assert "Биржа труда Т-Банка" in edited[-1]["text"]

        # 2. jobs:market:FINANCE -> opens finance branch
        cb_fin = CallbackQuery(
            id="cb_2", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:market:FINANCE"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=202, callback_query=cb_fin))
        edited = get_edited_messages(mock_bot)
        assert "Финансы и Банкинг" in edited[-1]["text"]

        # 3. jobs:apply:FINANCE -> hires user
        cb_apply = CallbackQuery(
            id="cb_3", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:apply:FINANCE"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=203, callback_query=cb_apply))
        answers = get_callback_answers(mock_bot)
        assert any("успешно трудоустроились" in (a.get("text") or "") and a.get("show_alert") is True for a in answers)

        # 4. jobs:shift:start:FINANCE -> starts shift
        cb_shift = CallbackQuery(
            id="cb_4", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:shift:start:FINANCE"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=204, callback_query=cb_shift))
        answers = get_callback_answers(mock_bot)
        assert any("Смена начата" in (a.get("text") or "") and a.get("show_alert") is True for a in answers)

        # 5. jobs:shift:claim:FINANCE (premature) -> rejected with alert
        cb_claim = CallbackQuery(
            id="cb_5", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:shift:claim:FINANCE"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=205, callback_query=cb_claim))
        answers = get_callback_answers(mock_bot)
        assert any("Смена еще не окончена" in (a.get("text") or "") and a.get("show_alert") is True for a in answers)

        # 6. jobs:quit:FINANCE -> resigns
        cb_quit = CallbackQuery(
            id="cb_6", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:quit:FINANCE"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=206, callback_query=cb_quit))
        answers = get_callback_answers(mock_bot)
        assert any("уволились с этой работы" in (a.get("text") or "") and a.get("show_alert") is True for a in answers)

    @pytest.mark.asyncio
    async def test_callback_hustle_and_choose(self, test_engine, mock_bot):
        """Verify jobs:hustle presents dilemma and jobs:hustle:choose executes."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 900202
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="Hustler", username="hustler")
        base_msg = Message(
            message_id=701,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        # 1. Trigger hustle
        cb_h = CallbackQuery(
            id="cb_h1", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:hustle"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=301, callback_query=cb_h))
        edited = get_edited_messages(mock_bot)
        assert "Шабашка:" in edited[-1]["text"]

        # 2. Choose option 0
        cb_choose = CallbackQuery(
            id="cb_h2", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:hustle:choose:0"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=302, callback_query=cb_choose))
        answers = get_callback_answers(mock_bot)
        assert any(
            ("Заработано" in (a.get("text") or "") or "Потери" in (a.get("text") or "") or "Баланс не изменился" in (a.get("text") or ""))
            and a.get("show_alert") is True
            for a in answers
        )
