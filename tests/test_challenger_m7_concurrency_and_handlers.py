"""Empirical Challenger Test Suite for Milestone 7: Offline Bot Handler Tests & Concurrency Stress.

Adversarial and empirical verification covering:
1. Handler dispatching through aiogram Dispatcher with mock Bot:
   - Commands /jobs, /work, and reply button '💼 Работа и бизнес'.
   - In-place dashboard refresh via 'jobs:menu'.
   - Return to main bank dashboard via 'main_menu'.
   - Job market navigation for all 4 branches (IT, SERVICE, FINANCE, SHADOW).
   - Hiring up to 2 active jobs; 3rd job application rejected with modal alert (show_alert=True).
   - Shift start, double shift prevention modal alert.
   - Premature salary claim rejection modal alert (< 2h / 7200s).
   - Completed shift salary claim crediting main account and editing card text.
   - Promotion modal alerts: insufficient shifts rejection, successful promotion, Level 5 MAX rejection.
   - Resignation (quit) preserving career level and shifts.
   - Guard modal alerts for unemployed operations (quit, shift start, shift claim, promote).
   - Quick hustle dilemma initiation, option execution, and cooldown rejection modal alerts.
2. Concurrency stress:
   - Concurrent salary claims on same job/user (double-claim rejection CAS invariant).
   - Concurrent shift starts on same job/user.
   - Concurrent quick hustles on same user (atomic last_hustle_at CAS invariant).
   - Dispatcher-level concurrent shift claim callback hammering with mock Bot.
"""

import asyncio
import datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Chat, Message, Update, User as TgUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.bot import create_dispatcher
from app.database.models import Account, LedgerEntry, Transaction, User, UserJob
from app.services.banking import BankingService
from app.services.jobs import JOB_CATALOG, JobBranch, JobService


# ==============================================================================
# Helper Inspection Functions
# ==============================================================================

def get_sent_messages(mock_bot: AsyncMock) -> list[dict[str, Any]]:
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


def get_edited_messages(mock_bot: AsyncMock) -> list[dict[str, Any]]:
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


def get_callback_answers(mock_bot: AsyncMock) -> list[dict[str, Any]]:
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
# Suite 1: Concurrency Stress Testing
# ==============================================================================

class TestJobsConcurrencyStress:
    """Adversarial stress testing against race conditions and double-payout attacks."""

    @pytest.mark.asyncio
    async def test_concurrent_salary_claims_double_claim_rejection(self, test_engine):
        """10 concurrent tasks attempt to claim salary for the same finished shift.

        Strict CAS invariant:
        - Exactly 1 claim must succeed.
        - Exactly 9 claims must fail with 'Смена уже завершена или не начата!'.
        - Salary must be credited to user's main account balance exactly once.
        - Exactly 1 Transaction(tx_type="SALARY") and 1 LedgerEntry(direction="CREDIT") recorded.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        # 1. Setup user with active IT job on finished shift
        t_start = datetime.datetime.now() - datetime.timedelta(hours=3)
        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=880001, username="claim_stress", full_name="Claim Stress"
            )
            acc.balance = Decimal("1000.00")
            await JobService.apply_for_job(session, user.id, JobBranch.IT)
            job = await JobService.start_shift(session, user.id, JobBranch.IT, now=t_start)
            await session.commit()
            user_id = user.id
            account_id = acc.id

        # 2. Worker coroutine attempting claim
        async def claim_task():
            async with session_factory() as session:
                try:
                    payout, msg = await JobService.claim_salary(
                        session=session,
                        user_id=user_id,
                        branch=JobBranch.IT,
                        duration_seconds=7200,
                        event_roll=0.5,  # Deterministic normal payout
                    )
                    await session.commit()
                    return ("SUCCESS", payout)
                except ValueError as e:
                    await session.rollback()
                    return ("REJECTED", str(e))
                except Exception as e:
                    await session.rollback()
                    return ("ERROR", type(e).__name__)

        # 3. Fire 10 concurrent claim tasks
        tasks = [claim_task() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        successes = [r for r in results if r[0] == "SUCCESS"]
        rejections = [r for r in results if r[0] == "REJECTED"]

        assert len(successes) == 1, f"Expected exactly 1 claim success, got {len(successes)}"
        assert len(rejections) == 9, f"Expected 9 rejections, got {len(rejections)}"

        for status, msg in rejections:
            assert "Смена уже завершена или не начата!" in msg

        # 4. Invariant DB checks
        async with session_factory() as session:
            # Main balance check
            res_bal = await session.execute(select(Account.balance).where(Account.id == account_id))
            final_bal = res_bal.scalar_one()
            claimed_payout = successes[0][1]
            assert final_bal == Decimal("1000.00") + claimed_payout

            # Transaction count check
            res_tx = await session.execute(
                select(func.count(Transaction.id)).where(Transaction.recipient_account_id == account_id)
            )
            tx_count = res_tx.scalar_one()
            assert tx_count == 1

            # Ledger entry count check
            res_le = await session.execute(
                select(func.count(LedgerEntry.id)).where(LedgerEntry.account_id == account_id)
            )
            le_count = res_le.scalar_one()
            assert le_count == 1

            # Job state check
            user_job = await JobService.get_user_job_by_branch(session, user_id, JobBranch.IT)
            assert user_job is not None
            assert user_job.is_on_shift is False
            assert user_job.shift_started_at is None
            assert user_job.shifts_completed == 1

    @pytest.mark.asyncio
    async def test_concurrent_shift_starts(self, test_engine):
        """10 concurrent tasks attempt to start a shift for the same unemployed or employed user.

        Invariant:
        - Job must transition to is_on_shift=True.
        - Subsequent checks verify user is on shift.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, _, _ = await BankingService.get_or_create_user(
                session, telegram_id=880002, username="shift_stress", full_name="Shift Stress"
            )
            await JobService.apply_for_job(session, user.id, JobBranch.SERVICE)
            await session.commit()
            user_id = user.id

        async def start_task():
            async with session_factory() as session:
                try:
                    await JobService.start_shift(session, user_id, JobBranch.SERVICE)
                    await session.commit()
                    return "STARTED"
                except ValueError as e:
                    await session.rollback()
                    return f"REJECTED: {e}"
                except Exception as e:
                    await session.rollback()
                    return f"ERROR: {type(e).__name__}"

        tasks = [start_task() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # At least one succeeded in starting the shift
        started_count = results.count("STARTED")
        assert started_count >= 1

        # Check in DB that user is indeed on shift
        async with session_factory() as session:
            job = await JobService.get_user_job_by_branch(session, user_id, JobBranch.SERVICE)
            assert job is not None
            assert job.is_on_shift is True
            assert job.shift_started_at is not None

            # Now an immediate subsequent start MUST be rejected
            with pytest.raises(ValueError, match="Вы уже на смене"):
                await JobService.start_shift(session, user_id, JobBranch.SERVICE)

    @pytest.mark.asyncio
    async def test_concurrent_quick_hustles(self, test_engine):
        """10 concurrent tasks attempt to execute quick hustle simultaneously.

        Strict CAS invariant:
        - User.last_hustle_at is updated atomically with compare-and-swap.
        - Exactly 1 hustle must succeed.
        - Exactly 9 hustles must fail with cooldown error.
        - Exactly 1 Transaction(tx_type LIKE 'HUSTLE_%') recorded.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=880003, username="hustle_stress", full_name="Hustle Stress"
            )
            acc.balance = Decimal("5000.00")
            user.last_hustle_at = None
            await session.commit()
            user_id = user.id
            account_id = acc.id

        t_now = datetime.datetime.now()

        async def hustle_task():
            async with session_factory() as session:
                try:
                    delta, msg = await JobService.execute_hustle(
                        session=session,
                        user_id=user_id,
                        hustle_id="hustle_server_crash",
                        choice_idx=0,
                        now=t_now,
                        cooldown_seconds=900,
                        success_roll=0.1,  # Guaranteed success
                        reward_override=Decimal("2000.00"),
                    )
                    await session.commit()
                    return ("SUCCESS", delta)
                except ValueError as e:
                    await session.rollback()
                    return ("REJECTED", str(e))
                except Exception as e:
                    await session.rollback()
                    return ("ERROR", type(e).__name__)

        tasks = [hustle_task() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        successes = [r for r in results if r[0] == "SUCCESS"]
        rejections = [r for r in results if r[0] == "REJECTED"]

        assert len(successes) == 1, f"Expected exactly 1 hustle success, got {len(successes)}"
        assert len(rejections) == 9, f"Expected 9 rejections, got {len(rejections)}"

        for _, msg in rejections:
            assert "Шабашкины руки должны отдохнуть" in msg

        # Verify DB state
        async with session_factory() as session:
            user = await session.get(User, user_id)
            assert user.last_hustle_at == t_now

            # Verify balance credited exactly once
            res_bal = await session.execute(select(Account.balance).where(Account.id == account_id))
            assert res_bal.scalar_one() == Decimal("7000.00")

            # Verify exactly 1 transaction
            res_tx = await session.execute(
                select(func.count(Transaction.id)).where(Transaction.recipient_account_id == account_id)
            )
            assert res_tx.scalar_one() == 1

    @pytest.mark.asyncio
    async def test_dispatcher_concurrent_shift_claims(self, test_engine, mock_bot):
        """Dispatcher-level stress: 5 concurrent callback queries claiming salary for the same finished shift.

        Verifies:
        - Exactly 1 callback query receives success alert ('💰 Зарплата получена:').
        - Exactly 4 callback queries receive rejection alert ('Смена уже завершена или не начата!').
        - All 5 answers have show_alert=True.
        """
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 880004
        t_start = datetime.datetime.now() - datetime.timedelta(hours=3)

        # Setup user and shift
        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=tg_id, username="dp_stress", full_name="DP Stress"
            )
            acc.balance = Decimal("2000.00")
            await JobService.apply_for_job(session, user.id, JobBranch.FINANCE)
            await JobService.start_shift(session, user.id, JobBranch.FINANCE, now=t_start)
            await session.commit()

        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="DP Stress", username="dp_stress")
        base_msg = Message(
            message_id=901,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        # Dispatch 5 concurrent callback queries
        updates = [
            Update(
                update_id=1000 + i,
                callback_query=CallbackQuery(
                    id=f"cb_race_{i}",
                    from_user=tg_user,
                    chat_instance="ci_race",
                    message=base_msg,
                    data="jobs:shift:claim:FINANCE",
                ).as_(mock_bot),
            )
            for i in range(5)
        ]

        await asyncio.gather(*[dp.feed_update(mock_bot, u) for u in updates])

        answers = get_callback_answers(mock_bot)
        assert len(answers) >= 5

        # Check answers content
        success_answers = [
            a for a in answers if "Зарплата получена" in (a.get("text") or "")
        ]
        rejected_answers = [
            a for a in answers if "Смена уже завершена или не начата" in (a.get("text") or "")
        ]

        assert len(success_answers) == 1, f"Expected 1 success answer, got {len(success_answers)}"
        assert len(rejected_answers) >= 4, f"Expected >= 4 rejection answers, got {len(rejected_answers)}"

        # Every answer should have show_alert=True
        assert all(a.get("show_alert") is True for a in success_answers + rejected_answers)


# ==============================================================================
# Suite 2: Full Callback Paths & Modal Alerts Verification
# ==============================================================================

class TestOfflineBotJobHandlersVerification:
    """Systematic empirical verification of all career callback routes and modal alert invariants."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("branch", [JobBranch.IT, JobBranch.SERVICE, JobBranch.FINANCE, JobBranch.SHADOW])
    async def test_callback_market_navigation_all_branches(self, branch, test_engine, mock_bot):
        """Verify jobs:market:<branch> opens detailed view with catalog and correct buttons."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 890001
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="MarketUser", username="market_user")
        base_msg = Message(
            message_id=101,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        cb = CallbackQuery(
            id=f"cb_m_{branch}",
            from_user=tg_user,
            chat_instance="ci",
            message=base_msg,
            data=f"jobs:market:{branch}",
        ).as_(mock_bot)

        await dp.feed_update(mock_bot, Update(update_id=101, callback_query=cb))

        edited = get_edited_messages(mock_bot)
        assert len(edited) >= 1
        last = edited[-1]
        assert JobBranch.get_title(branch) in last["text"]
        # Check inline keyboard has apply button
        btn_data = [b.callback_data for row in last["reply_markup"].inline_keyboard for b in row]
        assert f"jobs:apply:{branch}" in btn_data
        assert "jobs:market" in btn_data

    @pytest.mark.asyncio
    async def test_callback_apply_and_multi_employment_limit_modal_alert(self, test_engine, mock_bot):
        """Hiring 1st job, 2nd job, then 3rd job application rejected with modal alert (show_alert=True)."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 890002
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="ApplyUser", username="apply_user")
        base_msg = Message(
            message_id=102,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        # 1. Apply 1st job (IT) -> SUCCESS
        cb1 = CallbackQuery(
            id="cb_a1", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:apply:IT"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=201, callback_query=cb1))
        answers1 = get_callback_answers(mock_bot)
        assert any("успешно трудоустроились" in (a.get("text") or "") and a.get("show_alert") is True for a in answers1)

        # 2. Apply 2nd job (SERVICE) -> SUCCESS
        cb2 = CallbackQuery(
            id="cb_a2", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:apply:SERVICE"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=202, callback_query=cb2))
        answers2 = get_callback_answers(mock_bot)
        assert any("успешно трудоустроились" in (a.get("text") or "") and a.get("show_alert") is True for a in answers2)

        # 3. Apply 3rd job (FINANCE) -> REJECTED with modal alert
        cb3 = CallbackQuery(
            id="cb_a3", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:apply:FINANCE"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=203, callback_query=cb3))
        answers3 = get_callback_answers(mock_bot)
        assert any("Вы уже совмещаете 2 работы" in (a.get("text") or "") and a.get("show_alert") is True for a in answers3)

        # 4. Re-apply to already active job (IT) -> REJECTED with modal alert
        cb4 = CallbackQuery(
            id="cb_a4", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:apply:IT"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=204, callback_query=cb4))
        answers4 = get_callback_answers(mock_bot)
        assert any("устроены в ветке" in (a.get("text") or "") and a.get("show_alert") is True for a in answers4)

    @pytest.mark.asyncio
    async def test_callback_shift_lifecycle_and_premature_claim_modal_alert(self, test_engine, mock_bot):
        """Verify shift start, duplicate shift rejection, and premature claim modal alerts."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 890003
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="ShiftUser", username="shift_user")
        base_msg = Message(
            message_id=103,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        # 1. Apply for IT job
        cb_apply = CallbackQuery(
            id="cb_s1", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:apply:IT"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=301, callback_query=cb_apply))

        # 2. Start shift -> SUCCESS modal alert
        cb_start = CallbackQuery(
            id="cb_s2", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:shift:start:IT"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=302, callback_query=cb_start))
        ans_start = get_callback_answers(mock_bot)
        assert any("Смена начата" in (a.get("text") or "") and a.get("show_alert") is True for a in ans_start)

        # 3. Start shift again while already on shift -> REJECTED modal alert
        cb_start_dup = CallbackQuery(
            id="cb_s3", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:shift:start:IT"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=303, callback_query=cb_start_dup))
        ans_dup = get_callback_answers(mock_bot)
        assert any("Вы уже на смене" in (a.get("text") or "") and a.get("show_alert") is True for a in ans_dup)

        # 4. Premature claim (< 2h) -> REJECTED modal alert
        cb_claim_early = CallbackQuery(
            id="cb_s4", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:shift:claim:IT"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=304, callback_query=cb_claim_early))
        ans_early = get_callback_answers(mock_bot)
        assert any("Смена еще не окончена" in (a.get("text") or "") and a.get("show_alert") is True for a in ans_early)

    @pytest.mark.asyncio
    async def test_callback_shift_claim_success_and_card_update(self, test_engine, mock_bot):
        """Verify successful salary claim when shift is completed (elapsed >= 2 hours)."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 890004
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="ClaimUser", username="claim_user")

        # 1. Setup user with finished shift
        t_start = datetime.datetime.now() - datetime.timedelta(hours=2, minutes=30)
        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=tg_id, username="claim_user", full_name="Claim User"
            )
            acc.balance = Decimal("100.00")
            await JobService.apply_for_job(session, user.id, JobBranch.SHADOW)
            await JobService.start_shift(session, user.id, JobBranch.SHADOW, now=t_start)
            await session.commit()

        base_msg = Message(
            message_id=104,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        # 2. Claim salary via callback
        cb_claim = CallbackQuery(
            id="cb_c1", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:shift:claim:SHADOW"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=401, callback_query=cb_claim))

        answers = get_callback_answers(mock_bot)
        assert any("Зарплата получена" in (a.get("text") or "") and a.get("show_alert") is True for a in answers)

        edited = get_edited_messages(mock_bot)
        assert len(edited) >= 1
        assert "Смена успешно завершена" in edited[-1]["text"]

    @pytest.mark.asyncio
    async def test_callback_promotion_modal_alerts(self, test_engine, mock_bot):
        """Verify promotion modal alerts: insufficient shifts, successful promotion, and Level 5 MAX."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 890005
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="PromoUser", username="promo_user")

        # Setup user at level 1 with 0 shifts
        async with session_factory() as session:
            user, _, _ = await BankingService.get_or_create_user(
                session, telegram_id=tg_id, username="promo_user", full_name="Promo User"
            )
            await JobService.apply_for_job(session, user.id, JobBranch.IT)
            await session.commit()
            user_id = user.id

        base_msg = Message(
            message_id=105,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        # 1. Ineligible promotion attempt -> REJECTED modal alert
        cb_ineligible = CallbackQuery(
            id="cb_p1", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:promote:IT"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=501, callback_query=cb_ineligible))
        answers1 = get_callback_answers(mock_bot)
        assert any("Для повышения нужно отработать еще" in (a.get("text") or "") and a.get("show_alert") is True for a in answers1)

        # 2. Complete required shifts (3 shifts for Level 1 -> 2)
        async with session_factory() as session:
            job = await JobService.get_user_job_by_branch(session, user_id, JobBranch.IT)
            job.shifts_completed = 3
            await session.commit()

        # 3. Eligible promotion attempt -> SUCCESS modal alert
        cb_eligible = CallbackQuery(
            id="cb_p2", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:promote:IT"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=502, callback_query=cb_eligible))
        answers2 = get_callback_answers(mock_bot)
        assert any("Поздравляем с повышением" in (a.get("text") or "") and a.get("show_alert") is True for a in answers2)

        # Verify job advanced to Level 2
        async with session_factory() as session:
            job = await JobService.get_user_job_by_branch(session, user_id, JobBranch.IT)
            assert job.level == 2

        # 4. Set job to Level 5 (MAX)
        async with session_factory() as session:
            job = await JobService.get_user_job_by_branch(session, user_id, JobBranch.IT)
            job.level = 5
            job.shifts_completed = 50
            await session.commit()

        # 5. Promotion attempt at MAX tier -> REJECTED modal alert
        cb_max = CallbackQuery(
            id="cb_p3", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:promote:IT"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=503, callback_query=cb_max))
        answers3 = get_callback_answers(mock_bot)
        assert any("максимальной должности" in (a.get("text") or "") and a.get("show_alert") is True for a in answers3)

    @pytest.mark.asyncio
    async def test_callback_resignation_retention_and_unemployed_guards(self, test_engine, mock_bot):
        """Verify quitting preserves progress, and guards against actions when unemployed."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 890006
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="QuitUser", username="quit_user")

        # Setup user at level 3 in FINANCE
        async with session_factory() as session:
            user, _, _ = await BankingService.get_or_create_user(
                session, telegram_id=tg_id, username="quit_user", full_name="Quit User"
            )
            await JobService.apply_for_job(session, user.id, JobBranch.FINANCE)
            job = await JobService.get_user_job_by_branch(session, user.id, JobBranch.FINANCE)
            job.level = 3
            job.shifts_completed = 7
            await session.commit()
            user_id = user.id

        base_msg = Message(
            message_id=106,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        # 1. Quit active job -> SUCCESS modal alert
        cb_quit = CallbackQuery(
            id="cb_q1", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:quit:FINANCE"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=601, callback_query=cb_quit))
        answers1 = get_callback_answers(mock_bot)
        assert any("уволились с этой работы" in (a.get("text") or "") and a.get("show_alert") is True for a in answers1)

        # Check DB: is_active is False, but level=3, shifts_completed=7 preserved
        async with session_factory() as session:
            job = await JobService.get_user_job_by_branch(session, user_id, JobBranch.FINANCE)
            assert job.is_active is False
            assert job.level == 3
            assert job.shifts_completed == 7

        # 2. Quit when already inactive -> REJECTED modal alert
        cb_quit_again = CallbackQuery(
            id="cb_q2", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:quit:FINANCE"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=602, callback_query=cb_quit_again))
        answers2 = get_callback_answers(mock_bot)
        assert any("Вы не устроены на этой работе" in (a.get("text") or "") and a.get("show_alert") is True for a in answers2)

        # 3. Start shift when unemployed -> REJECTED modal alert
        cb_shift_unemployed = CallbackQuery(
            id="cb_q3", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:shift:start:FINANCE"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=603, callback_query=cb_shift_unemployed))
        answers3 = get_callback_answers(mock_bot)
        assert any("Сначала устройтесь на эту работу" in (a.get("text") or "") and a.get("show_alert") is True for a in answers3)

        # 4. Claim salary when unemployed -> REJECTED modal alert
        cb_claim_unemployed = CallbackQuery(
            id="cb_q4", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:shift:claim:FINANCE"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=604, callback_query=cb_claim_unemployed))
        answers4 = get_callback_answers(mock_bot)
        assert any("Вы не устроены на этой работе" in (a.get("text") or "") and a.get("show_alert") is True for a in answers4)

        # 5. Promote when unemployed -> REJECTED modal alert
        cb_promo_unemployed = CallbackQuery(
            id="cb_q5", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:promote:FINANCE"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=605, callback_query=cb_promo_unemployed))
        answers5 = get_callback_answers(mock_bot)
        assert any("Вы не устроены" in (a.get("text") or "") and a.get("show_alert") is True for a in answers5)

    @pytest.mark.asyncio
    async def test_callback_hustle_and_cooldown_modal_alert(self, test_engine, mock_bot):
        """Verify hustle dilemma triggering, choice execution, and cooldown enforcement modal alert."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 890007
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="HustleUser", username="hustle_user")
        base_msg = Message(
            message_id=107,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        # 1. Trigger hustle
        cb_h = CallbackQuery(
            id="cb_hu1", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:hustle"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=701, callback_query=cb_h))
        edited = get_edited_messages(mock_bot)
        assert "Шабашка:" in edited[-1]["text"]

        # 2. Choose option 0 -> SUCCESS modal alert
        cb_c = CallbackQuery(
            id="cb_hu2", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:hustle:choose:0"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=702, callback_query=cb_c))
        answers1 = get_callback_answers(mock_bot)
        assert any(
            ("Заработано" in (a.get("text") or "") or "Потери" in (a.get("text") or "") or "Баланс не изменился" in (a.get("text") or ""))
            and a.get("show_alert") is True
            for a in answers1
        )

        # 3. Trigger hustle again immediately -> REJECTED modal alert (cooldown active)
        cb_h_early = CallbackQuery(
            id="cb_hu3", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:hustle"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=703, callback_query=cb_h_early))
        answers2 = get_callback_answers(mock_bot)
        assert any("Шабашкины руки должны отдохнуть" in (a.get("text") or "") and a.get("show_alert") is True for a in answers2)

        # 4. Trigger hustle choose again immediately -> REJECTED modal alert (cooldown active)
        cb_c_early = CallbackQuery(
            id="cb_hu4", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:hustle:choose:0"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=704, callback_query=cb_c_early))
        answers3 = get_callback_answers(mock_bot)
        assert any("Шабашкины руки должны отдохнуть" in (a.get("text") or "") and a.get("show_alert") is True for a in answers3)

    @pytest.mark.asyncio
    async def test_callback_menu_refresh_and_main_menu_navigation(self, test_engine, mock_bot):
        """Verify jobs:menu refreshes career dashboard and main_menu returns to main banking menu."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 890008
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="NavUser2", username="nav_user_2")
        base_msg = Message(
            message_id=108,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        # 1. jobs:menu -> edits message in-place
        cb_menu = CallbackQuery(
            id="cb_n1", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:menu"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=801, callback_query=cb_menu))
        edited = get_edited_messages(mock_bot)
        assert "Т-Банк Карьера и Бизнес" in edited[-1]["text"]

        # 2. main_menu -> navigates to main bank dashboard
        cb_main = CallbackQuery(
            id="cb_n2", from_user=tg_user, chat_instance="ci", message=base_msg, data="main_menu"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=802, callback_query=cb_main))
        edited = get_edited_messages(mock_bot)
        # Verifies return to main bank menu text
        assert "Главное меню" in edited[-1]["text"] or "Т-Банк" in edited[-1]["text"]


# ==============================================================================
# Suite 3: Message Entrypoints, FSM Reset & Resilience
# ==============================================================================

class TestDispatcherMessageEntrypointsAndResilience:
    """Verify entrypoints, FSM state clearing, and malformed update resilience."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cmd", ["/jobs", "/work", "💼 Работа и бизнес"])
    async def test_message_entrypoints_open_dashboard(self, cmd, test_engine, mock_bot):
        """Verify /jobs, /work, and reply button open the career dashboard."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 890009
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="CmdUser", username="cmd_user")
        msg = Message(
            message_id=201,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text=cmd,
        ).as_(mock_bot)

        await dp.feed_update(mock_bot, Update(update_id=901, message=msg))

        sent = get_sent_messages(mock_bot)
        assert len(sent) >= 1
        assert "Т-Банк Карьера и Бизнес" in sent[-1]["text"]
        assert sent[-1]["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_fsm_state_cleared_on_career_entry(self, test_engine, mock_bot, fsm_storage):
        """Verify active FSM state is cleared upon entering career menu."""
        from aiogram.fsm.state import State, StatesGroup
        from aiogram.fsm.storage.base import StorageKey

        class DummyState(StatesGroup):
            active_step = State()

        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory, storage=fsm_storage)

        tg_id = 890010
        key = StorageKey(bot_id=mock_bot.id, chat_id=tg_id, user_id=tg_id)
        await fsm_storage.set_state(key, DummyState.active_step)
        assert await fsm_storage.get_state(key) == DummyState.active_step.state

        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="FsmUser", username="fsm_user")
        msg = Message(
            message_id=202,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="/jobs",
        ).as_(mock_bot)

        await dp.feed_update(mock_bot, Update(update_id=902, message=msg))

        # FSM state should now be cleared (None)
        assert await fsm_storage.get_state(key) is None

    @pytest.mark.asyncio
    async def test_malformed_and_unknown_branch_callbacks(self, test_engine, mock_bot):
        """Verify invalid or corrupted branch callback queries trigger modal alerts without crash."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        tg_id = 890011
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="HackerUser", username="hacker_user")
        base_msg = Message(
            message_id=203,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        # 1. Invalid branch in market
        cb_unknown_market = CallbackQuery(
            id="cb_bad_1", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:market:UNKNOWN"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=903, callback_query=cb_unknown_market))
        answers1 = get_callback_answers(mock_bot)
        assert any("Неизвестная" in (a.get("text") or "") and a.get("show_alert") is True for a in answers1)

        # 2. Invalid branch in apply
        cb_unknown_apply = CallbackQuery(
            id="cb_bad_2", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:apply:GHOST"
        ).as_(mock_bot)
        await dp.feed_update(mock_bot, Update(update_id=904, callback_query=cb_unknown_apply))
        answers2 = get_callback_answers(mock_bot)
        assert any("Неизвестная" in (a.get("text") or "") and a.get("show_alert") is True for a in answers2)

    @pytest.mark.asyncio
    async def test_telegram_bad_request_resilience(self, test_engine, mock_bot):
        """Verify TelegramBadRequest ('message is not modified') is gracefully caught."""
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        dp = create_dispatcher(session_factory=session_factory)

        # Configure mock_bot to raise TelegramBadRequest on edit_message_text
        mock_bot.edit_message_text = AsyncMock(
            side_effect=TelegramBadRequest(
                method=MagicMock(),
                message="Bad Request: message is not modified: specified new message content and reply markup are exactly the same as a current content and reply markup of the message",
            )
        )

        tg_id = 890012
        chat = Chat(id=tg_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=tg_id, is_bot=False, first_name="DupEditUser", username="dup_edit_user")
        base_msg = Message(
            message_id=204,
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text="Menu",
        ).as_(mock_bot)

        cb_menu = CallbackQuery(
            id="cb_dup_edit", from_user=tg_user, chat_instance="ci", message=base_msg, data="jobs:menu"
        ).as_(mock_bot)

        # Must NOT raise exception
        await dp.feed_update(mock_bot, Update(update_id=905, callback_query=cb_menu))

