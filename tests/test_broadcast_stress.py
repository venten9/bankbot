"""Empirical Stress Tests: Broadcast Engine, Dispatch Loop Resilience, Audit Persistence, and HTML Escaping.

Author: Challenger 2 (Milestone 3 Gate)
Scope:
- Broadcast dispatch loop with mixed user outcomes (success, blocked, flood control retry, zero recipients, malformed HTML).
- Accurate delivery summary statistics arithmetic and persistent AdminLog verification.
- Rate-limiting delay verification and division-by-zero resilience.
- Comprehensive HTML escaping across all admin templates, ledger statements, audit logs, and push alerts.
"""

import asyncio
import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy import func, select

from app.bot.formatters.admin import (
    format_adjustment_confirmation,
    format_admin_audit_logs,
    format_admin_dashboard,
    format_admin_user_card,
    format_broadcast_preview,
    format_user_ledger_statement,
)
from app.bot.routers.admin import (
    handle_adjustment_confirm,
    handle_confirm_broadcast,
    handle_start_broadcast,
    process_broadcast_message,
)
from app.bot.states.states import BroadcastStates
from app.config import settings
from app.database.models import Account, AdminLog, Card, Transaction, User
from app.services.admin import AdminService
from app.services.banking import BankingService
from tests.test_admin_bot_handlers import (
    get_all_callback_answers,
    get_all_edited_texts,
    get_all_sent_texts,
    get_push_notifications_for_user,
)


# ==============================================================================
# 1. Stress Tests: Broadcast Dispatch Loop with Mixed User Outcomes
# ==============================================================================

class TestBroadcastDispatchMixedOutcomes:
    """Stress-test broadcast loop resilience under heterogeneous delivery outcomes."""

    @pytest.mark.asyncio
    async def test_broadcast_large_pool_mixed_outcomes(
        self, mock_bot, async_session, sample_admin
    ):
        """
        Stress-test broadcast with 100 recipients under mixed fault conditions:
        - 60 clean deliveries
        - 15 blocked users (TelegramForbiddenError)
        - 10 flood control hits (TelegramRetryAfter) that succeed on retry
        - 5 flood control hits (TelegramRetryAfter) that get blocked on retry
        - 5 flood control hits (TelegramRetryAfter) that fail with generic Exception on retry
        - 3 malformed requests (TelegramBadRequest)
        - 2 upstream server errors (TelegramAPIError)
        Total: 100 recipients.
        """
        total_recipients = 100
        recipient_ids = [100000 + i for i in range(total_recipients)]

        retry_attempted = set()

        async def dynamic_send_message(*args, **kwargs):
            chat_id = kwargs.get("chat_id") or (args[0] if args else None)
            idx = chat_id - 100000

            # 0..59: clean success
            if 0 <= idx < 60:
                return AsyncMock()

            # 60..74: user blocked bot initially
            elif 60 <= idx < 75:
                raise TelegramForbiddenError(
                    method=None, message="Forbidden: bot was blocked by the user"
                )

            # 75..84: RetryAfter -> retry succeeds
            elif 75 <= idx < 85:
                if chat_id not in retry_attempted:
                    retry_attempted.add(chat_id)
                    raise TelegramRetryAfter(
                        method=None, message="Too Many Requests", retry_after=0.001
                    )
                return AsyncMock()

            # 85..89: RetryAfter -> retry blocked (TelegramForbiddenError)
            elif 85 <= idx < 90:
                if chat_id not in retry_attempted:
                    retry_attempted.add(chat_id)
                    raise TelegramRetryAfter(
                        method=None, message="Too Many Requests", retry_after=0.001
                    )
                raise TelegramForbiddenError(
                    method=None, message="Forbidden: bot blocked on retry"
                )

            # 90..94: RetryAfter -> retry fails with Exception
            elif 90 <= idx < 95:
                if chat_id not in retry_attempted:
                    retry_attempted.add(chat_id)
                    raise TelegramRetryAfter(
                        method=None, message="Too Many Requests", retry_after=0.001
                    )
                raise RuntimeError("Connection reset during retry")

            # 95..97: BadRequest (e.g. invalid chat ID or deactivated account)
            elif 95 <= idx < 98:
                raise TelegramBadRequest(
                    method=None, message="Bad Request: chat not found"
                )

            # 98..99: TelegramAPIError
            elif 98 <= idx < 100:
                raise TelegramAPIError(
                    method=None, message="Telegram server error 502"
                )

            return AsyncMock()

        with patch.object(AdminService, "get_all_recipient_ids", return_value=recipient_ids):
            with patch.object(mock_bot, "send_message", side_effect=dynamic_send_message):
                summary = await AdminService.execute_broadcast(
                    bot=mock_bot,
                    session=async_session,
                    admin_telegram_id=sample_admin.telegram_id,
                    message_text="<b>Важное обновление системы безопасности Т-Банка</b>",
                    delay=0.0,
                )

        # Expected counts breakdown:
        # Sent: 60 (clean) + 10 (retry success) = 70
        # Blocked: 15 (initial) + 5 (retry blocked) = 20
        # Failed: 15 (initial blocked) + 5 (retry blocked) + 5 (retry exc) + 3 (bad request) + 2 (api error) = 30
        # Retried: 10 + 5 + 5 = 20
        # Total: 100
        assert summary["total"] == 100
        assert summary["sent"] == 70
        assert summary["blocked"] == 20
        assert summary["failed"] == 30
        assert summary["retried"] == 20

        # Invariant checks:
        assert summary["sent"] + summary["failed"] == summary["total"]
        assert summary["blocked"] <= summary["failed"]
        other_errors = summary["failed"] - summary["blocked"]
        assert other_errors == 10  # 5 retry exc + 3 bad request + 2 api error
        assert summary["sent"] + summary["blocked"] + other_errors == summary["total"]

    @pytest.mark.asyncio
    async def test_broadcast_all_blocked_users(
        self, mock_bot, async_session, sample_admin
    ):
        """Stress-test when 100% of recipient pool has blocked the bot."""
        recipients = [200001, 200002, 200003, 200004, 200005]

        with patch.object(AdminService, "get_all_recipient_ids", return_value=recipients):
            with patch.object(
                mock_bot,
                "send_message",
                side_effect=TelegramForbiddenError(
                    method=None, message="Forbidden: bot blocked"
                ),
            ):
                summary = await AdminService.execute_broadcast(
                    bot=mock_bot,
                    session=async_session,
                    admin_telegram_id=sample_admin.telegram_id,
                    message_text="Привет всем",
                    delay=0.0,
                )

        assert summary["total"] == 5
        assert summary["sent"] == 0
        assert summary["blocked"] == 5
        assert summary["failed"] == 5
        assert summary["retried"] == 0
        assert summary["effective_speed"] == 0.0

    @pytest.mark.asyncio
    async def test_broadcast_malformed_html_entity_exception(
        self, mock_bot, async_session, sample_admin
    ):
        """Verify that malformed HTML parsing error from Telegram API is trapped as TelegramBadRequest."""
        recipients = [300001, 300002]

        with patch.object(AdminService, "get_all_recipient_ids", return_value=recipients):
            with patch.object(
                mock_bot,
                "send_message",
                side_effect=TelegramBadRequest(
                    method=None, message="Bad Request: can't parse entities: unclosed tag"
                ),
            ):
                summary = await AdminService.execute_broadcast(
                    bot=mock_bot,
                    session=async_session,
                    admin_telegram_id=sample_admin.telegram_id,
                    message_text="<b>Невалидный HTML без закрывающего тега",
                    delay=0.0,
                )

        assert summary["total"] == 2
        assert summary["sent"] == 0
        assert summary["blocked"] == 0
        assert summary["failed"] == 2

    @pytest.mark.asyncio
    async def test_broadcast_unexpected_system_exceptions_graceful(
        self, mock_bot, async_session, sample_admin
    ):
        """Verify unexpected runtime exceptions (e.g. MemoryError, OSError) do not crash loop."""
        recipients = [400001, 400002, 400003]

        call_idx = 0

        async def faulty_send(*args, **kwargs):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                raise OSError("Socket read error")
            elif call_idx == 2:
                raise asyncio.TimeoutError("Gateway timeout")
            return AsyncMock()

        with patch.object(AdminService, "get_all_recipient_ids", return_value=recipients):
            with patch.object(mock_bot, "send_message", side_effect=faulty_send):
                summary = await AdminService.execute_broadcast(
                    bot=mock_bot,
                    session=async_session,
                    admin_telegram_id=sample_admin.telegram_id,
                    message_text="Проверка неожиданных сбоев",
                    delay=0.0,
                )

        assert summary["total"] == 3
        assert summary["sent"] == 1
        assert summary["failed"] == 2
        assert summary["blocked"] == 0


# ==============================================================================
# 2. Stress Tests: Zero Recipients Edge Cases
# ==============================================================================

class TestBroadcastZeroRecipients:
    """Verify zero-recipient edge cases across service and bot handlers."""

    @pytest.mark.asyncio
    async def test_service_zero_recipients_math_and_logging(
        self, mock_bot, async_session, sample_admin
    ):
        """Verify AdminService.execute_broadcast with zero recipients produces valid metrics without division by zero."""
        with patch.object(AdminService, "get_all_recipient_ids", return_value=[]):
            summary = await AdminService.execute_broadcast(
                bot=mock_bot,
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                message_text="Сообщение в никуда",
                delay=0.0,
            )

        assert summary["total"] == 0
        assert summary["sent"] == 0
        assert summary["blocked"] == 0
        assert summary["failed"] == 0
        assert summary["retried"] == 0
        assert summary["effective_speed"] == 0.0
        assert summary["duration_seconds"] >= 0.0

        # Verify audit log was recorded for zero-recipient broadcast
        await async_session.commit()
        log_res = await async_session.execute(
            select(AdminLog)
            .where(AdminLog.action == "BROADCAST")
            .order_by(AdminLog.created_at.desc())
        )
        log = log_res.scalars().first()
        assert log is not None
        assert "0/0 доставлено" in log.reason

    @pytest.mark.asyncio
    async def test_router_start_broadcast_zero_recipients_command_handled(
        self, mock_bot, message_factory, fsm_context_factory, async_session, sample_admin
    ):
        """When 0 users are registered, /broadcast command replies with alert and refuses to start FSM."""
        state = fsm_context_factory(sample_admin.telegram_id)

        with patch.object(AdminService, "get_all_recipient_ids", return_value=[]):
            msg = message_factory("/broadcast", user_id=sample_admin.telegram_id)
            await handle_start_broadcast(msg, session=async_session, state=state)

        # FSM state must remain None
        assert await state.get_state() is None

        # Bot sends error message
        sent_texts = get_all_sent_texts(mock_bot)
        assert any("нет зарегистрированных пользователей" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_router_start_broadcast_zero_recipients_callback_handled(
        self, mock_bot, callback_factory, fsm_context_factory, async_session, sample_admin
    ):
        """When 0 users are registered, inline button admin:broadcast:start triggers alert popup."""
        state = fsm_context_factory(sample_admin.telegram_id)

        with patch.object(AdminService, "get_all_recipient_ids", return_value=[]):
            cb = callback_factory("admin:broadcast:start", user_id=sample_admin.telegram_id)
            await handle_start_broadcast(cb, session=async_session, state=state)

        assert await state.get_state() is None
        answers = get_all_callback_answers(mock_bot)
        assert any(
            a.get("show_alert") is True and "нет зарегистрированных пользователей" in (a.get("text") or "")
            for a in answers
        )


# ==============================================================================
# 3. Delivery Summary Statistics Accuracy & Audit Log Persistence
# ==============================================================================

class TestBroadcastSummaryAndAuditPersistence:
    """Verify delivery report integrity and database audit log persistence."""

    @pytest.mark.asyncio
    async def test_audit_log_fields_and_persistence(
        self, mock_bot, async_session, sample_admin
    ):
        """Verify complete audit log entry fields in database."""
        test_msg = "Уважаемые клиенты Т-Банка! Напоминаем о правилах финансовой безопасности."
        recipients = [500001, 500002, 500003]

        with patch.object(AdminService, "get_all_recipient_ids", return_value=recipients):
            summary = await AdminService.execute_broadcast(
                bot=mock_bot,
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                message_text=test_msg,
                delay=0.0,
            )

        await async_session.commit()

        # Query audit log
        logs = await AdminService.get_admin_logs(async_session, limit=5)
        broadcast_logs = [l for l in logs if l.action == "BROADCAST"]
        assert len(broadcast_logs) >= 1
        entry = broadcast_logs[0]

        assert entry.admin_telegram_id == sample_admin.telegram_id
        assert entry.target_telegram_id is None
        assert entry.target_user_id is None
        assert entry.amount is None
        assert "3/3 доставлено" in entry.reason
        assert "0 заблокировано" in entry.reason
        assert "0 ошибок" in entry.reason
        assert test_msg[:50] in entry.reason

    @pytest.mark.asyncio
    async def test_router_confirm_broadcast_delivers_summary_report(
        self, mock_bot, callback_factory, fsm_context_factory, async_session, sample_admin
    ):
        """Verify that handle_confirm_broadcast sends formatted summary report back to administrator."""
        state = fsm_context_factory(sample_admin.telegram_id)
        await state.set_state(BroadcastStates.confirm_broadcast)
        await state.update_data(broadcast_text="<b>Новость дня!</b>")

        fake_summary = {
            "total": 50,
            "sent": 42,
            "blocked": 5,
            "failed": 8,  # 5 blocked + 3 other
            "retried": 3,
            "duration_seconds": 2.1,
            "effective_speed": 20.0,
        }

        with patch.object(AdminService, "execute_broadcast", return_value=fake_summary):
            cb = callback_factory("admin:broadcast:confirm", user_id=sample_admin.telegram_id)
            await handle_confirm_broadcast(
                cb, session=async_session, state=state, bot=mock_bot
            )

        assert await state.get_state() is None

        # Verify summary report message
        sent_texts = get_all_sent_texts(mock_bot)
        assert any("Рассылка успешно завершена" in t for t in sent_texts)
        report = next(t for t in sent_texts if "Рассылка успешно завершена" in t)
        assert "Всего адресатов:       <b>50</b>" in report
        assert "Успешно доставлено:    <b>42</b>" in report
        assert "Заблокировали бота:   <b>5</b>" in report
        assert "Прочих ошибок:         <b>3</b>" in report  # 8 failed - 5 blocked = 3


# ==============================================================================
# 4. Stress Tests: HTML Escaping & Injection Defense
# ==============================================================================

class TestAdminHtmlEscapingDefense:
    """Stress-test HTML escaping across all admin templates, views, and notifications."""

    def test_adjustment_confirmation_escapes_xss_payloads(self):
        """Verify format_adjustment_confirmation escapes script tags and attributes."""
        malicious_user = User(
            id=999,
            telegram_id=999999,
            username="<script>alert('hack')</script>",
            full_name="<img src=x onerror=alert('name')>",
        )
        malicious_reason = "<script>alert('pwned')</script> & <b>test</b> \"quote\""

        card = format_adjustment_confirmation(
            user=malicious_user,
            delta=Decimal("150.00"),
            reason=malicious_reason,
            before_balance=Decimal("1000.00"),
        )

        assert "<script>" not in card
        assert "<img src=x" not in card
        assert "&lt;script&gt;alert('hack')&lt;/script&gt;" in card
        assert "&lt;img src=x onerror=alert('name')&gt;" in card
        assert "&lt;script&gt;alert('pwned')&lt;/script&gt; &amp; &lt;b&gt;test&lt;/b&gt; \"quote\"" in card

    def test_user_card_escapes_malicious_user_and_tx(self):
        """Verify format_admin_user_card escapes customer details and transaction metadata."""
        user = User(
            id=123,
            telegram_id=123456,
            username="dangerous_user<script>",
            full_name="Evil <script>User</script>",
            created_at=datetime.datetime(2026, 1, 1, 12, 0),
        )
        overview = {
            "card": {"formatted_full": "2200 •••• •••• 1234", "expiry_date": "12/28", "cvv": "999", "is_frozen": False},
            "main_balance": "500.00",
            "savings_balance": "0.00",
            "savings_accrued_interest": "0.00",
            "cashback_balance": "0.00",
            "portfolio_value": "0.00",
        }
        recent_txs = [
            Transaction(
                id="tx-attack-12345678",
                sender_account_id=1,
                amount=Decimal("100.00"),
                category="<script>steal_cookies()</script>",
                description="<b onmouseover=evil()>Desc</b>",
                created_at=datetime.datetime(2026, 1, 1, 12, 30),
            )
        ]

        card_str = format_admin_user_card(user, overview, recent_txs, account_ids=[1])

        assert "<script>steal_cookies()</script>" not in card_str
        assert "<b onmouseover" not in card_str
        assert "&lt;script&gt;steal_cookies()&lt;/script&gt;" in card_str
        assert "&lt;b onmouseover=evil()&gt;" in card_str
        assert "&lt;script&gt;User&lt;/script&gt;" in card_str

    def test_ledger_statement_escapes_tx_fields(self):
        """Verify format_user_ledger_statement escapes transaction category and description."""
        user = User(
            id=123,
            telegram_id=123456,
            username="normal_user",
            full_name="Normal User",
            created_at=datetime.datetime(2026, 1, 1, 12, 0),
        )
        txs = [
            Transaction(
                id="tx-ledger-9999",
                sender_account_id=1,
                amount=Decimal("50.00"),
                category="<a href='http://phish.com'>Перевод</a>",
                description="<iframe src='http://evil.com'></iframe>",
                fiscal_code="ФП-123456",
                created_at=datetime.datetime(2026, 1, 1, 12, 45),
            )
        ]

        ledger_str = format_user_ledger_statement(user, txs, account_ids=[1])

        assert "<a href='http://phish.com'>" not in ledger_str
        assert "<iframe" not in ledger_str
        assert "&lt;a href='http://phish.com'&gt;Перевод&lt;/a&gt;" in ledger_str
        assert "&lt;iframe src='http://evil.com'&gt;&lt;/iframe&gt;" in ledger_str

    def test_admin_audit_logs_escapes_reason(self):
        """Verify format_admin_audit_logs escapes arbitrary administrator reasons."""
        logs = [
            AdminLog(
                id=1,
                admin_telegram_id=999,
                target_telegram_id=888,
                action="ADMIN_CREDIT",
                amount=Decimal("100.00"),
                reason="<script>alert('audit')</script> & <b>bold</b>",
                created_at=datetime.datetime(2026, 1, 1, 13, 0),
            )
        ]

        audit_str = format_admin_audit_logs(logs)

        assert "<script>" not in audit_str
        assert "&lt;script&gt;alert('audit')&lt;/script&gt; &amp; &lt;b&gt;bold&lt;/b&gt;" in audit_str

    @pytest.mark.asyncio
    async def test_balance_adjustment_push_notification_escapes_reason(
        self, mock_bot, callback_factory, fsm_context_factory, async_session, sample_admin, sample_user
    ):
        """Verify that when an admin enters reason containing HTML, the push notification sent to the client is escaped."""
        state = fsm_context_factory(sample_admin.telegram_id)
        await state.update_data(
            target_user_id=sample_user.id,
            target_telegram_id=sample_user.telegram_id,
            target_username=sample_user.username,
            target_full_name=sample_user.full_name,
            delta="500.00",
            reason="Бонус за участие в программе <script>alert(1)</script> & <b>акции</b>",
        )

        cb = callback_factory("admin:adjust:confirm", user_id=sample_admin.telegram_id)
        await handle_adjustment_confirm(
            cb, session=async_session, state=state, bot=mock_bot
        )

        user_pushes = get_push_notifications_for_user(mock_bot, sample_user.telegram_id)
        assert len(user_pushes) >= 1
        push_text = user_pushes[0]

        # Verify raw script tag is NEVER present in push
        assert "<script>" not in push_text
        assert "<b>акции</b>" not in push_text
        # Verify properly escaped entities
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in push_text
        assert "&lt;b&gt;акции&lt;/b&gt;" in push_text


# ==============================================================================
# 5. Stress Tests: Rate-Limiting, Boundary Conditions & Dashboard Edge Cases
# ==============================================================================

class TestBroadcastRateLimitingAndBoundaryStress:
    """Stress-test pacing delays, boundary text lengths, and extreme dashboard metrics."""

    @pytest.mark.asyncio
    async def test_broadcast_rate_limit_delay_applied(
        self, mock_bot, async_session, sample_admin
    ):
        """Verify that the pacing delay is actually respected between user dispatches."""
        recipients = [600001, 600002, 600003]
        target_delay = 0.02  # 20ms per message

        with patch.object(AdminService, "get_all_recipient_ids", return_value=recipients):
            summary = await AdminService.execute_broadcast(
                bot=mock_bot,
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                message_text="Проверка задержки",
                delay=target_delay,
            )

        assert summary["total"] == 3
        assert summary["sent"] == 3
        # With 3 recipients and delay=0.02s, minimum duration is 3 * 0.02s = 0.06s
        assert summary["duration_seconds"] >= 0.05
        assert summary["effective_speed"] > 0.0

    @pytest.mark.asyncio
    async def test_broadcast_message_length_boundary_4000_accepted(
        self, mock_bot, message_factory, fsm_context_factory, async_session, sample_admin
    ):
        """Verify message of exactly 4000 characters is accepted into confirmation state."""
        state = fsm_context_factory(sample_admin.telegram_id)
        await state.set_state(BroadcastStates.waiting_for_message)
        await state.update_data(recipient_count=1)

        msg_4000 = "A" * 4000
        msg = message_factory(msg_4000, user_id=sample_admin.telegram_id)
        await process_broadcast_message(msg, session=async_session, state=state)

        assert await state.get_state() == BroadcastStates.confirm_broadcast
        data = await state.get_data()
        assert data.get("broadcast_text") == msg_4000

    @pytest.mark.asyncio
    async def test_broadcast_message_length_boundary_4001_rejected(
        self, mock_bot, message_factory, fsm_context_factory, async_session, sample_admin
    ):
        """Verify message of 4001 characters is rejected with informative error."""
        state = fsm_context_factory(sample_admin.telegram_id)
        await state.set_state(BroadcastStates.waiting_for_message)
        await state.update_data(recipient_count=1)

        msg_4001 = "B" * 4001
        msg = message_factory(msg_4001, user_id=sample_admin.telegram_id)
        await process_broadcast_message(msg, session=async_session, state=state)

        # Must remain in waiting_for_message
        assert await state.get_state() == BroadcastStates.waiting_for_message
        sent_texts = get_all_sent_texts(mock_bot)
        assert any("слишком длинное (4001 симв.)" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_broadcast_audit_log_reason_truncation(
        self, mock_bot, async_session, sample_admin
    ):
        """Verify that long broadcast text is safely truncated in AdminLog reason to prevent DB row bloat."""
        long_text = "C" * 800
        recipients = [700001]

        with patch.object(AdminService, "get_all_recipient_ids", return_value=recipients):
            await AdminService.execute_broadcast(
                bot=mock_bot,
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                message_text=long_text,
                delay=0.0,
            )

        await async_session.commit()
        logs = await AdminService.get_admin_logs(async_session, limit=1)
        assert len(logs) >= 1
        log = logs[0]
        # Text snippet must be truncated to 120 chars
        expected_snippet = f"Текст: {'C' * 120}"
        assert expected_snippet in log.reason
        assert ('C' * 121) not in log.reason

    def test_admin_metrics_dashboard_zero_and_large_values(self):
        """Verify format_admin_dashboard handles zero transactions without ZeroDivisionError and formats large values."""
        # 1. Zero state
        zero_metrics = {
            "total_users": 0,
            "active_accounts": 0,
            "frozen_accounts": 0,
            "total_liquidity": Decimal("0.00"),
            "total_transactions": 0,
            "total_volume": Decimal("0.00"),
        }
        zero_text = format_admin_dashboard(zero_metrics)
        assert "<b>Средний чек операции:</b>     <code>0.00 ₽</code>" in zero_text
        assert "<b>Всего пользователей:</b>      <code>0</code>" in zero_text

        # 2. Large volume state
        large_metrics = {
            "total_users": 1500000,
            "active_accounts": 1490000,
            "frozen_accounts": 10000,
            "total_liquidity": Decimal("25000000000.50"),
            "total_transactions": 30000000,
            "total_volume": Decimal("90000000000.00"),
        }
        large_text = format_admin_dashboard(large_metrics)
        assert "1,500,000" in large_text
        assert "25,000,000,000.50 ₽" in large_text
        assert "3,000.00 ₽" in large_text  # 90B / 30M = 3000.00 avg check
