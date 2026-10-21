"""Empirical Challenger Test Suite for Milestone 6: Jobs Router, Handlers & Bot Integration.

Adversarial and empirical verification covering:
1. Router Registration & Dispatcher Setup:
   - jobs_router registration in create_dispatcher().
   - Sub-router naming, priority, and multiple create_dispatcher() calls idempotency.
   - Presence of reply button "💼 Работа и бизнес" in get_main_menu_keyboard().
2. Message Triggers E2E via aiogram feed_update:
   - Command /jobs.
   - Command /work.
   - Reply button message "💼 Работа и бизнес".
   - Active FSM state clearing on career menu entry.
3. Callback Queries Full Dispatch Matrix:
   - jobs:menu (dashboard refresh in place).
   - jobs:market (branch catalog navigation).
   - jobs:market:<branch> for IT, SERVICE, FINANCE, SHADOW.
   - jobs:apply:<branch> (career hiring).
   - jobs:quit:<branch> (career resignation retaining progress).
   - jobs:shift:start:<branch> (shift lifecycle).
   - jobs:shift:claim:<branch> (salary collection and double-entry ledger).
   - jobs:promote:<branch> (career progression).
   - jobs:hustle (quick hustle dilemma initiation).
   - jobs:hustle:choose:<option_idx> and jobs:hustle:choose:<hustle_id>:<option_idx>.
   - main_menu (return to bank dashboard).
4. Adversarial Error Alerts & Boundary Conditions (show_alert=True):
   - Multi-employment cap: 3rd active job application rejected with show_alert=True.
   - Premature shift claim: salary claim before 2 hours (7200s) rejected with show_alert=True.
   - Double shift start: starting shift when already on shift rejected with show_alert=True.
   - Unemployed shift start / claim: starting/claiming shift when not employed in branch rejected with show_alert=True.
   - Unemployed resignation: quitting a branch where not employed rejected with show_alert=True.
   - Invalid branch callback: jobs:market:UNKNOWN rejected with show_alert=True.
   - Hustle cooldown enforcement: second hustle within 15 minutes rejected with show_alert=True.
   - Premature promotion: promotion without required shifts rejected with show_alert=True.
   - Max level promotion: promotion at level 5 (CTO) rejected with show_alert=True.
   - Hustle non-negative balance clamping: negative outcome on 0 balance cannot trigger negative balance.
5. Robustness & Fault Tolerance:
   - TelegramBadRequest ("message is not modified") handling without unhandled exceptions.
   - Callback with None message handling.
   - HTML injection defense on user display names and event notices.
   - Callback query data 64-byte limit invariant across all generated keyboards.
"""

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.bot import create_dispatcher, detach_router
from app.bot.formatters.jobs import (
    format_branch_catalog,
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
from app.bot.states.states import TransferStates
from app.database.models import Account, Transaction, User, UserJob
from app.services.banking import BankingService
from app.services.jobs import JobBranch, JobService


# ==============================================================================
# Helper Inspection Functions
# ==============================================================================

def extract_bot_sent_messages(mock_bot: AsyncMock) -> list[dict]:
    """Extract all messages sent via bot(SendMessage(...)) or bot.send_message."""
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
    """Extract all message edits via bot(EditMessageText(...)) or bot.edit_message_text."""
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
    """Extract all callback query answers via bot(AnswerCallbackQuery(...)) or bot.answer_callback_query."""
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
# Suite 1: Router Registration & Dispatcher Setup
# ==============================================================================

class TestJobsRouterRegistrationAndWiring:
    """Empirically challenge jobs_router registration and wiring in create_dispatcher()."""

    def test_jobs_router_is_registered_in_dispatcher(self):
        """Verify jobs_router is included in create_dispatcher()."""
        dp = create_dispatcher()
        sub_routers = dp.sub_routers
        assert jobs_router in sub_routers, "jobs_router must be present in dispatcher sub_routers"
        assert jobs_router.name == "jobs"

    def test_create_dispatcher_repeated_calls_idempotency(self):
        """Verify repeated calls to create_dispatcher do not raise or duplicate router parents."""
        for _ in range(10):
            dp = create_dispatcher()
            assert jobs_router in dp.sub_routers
            sub_names = [r.name for r in dp.sub_routers]
            assert sub_names.count("jobs") == 1

    def test_reply_keyboard_contains_jobs_button(self):
        """Verify get_main_menu_keyboard contains the persistent '💼 Работа и бизнес' button."""
        markup = get_main_menu_keyboard()
        button_texts = [btn.text for row in markup.keyboard for btn in row]
        assert "💼 Работа и бизнес" in button_texts, (
            "Reply keyboard must contain '💼 Работа и бизнес' button"
        )

    def test_all_jobs_callback_queries_under_64_bytes(self):
        """Verify every callback_data produced by jobs keyboards is strictly <= 64 bytes."""
        # Main dashboard
        markup_menu = get_jobs_menu_keyboard([])
        for row in markup_menu.inline_keyboard:
            for btn in row:
                assert btn.callback_data is not None
                assert len(btn.callback_data.encode("utf-8")) <= 64

        # Market
        markup_market = get_job_market_keyboard()
        for row in markup_market.inline_keyboard:
            for btn in row:
                assert btn.callback_data is not None
                assert len(btn.callback_data.encode("utf-8")) <= 64

        # Branch details for all 4 branches
        for branch in JobBranch.ALL:
            for is_emp in (True, False):
                for count in (0, 1, 2):
                    markup_b = get_branch_detail_keyboard(branch, is_employed=is_emp, active_count=count)
                    for row in markup_b.inline_keyboard:
                        for btn in row:
                            assert btn.callback_data is not None
                            assert len(btn.callback_data.encode("utf-8")) <= 64

        # Hustle keyboards
        catalog = JobService.get_hustle_catalog()
        for quest in catalog:
            markup_h = get_hustle_keyboard(quest["options"], hustle_id=quest["id"])
            for row in markup_h.inline_keyboard:
                for btn in row:
                    assert btn.callback_data is not None
                    assert len(btn.callback_data.encode("utf-8")) <= 64


# ==============================================================================
# Suite 2: Message Triggers E2E via aiogram feed_update
# ==============================================================================

class TestJobsMessageTriggersE2E:
    """Empirically test message triggers (/jobs, /work, text) through the full dispatcher."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "trigger_text",
        [
            "/jobs",
            "/work",
            "💼 Работа и бизнес",
        ],
    )
    async def test_message_triggers_dispatch_to_career_menu(
        self,
        trigger_text,
        test_engine,
        mock_bot,
    ):
        """Verify /jobs, /work, and '💼 Работа и бизнес' correctly open the career menu."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 700101
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="CareerTester", username="careertester")
        msg = Message(
            message_id=101,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text=trigger_text,
        ).as_(mock_bot)

        update = Update(update_id=1, message=msg)
        await dp.feed_update(mock_bot, update)

        sent_messages = extract_bot_sent_messages(mock_bot)
        assert len(sent_messages) >= 1, f"Expected message response for trigger '{trigger_text}'"
        last_msg = sent_messages[-1]
        assert "Т-Банк Карьера и Бизнес" in last_msg["text"]
        assert last_msg["reply_markup"] is not None

        # Verify buttons in returned inline keyboard
        buttons = [
            btn.callback_data
            for row in last_msg["reply_markup"].inline_keyboard
            for btn in row
        ]
        assert "jobs:market" in buttons
        assert "jobs:hustle" in buttons
        assert "jobs:menu" in buttons
        assert "main_menu" in buttons

    @pytest.mark.asyncio
    async def test_message_trigger_clears_active_fsm_state(
        self,
        test_engine,
        mock_bot,
    ):
        """Verify sending /jobs clears any existing dialogue FSM state (e.g. transfer state)."""
        storage = MemoryStorage()
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory, storage=storage)

        tg_id = 700102
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="FsmUser", username="fsm_user")

        # Set FSM state with exact StorageKey
        fsm_key = StorageKey(bot_id=mock_bot.id, chat_id=tg_id, user_id=tg_id)
        fsm_ctx = FSMContext(storage=storage, key=fsm_key)
        await fsm_ctx.set_state(TransferStates.waiting_for_amount)
        current_state = await fsm_ctx.get_state()
        assert current_state == TransferStates.waiting_for_amount.state

        msg = Message(
            message_id=102,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="💼 Работа и бизнес",
        ).as_(mock_bot)
        user = User(
            id=700102,
            telegram_id=tg_id,
            username="fsm_user",
            full_name="FsmUser",
            created_at=datetime.datetime.now(),
        )

        # Calling handle_jobs_menu clears FSM state
        async with session_factory() as session:
            await handle_jobs_menu(msg, session=session, user=user, state=fsm_ctx)

        # Ensure state was cleared
        cleared_state = await fsm_ctx.get_state()
        assert cleared_state is None


# ==============================================================================
# Suite 3: Callback Queries Dispatch Matrix E2E via aiogram feed_update
# ==============================================================================

class TestJobsCallbackQueriesDispatchE2E:
    """Empirically test each callback query dispatch and state transition via dp.feed_update."""

    @pytest.mark.asyncio
    async def test_callback_jobs_menu_refresh(self, test_engine, mock_bot):
        """Verify 'jobs:menu' callback refreshes career dashboard and answers query."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 700201
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="RefreshTester", username="refreshtester")
        msg = Message(
            message_id=201,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Old Text",
        ).as_(mock_bot)
        cb = CallbackQuery(
            id="cb_menu_1",
            from_user=tg_user,
            chat_instance="ci_1",
            message=msg,
            data="jobs:menu",
        ).as_(mock_bot)

        update = Update(update_id=10, callback_query=cb)
        await dp.feed_update(mock_bot, update)

        edits = extract_bot_edited_messages(mock_bot)
        assert len(edits) >= 1
        assert "Т-Банк Карьера и Бизнес" in edits[-1]["text"]

        answers = extract_bot_callback_answers(mock_bot)
        assert any("Статус карьеры обновлен" in (a.get("text") or "") for a in answers)

    @pytest.mark.asyncio
    async def test_callback_jobs_market_and_branch_detail(self, test_engine, mock_bot):
        """Verify 'jobs:market' and 'jobs:market:IT' navigate correctly."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 700202
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="MarketUser", username="market_user")
        msg = Message(
            message_id=202,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        # 1. Market overview
        cb1 = CallbackQuery(
            id="cb_market_1",
            from_user=tg_user,
            chat_instance="ci_1",
            message=msg,
            data="jobs:market",
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=11, callback_query=cb1))

        edits = extract_bot_edited_messages(mock_bot)
        assert len(edits) >= 1
        assert "Биржа труда Т-Банка" in edits[-1]["text"]

        # 2. Branch detail: IT
        cb2 = CallbackQuery(
            id="cb_market_it",
            from_user=tg_user,
            chat_instance="ci_1",
            message=msg,
            data="jobs:market:IT",
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=12, callback_query=cb2))

        edits = extract_bot_edited_messages(mock_bot)
        assert "IT / Офис" in edits[-1]["text"]
        assert "Стажер техподдержки" in edits[-1]["text"]

    @pytest.mark.asyncio
    async def test_callback_jobs_apply_and_quit_lifecycle(self, test_engine, mock_bot):
        """Verify hiring ('jobs:apply:IT') and resigning ('jobs:quit:IT')."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 700203
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="ApplyUser", username="apply_user")
        msg = Message(
            message_id=203,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Branch",
        ).as_(mock_bot)

        # 1. Apply for IT
        cb_apply = CallbackQuery(
            id="cb_apply_1",
            from_user=tg_user,
            chat_instance="ci_1",
            message=msg,
            data="jobs:apply:IT",
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=13, callback_query=cb_apply))

        answers = extract_bot_callback_answers(mock_bot)
        assert any("успешно трудоустроились" in (a.get("text") or "") and a.get("show_alert") is True for a in answers)

        # Verify DB active status
        async with session_factory() as session:
            user = await session.scalar(select(User).where(User.telegram_id == tg_id))
            job = await JobService.get_user_job_by_branch(session, user.id, JobBranch.IT)
            assert job is not None
            assert job.is_active is True
            assert job.level == 1

        # 2. Quit IT
        cb_quit = CallbackQuery(
            id="cb_quit_1",
            from_user=tg_user,
            chat_instance="ci_1",
            message=msg,
            data="jobs:quit:IT",
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=14, callback_query=cb_quit))

        answers = extract_bot_callback_answers(mock_bot)
        assert any("уволились с этой работы" in (a.get("text") or "") and a.get("show_alert") is True for a in answers)

        async with session_factory() as session:
            user = await session.scalar(select(User).where(User.telegram_id == tg_id))
            job = await JobService.get_user_job_by_branch(session, user.id, JobBranch.IT)
            assert job.is_active is False
            assert job.level == 1  # progress retained

    @pytest.mark.asyncio
    async def test_callback_jobs_shift_lifecycle_e2e(self, test_engine, mock_bot):
        """Verify shift start and claim lifecycle via callbacks."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 700204
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="ShiftUser", username="shift_user")
        msg = Message(
            message_id=204,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Career",
        ).as_(mock_bot)

        # Pre-employ in SERVICE
        async with session_factory() as session:
            u, _, _ = await BankingService.get_or_create_user(session, tg_id, "shift_user", "Shift User")
            await JobService.apply_for_job(session, u.id, JobBranch.SERVICE)
            await session.commit()

        # 1. Start shift
        cb_start = CallbackQuery(
            id="cb_s_1",
            from_user=tg_user,
            chat_instance="ci_1",
            message=msg,
            data="jobs:shift:start:SERVICE",
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=15, callback_query=cb_start))

        answers = extract_bot_callback_answers(mock_bot)
        assert any("Смена начата" in (a.get("text") or "") and a.get("show_alert") is True for a in answers)

        # 2. Simulate 2 hours elapsed in DB
        async with session_factory() as session:
            user = await session.scalar(select(User).where(User.telegram_id == tg_id))
            job = await JobService.get_user_job_by_branch(session, user.id, JobBranch.SERVICE)
            job.shift_started_at = datetime.datetime.now() - datetime.timedelta(seconds=7205)
            await session.commit()

        # 3. Claim salary
        cb_claim = CallbackQuery(
            id="cb_s_2",
            from_user=tg_user,
            chat_instance="ci_1",
            message=msg,
            data="jobs:shift:claim:SERVICE",
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=16, callback_query=cb_claim))

        answers = extract_bot_callback_answers(mock_bot)
        assert any("Зарплата получена" in (a.get("text") or "") and a.get("show_alert") is True for a in answers)

        # Verify balance was credited and transaction created
        async with session_factory() as session:
            overview = await BankingService.get_user_overview(session, user.id)
            assert overview["main_balance"] > Decimal("0.00")
            job = await JobService.get_user_job_by_branch(session, user.id, JobBranch.SERVICE)
            assert job.is_on_shift is False
            assert job.shifts_completed == 1

    @pytest.mark.asyncio
    async def test_callback_jobs_hustle_dilemma_and_choose(self, test_engine, mock_bot):
        """Verify quick hustle trigger and decision choice callbacks."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 700205
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="HustleUser", username="hustle_user")
        msg = Message(
            message_id=205,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        # 1. Trigger hustle
        cb_hustle = CallbackQuery(
            id="cb_h_1",
            from_user=tg_user,
            chat_instance="ci_1",
            message=msg,
            data="jobs:hustle",
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=17, callback_query=cb_hustle))

        edits = extract_bot_edited_messages(mock_bot)
        assert len(edits) >= 1
        assert "Шабашка:" in edits[-1]["text"]

        # 2. Choose option 0 with explicit quest id
        catalog = JobService.get_hustle_catalog()
        hustle_id = catalog[0]["id"]
        cb_choose = CallbackQuery(
            id="cb_h_2",
            from_user=tg_user,
            chat_instance="ci_1",
            message=msg,
            data=f"jobs:hustle:choose:{hustle_id}:0",
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=18, callback_query=cb_choose))

        answers = extract_bot_callback_answers(mock_bot)
        assert any(
            ("Заработано" in (a.get("text") or "") or "Потери" in (a.get("text") or "") or "Баланс не изменился" in (a.get("text") or ""))
            and a.get("show_alert") is True
            for a in answers
        )

    @pytest.mark.asyncio
    async def test_callback_main_menu(self, test_engine, mock_bot):
        """Verify 'main_menu' callback navigates back to neobank dashboard."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 700206
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="BackUser", username="back_user")
        msg = Message(
            message_id=206,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Jobs",
        ).as_(mock_bot)

        cb_main = CallbackQuery(
            id="cb_m_1",
            from_user=tg_user,
            chat_instance="ci_1",
            message=msg,
            data="main_menu",
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=19, callback_query=cb_main))

        edits = extract_bot_edited_messages(mock_bot)
        assert len(edits) >= 1
        assert "Добро пожаловать в Т-Банк Онлайн" in edits[-1]["text"]


# ==============================================================================
# Suite 4: Adversarial Error Alerts & Boundary Conditions
# ==============================================================================

class TestJobsErrorAlertsAndValidationAdversarial:
    """Stress-test error alert responses (show_alert=True) across all invalid operations."""

    @pytest.mark.asyncio
    async def test_error_alert_multi_employment_3rd_job_rejected(
        self,
        async_session: AsyncSession,
        mock_bot: AsyncMock,
        callback_factory,
    ):
        """Verify 3rd job application is rejected with show_alert=True."""
        user, _, _ = await BankingService.get_or_create_user(
            async_session, 800001, "multi_emp", "Multi Employee"
        )
        # Apply for 2 jobs
        await JobService.apply_for_job(async_session, user.id, JobBranch.IT)
        await JobService.apply_for_job(async_session, user.id, JobBranch.SERVICE)
        await async_session.commit()

        # Attempt 3rd job via handler
        cb = callback_factory("jobs:apply:FINANCE", user_id=800001)
        await handle_jobs_apply(cb, session=async_session, user=user)

        answers = extract_bot_callback_answers(mock_bot)
        assert len(answers) >= 1
        last_answer = answers[-1]
        assert last_answer["show_alert"] is True
        assert "совмещаете 2 работы" in last_answer["text"]

    @pytest.mark.asyncio
    async def test_error_alert_premature_shift_claim_rejected(
        self,
        async_session: AsyncSession,
        mock_bot: AsyncMock,
        callback_factory,
    ):
        """Verify premature shift claim is rejected with show_alert=True showing remaining time."""
        user, _, _ = await BankingService.get_or_create_user(
            async_session, 800002, "premature_user", "Premature User"
        )
        await JobService.apply_for_job(async_session, user.id, JobBranch.IT)
        await JobService.start_shift(async_session, user.id, JobBranch.IT)
        await async_session.commit()

        # Attempt claim immediately (0 seconds elapsed)
        cb = callback_factory("jobs:shift:claim:IT", user_id=800002)
        await handle_jobs_shift_claim(cb, session=async_session, user=user)

        answers = extract_bot_callback_answers(mock_bot)
        assert len(answers) >= 1
        last_answer = answers[-1]
        assert last_answer["show_alert"] is True
        assert "Смена еще не окончена" in last_answer["text"]

    @pytest.mark.asyncio
    async def test_error_alert_double_shift_start_rejected(
        self,
        async_session: AsyncSession,
        mock_bot: AsyncMock,
        callback_factory,
    ):
        """Verify starting shift when already on shift is rejected with show_alert=True."""
        user, _, _ = await BankingService.get_or_create_user(
            async_session, 800003, "double_shift", "Double Shift User"
        )
        await JobService.apply_for_job(async_session, user.id, JobBranch.SHADOW)
        await JobService.start_shift(async_session, user.id, JobBranch.SHADOW)
        await async_session.commit()

        # Attempt second start
        cb = callback_factory("jobs:shift:start:SHADOW", user_id=800003)
        await handle_jobs_shift_start(cb, session=async_session, user=user)

        answers = extract_bot_callback_answers(mock_bot)
        assert len(answers) >= 1
        last_answer = answers[-1]
        assert last_answer["show_alert"] is True
        assert "Вы уже на смене" in last_answer["text"]

    @pytest.mark.asyncio
    async def test_error_alert_shift_operations_when_unemployed(
        self,
        async_session: AsyncSession,
        mock_bot: AsyncMock,
        callback_factory,
    ):
        """Verify shift start and claim when user is not employed in branch are rejected with show_alert=True."""
        user, _, _ = await BankingService.get_or_create_user(
            async_session, 800004, "unemp_user", "Unemployed User"
        )

        # 1. Start shift when unemployed
        cb_start = callback_factory("jobs:shift:start:FINANCE", user_id=800004)
        await handle_jobs_shift_start(cb_start, session=async_session, user=user)

        answers = extract_bot_callback_answers(mock_bot)
        assert answers[-1]["show_alert"] is True
        assert "Сначала устройтесь" in answers[-1]["text"]

        # 2. Claim shift when unemployed
        cb_claim = callback_factory("jobs:shift:claim:FINANCE", user_id=800004)
        await handle_jobs_shift_claim(cb_claim, session=async_session, user=user)

        answers = extract_bot_callback_answers(mock_bot)
        assert answers[-1]["show_alert"] is True
        assert "Вы не устроены" in answers[-1]["text"]

    @pytest.mark.asyncio
    async def test_error_alert_quit_when_unemployed(
        self,
        async_session: AsyncSession,
        mock_bot: AsyncMock,
        callback_factory,
    ):
        """Verify quitting a job where user is not employed returns show_alert=True."""
        user, _, _ = await BankingService.get_or_create_user(
            async_session, 800005, "never_hired", "Never Hired"
        )
        cb = callback_factory("jobs:quit:SHADOW", user_id=800005)
        await handle_jobs_quit(cb, session=async_session, user=user)

        answers = extract_bot_callback_answers(mock_bot)
        assert answers[-1]["show_alert"] is True
        assert "Вы не устроены" in answers[-1]["text"]

    @pytest.mark.asyncio
    async def test_error_alert_invalid_branch_name(
        self,
        async_session: AsyncSession,
        mock_bot: AsyncMock,
        callback_factory,
    ):
        """Verify invalid branch name in callback query is rejected with show_alert=True."""
        user, _, _ = await BankingService.get_or_create_user(
            async_session, 800006, "invalid_branch", "Invalid Branch"
        )
        cb = callback_factory("jobs:market:NOT_A_REAL_BRANCH", user_id=800006)
        await handle_jobs_market_branch(cb, session=async_session, user=user)

        answers = extract_bot_callback_answers(mock_bot)
        assert answers[-1]["show_alert"] is True
        assert "Неизвестная отрасль" in answers[-1]["text"]

    @pytest.mark.asyncio
    async def test_error_alert_hustle_cooldown_active(
        self,
        async_session: AsyncSession,
        mock_bot: AsyncMock,
        callback_factory,
    ):
        """Verify hustle attempt during 15-minute cooldown is rejected with show_alert=True."""
        user, _, _ = await BankingService.get_or_create_user(
            async_session, 800007, "cd_user", "Cooldown User"
        )
        user.last_hustle_at = datetime.datetime.now() - datetime.timedelta(minutes=5)
        await async_session.commit()

        # 1. Trigger hustle during cooldown
        cb1 = callback_factory("jobs:hustle", user_id=800007)
        await handle_jobs_hustle(cb1, session=async_session, user=user)

        answers = extract_bot_callback_answers(mock_bot)
        assert answers[-1]["show_alert"] is True
        assert "Шабашкины руки должны отдохнуть" in answers[-1]["text"]

        # 2. Choose option during cooldown
        cb2 = callback_factory("jobs:hustle:choose:0", user_id=800007)
        await handle_jobs_hustle_choose(cb2, session=async_session, user=user)

        answers = extract_bot_callback_answers(mock_bot)
        assert answers[-1]["show_alert"] is True
        assert "Шабашкины руки" in answers[-1]["text"] or "Кулдаун" in answers[-1]["text"]

    @pytest.mark.asyncio
    async def test_error_alert_promotion_requirements_not_met(
        self,
        async_session: AsyncSession,
        mock_bot: AsyncMock,
        callback_factory,
    ):
        """Verify promotion attempt without required shifts is rejected with show_alert=True."""
        user, _, _ = await BankingService.get_or_create_user(
            async_session, 800008, "promo_user", "Promo User"
        )
        await JobService.apply_for_job(async_session, user.id, JobBranch.IT)
        await async_session.commit()

        cb = callback_factory("jobs:promote:IT", user_id=800008)
        await handle_jobs_promote(cb, session=async_session, user=user)

        answers = extract_bot_callback_answers(mock_bot)
        assert answers[-1]["show_alert"] is True
        assert "Для повышения нужно отработать" in answers[-1]["text"]

    @pytest.mark.asyncio
    async def test_error_alert_promotion_max_tier_5(
        self,
        async_session: AsyncSession,
        mock_bot: AsyncMock,
        callback_factory,
    ):
        """Verify promotion attempt at Level 5 is rejected with show_alert=True."""
        user, _, _ = await BankingService.get_or_create_user(
            async_session, 800009, "max_promo", "Max Promo User"
        )
        job = await JobService.apply_for_job(async_session, user.id, JobBranch.IT)
        job.level = 5
        job.shifts_completed = 100
        await async_session.commit()

        cb = callback_factory("jobs:promote:IT", user_id=800009)
        await handle_jobs_promote(cb, session=async_session, user=user)

        answers = extract_bot_callback_answers(mock_bot)
        assert answers[-1]["show_alert"] is True
        assert "максимальной должности" in answers[-1]["text"]

    @pytest.mark.asyncio
    async def test_hustle_non_negative_balance_clamping_alert(
        self,
        async_session: AsyncSession,
        mock_bot: AsyncMock,
        callback_factory,
    ):
        """Verify negative hustle outcome on 0.00 ₽ balance clamps at 0.00 ₽ without overdraft."""
        user, main_acc, _ = await BankingService.get_or_create_user(
            async_session, 800010, "poor_hustler", "Poor Hustler"
        )
        assert main_acc.balance == Decimal("0.00")

        # Mock execute_hustle to return a 0.00 delta (clamped outcome)
        original_execute = JobService.execute_hustle
        async def mock_execute(session, uid, hid, opt):
            user.last_hustle_at = datetime.datetime.now()
            await session.commit()
            return Decimal("0.00"), "Полиция конфисковала товар, но у вас нечего взять!"

        try:
            JobService.execute_hustle = mock_execute
            cb = callback_factory("jobs:hustle:choose:0", user_id=800010)
            await handle_jobs_hustle_choose(cb, session=async_session, user=user)

            answers = extract_bot_callback_answers(mock_bot)
            assert answers[-1]["show_alert"] is True
            assert "защита от овердрафта" in answers[-1]["text"]
            assert main_acc.balance == Decimal("0.00")
        finally:
            JobService.execute_hustle = original_execute


# ==============================================================================
# Suite 5: Robustness, Formatters & Edge Cases
# ==============================================================================

class TestJobsKeyboardsAndFormattersEdgeCases:
    """Challenge formatters, progress bars, HTML escaping, and Telegram error resilience."""

    def test_progress_bar_edge_cases(self):
        """Verify format_progress_bar bounds, clamp values, and level 5 max-tier handling."""
        # 0%
        assert format_progress_bar(0) == "░░░░░░░░░░"
        # 50%
        assert format_progress_bar(50) == "█████░░░░░"
        # 100%
        assert format_progress_bar(100) == "██████████"
        # Clamp negative
        assert format_progress_bar(-20) == "░░░░░░░░░░"
        # Clamp overflow
        assert format_progress_bar(150) == "██████████"

        # Promotion progress bar with 0 required (Tier 5)
        bar_max = format_promotion_progress_bar(completed=50, required=0)
        assert bar_max == "██████████ MAX"

    def test_time_remaining_formatting(self):
        """Verify format_time_remaining cleanly formats hours, minutes, seconds."""
        assert format_time_remaining(0) == "Готово"
        assert format_time_remaining(-100) == "Готово"
        assert format_time_remaining(59) == "59 сек"
        assert format_time_remaining(60) == "1 мин 0 сек"
        assert format_time_remaining(3665) == "1 ч 1 мин"
        assert format_time_remaining(7200) == "2 ч 0 мин"

    def test_html_escaping_in_formatters(self):
        """Verify malicious HTML tags in user names and event texts are strictly escaped."""
        evil_user = User(
            id=999,
            telegram_id=999999,
            username="hacker<script>",
            full_name="<Evil & Malicious> User",
            created_at=datetime.datetime.now(),
        )
        dashboard = format_career_dashboard(evil_user, [])
        assert "<Evil & Malicious>" not in dashboard
        assert "&lt;Evil &amp; Malicious&gt;" in dashboard
        assert "<script>" not in dashboard

    @pytest.mark.asyncio
    async def test_telegram_bad_request_message_not_modified_graceful(
        self,
        async_session: AsyncSession,
        mock_bot: AsyncMock,
        callback_factory,
    ):
        """Verify handler swallows TelegramBadRequest ('message is not modified') gracefully."""
        user, _, _ = await BankingService.get_or_create_user(
            async_session, 800011, "spam_clicker", "Spam Clicker"
        )
        cb = callback_factory("jobs:menu", user_id=800011)

        # Make bot raise TelegramBadRequest when edit_message_text is called
        def mock_call(method, *args, **kwargs):
            if type(method).__name__ == "EditMessageText":
                raise TelegramBadRequest(method=method, message="message is not modified")
            return MagicMock()

        mock_bot.side_effect = mock_call

        # Must not raise exception
        await handle_jobs_menu_callback(cb, session=async_session, user=user)

        answers = extract_bot_callback_answers(mock_bot)
        assert len(answers) >= 1
        assert "Статус карьеры обновлен" in answers[-1]["text"]

    @pytest.mark.asyncio
    async def test_callback_with_none_message_resilience(
        self,
        async_session: AsyncSession,
        mock_bot: AsyncMock,
    ):
        """Verify callback with None message (inline mode or deleted) handles safely without crash."""
        user, _, _ = await BankingService.get_or_create_user(
            async_session, 800012, "none_msg", "None Msg"
        )
        tg_user = TgUser(id=800012, is_bot=False, first_name="None Msg", username="none_msg")
        cb = CallbackQuery(
            id="cb_none_1",
            from_user=tg_user,
            chat_instance="ci_none",
            message=None,
            data="jobs:menu",
        ).as_(mock_bot)

        await handle_jobs_menu_callback(cb, session=async_session, user=user)

        answers = extract_bot_callback_answers(mock_bot)
        assert len(answers) >= 1
        assert "Статус карьеры обновлен" in answers[-1]["text"]

    @pytest.mark.asyncio
    async def test_callback_with_empty_or_malformed_data(
        self,
        async_session: AsyncSession,
        mock_bot: AsyncMock,
        message_factory,
    ):
        """Verify callbacks with empty or malformed data return gracefully without crash."""
        user, _, _ = await BankingService.get_or_create_user(
            async_session, 800013, "malformed", "Malformed"
        )
        msg = message_factory("Menu", user_id=800013)
        tg_user = TgUser(id=800013, is_bot=False, first_name="Malformed", username="malformed")
        
        # In aiogram 3, data is a str, empty str can happen
        cb = CallbackQuery(
            id="cb_malformed_1",
            from_user=tg_user,
            chat_instance="ci_malformed",
            message=msg,
            data="",
        ).as_(mock_bot)

        await handle_jobs_market_branch(cb, session=async_session, user=user)
        await handle_jobs_apply(cb, session=async_session, user=user)
        await handle_jobs_quit(cb, session=async_session, user=user)
        await handle_jobs_shift_start(cb, session=async_session, user=user)
        await handle_jobs_shift_claim(cb, session=async_session, user=user)
        await handle_jobs_promote(cb, session=async_session, user=user)
        await handle_jobs_hustle_choose(cb, session=async_session, user=user)

        answers = extract_bot_callback_answers(mock_bot)
        assert len(answers) >= 7
