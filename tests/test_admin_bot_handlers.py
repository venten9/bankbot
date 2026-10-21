"""Offline Bot Handlers & Comprehensive Integration Tests for Admin Panel (/admin).

Covers:
- Dual-Layer RBAC: rejection of non-admin commands and callback queries with 403 alert, IsAdminFilter.
- Admin Dashboard: /admin command, metrics display, metrics refresh callback, exit callback.
- User Search: Search by username, Telegram ID, 16-digit card number, phone, and nonexistent handling.
- User Inspection: Full card profile, balances, recent transactions, and ledger statements.
- Freeze / Unfreeze: Account and card freezing/unfreezing, audit logging, push notifications.
- Balance Adjustments: FSM credit and debit, reason length validation (5-255 chars), debit overdraft check, HTML escaping, push alerts.
- Broadcast Wizard: FSM message input, preview, cancellation, confirmation, rate-limited dispatch loop, exception handling, and audit logs.
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy import select

from app.bot.filters.admin import IsAdminFilter
from app.bot.routers.admin import (
    handle_admin_balance_initiate,
    handle_admin_dashboard,
    handle_admin_dashboard_callback,
    handle_admin_exit,
    handle_admin_toggle_freeze_user,
    handle_adjustment_cancel,
    handle_adjustment_confirm,
    handle_audit_logs,
    handle_cancel_broadcast,
    handle_confirm_broadcast,
    handle_metrics_refresh,
    handle_start_broadcast,
    handle_unauthorized_admin_callback,
    handle_unauthorized_admin_command,
    handle_user_ledger,
    handle_user_search_prompt,
    handle_user_view,
    process_balance_delta,
    process_balance_reason,
    process_broadcast_message,
    process_user_search_query,
)
from app.bot.states.states import AdminStates, BroadcastStates
from app.config import settings
from app.database.models import Account, AdminLog, Card, Transaction, User
from app.services.admin import AdminService
from app.services.banking import BankingService


# ==============================================================================
# Test Inspection Helpers
# ==============================================================================

def get_all_sent_texts(mock_bot) -> list[str]:
    """Collect all text payloads sent via bot.send_message or message.answer / bot(SendMessage)."""
    texts = []
    # 1. Direct bot.send_message calls
    for call in mock_bot.send_message.call_args_list:
        args, kwargs = call
        if "text" in kwargs:
            texts.append(kwargs["text"])
        elif len(args) > 1 and isinstance(args[1], str):
            texts.append(args[1])
    # 2. Callable bot(SendMessage(...)) calls
    for call in mock_bot.call_args_list:
        if call[0]:
            method = call[0][0]
            if type(method).__name__ == "SendMessage" and hasattr(method, "text") and method.text is not None:
                texts.append(method.text)
    return texts


def get_all_edited_texts(mock_bot) -> list[str]:
    """Collect all text payloads from edit_message_text calls."""
    texts = []
    # 1. Direct bot.edit_message_text calls
    for call in mock_bot.edit_message_text.call_args_list:
        args, kwargs = call
        if "text" in kwargs:
            texts.append(kwargs["text"])
    # 2. Callable bot(EditMessageText(...)) calls
    for call in mock_bot.call_args_list:
        if call[0]:
            method = call[0][0]
            if type(method).__name__ == "EditMessageText" and hasattr(method, "text") and method.text is not None:
                texts.append(method.text)
    return texts


def get_all_callback_answers(mock_bot) -> list[dict]:
    """Collect all callback query answer payloads."""
    answers = []
    for call in mock_bot.answer_callback_query.call_args_list:
        answers.append(call[1])
    for call in mock_bot.call_args_list:
        if call[0]:
            method = call[0][0]
            if type(method).__name__ == "AnswerCallbackQuery":
                answers.append({
                    "text": getattr(method, "text", None),
                    "show_alert": getattr(method, "show_alert", None),
                })
    return answers


def get_push_notifications_for_user(mock_bot, user_id: int) -> list[str]:
    """Collect all push messages specifically dispatched to a given user chat ID."""
    notifs = []
    for call in mock_bot.send_message.call_args_list:
        args, kwargs = call
        chat_id = kwargs.get("chat_id") or (args[0] if args else None)
        if chat_id == user_id:
            text = kwargs.get("text") or (args[1] if len(args) > 1 else "")
            notifs.append(text)
    for call in mock_bot.call_args_list:
        if call[0]:
            method = call[0][0]
            if type(method).__name__ == "SendMessage" and getattr(method, "chat_id", None) == user_id:
                notifs.append(getattr(method, "text", ""))
    return notifs


# ==============================================================================
# 1. RBAC and Dashboard Handlers Tests
# ==============================================================================

class TestAdminRbacAndDashboardHandlers:
    """Verify RBAC 403 barriers and dashboard rendering."""

    @pytest.mark.asyncio
    async def test_unauthorized_user_command_blocked(
        self, mock_bot, message_factory, async_session, fsm_context_factory, sample_user
    ):
        """Unauthorized non-admin receives 403 denial message on /admin."""
        non_admin_id = 7777777
        assert non_admin_id not in settings.ADMIN_IDS

        # Test IsAdminFilter directly
        filter_obj = IsAdminFilter()
        msg_unauth = message_factory("/admin", user_id=non_admin_id)
        assert await filter_obj(msg_unauth) is False

        # Test handler fallback
        await handle_unauthorized_admin_command(msg_unauth)
        sent_texts = get_all_sent_texts(mock_bot)
        assert len(sent_texts) >= 1
        assert any("Доступ запрещен" in t for t in sent_texts)
        assert any("Олег" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_unauthorized_callback_rejected(
        self, mock_bot, callback_factory
    ):
        """Unauthorized callback queries prefixed with admin: receive 403 alert popup."""
        non_admin_id = 8888888
        assert non_admin_id not in settings.ADMIN_IDS

        cb_unauth = callback_factory("admin:metrics:refresh", user_id=non_admin_id)
        filter_obj = IsAdminFilter()
        assert await filter_obj(cb_unauth) is False

        await handle_unauthorized_admin_callback(cb_unauth)
        answers = get_all_callback_answers(mock_bot)
        assert len(answers) >= 1
        assert any(a.get("show_alert") is True and "Доступ запрещен" in (a.get("text") or "") for a in answers)

    @pytest.mark.asyncio
    async def test_authorized_admin_dashboard_rendered(
        self, mock_bot, message_factory, async_session, fsm_context_factory, sample_admin
    ):
        """Authorized administrator receives live system metrics dashboard."""
        state = fsm_context_factory(sample_admin.telegram_id)
        msg_admin = message_factory("/admin", user_id=sample_admin.telegram_id)

        filter_obj = IsAdminFilter()
        assert await filter_obj(msg_admin) is True

        await handle_admin_dashboard(msg_admin, session=async_session, state=state)
        sent_texts = get_all_sent_texts(mock_bot)
        assert len(sent_texts) >= 1
        dashboard_text = sent_texts[0]
        assert "Панель Управления" in dashboard_text
        assert "Всего пользователей" in dashboard_text
        assert "Агрегированная ликвидность" in dashboard_text
        assert "Статистика транзакций" in dashboard_text

    @pytest.mark.asyncio
    async def test_admin_metrics_refresh_callback(
        self, mock_bot, callback_factory, async_session, sample_admin
    ):
        """Admin metrics refresh callback edits dashboard message in place."""
        cb_refresh = callback_factory("admin:metrics:refresh", user_id=sample_admin.telegram_id)
        await handle_metrics_refresh(cb_refresh, session=async_session)

        edited_texts = get_all_edited_texts(mock_bot)
        assert len(edited_texts) >= 1
        assert "Панель Управления" in edited_texts[0]

        answers = get_all_callback_answers(mock_bot)
        assert any("Метрики обновлены" in (a.get("text") or "") for a in answers)

    @pytest.mark.asyncio
    async def test_admin_exit_callback(
        self, mock_bot, callback_factory, fsm_context_factory, sample_admin
    ):
        """Admin exit callback clears state and presents user banking main menu."""
        state = fsm_context_factory(sample_admin.telegram_id)
        await state.set_state(AdminStates.search_query)

        cb_exit = callback_factory("admin:exit", user_id=sample_admin.telegram_id)
        await handle_admin_exit(cb_exit, state=state)

        assert await state.get_state() is None
        answers = get_all_callback_answers(mock_bot)
        assert any("Вы вышли" in (a.get("text") or "") for a in answers)


# ==============================================================================
# 2. User Search and Inspection Handlers Tests
# ==============================================================================

class TestAdminUserSearchAndInspectionHandlers:
    """Verify user lookup, card inspection, freeze/unfreeze lifecycle, and ledger."""

    @pytest.mark.asyncio
    async def test_search_prompt_and_cancel_flow(
        self, mock_bot, message_factory, callback_factory, fsm_context_factory, async_session, sample_admin
    ):
        """Search prompt sets state, and cancel returns cleanly to dashboard."""
        state = fsm_context_factory(sample_admin.telegram_id)
        cb_search = callback_factory("admin:user:search", user_id=sample_admin.telegram_id)

        await handle_user_search_prompt(cb_search, state=state)
        assert await state.get_state() == AdminStates.search_query

        # Send cancel
        msg_cancel = message_factory("❌ Отмена", user_id=sample_admin.telegram_id)
        await process_user_search_query(msg_cancel, session=async_session, state=state)
        assert await state.get_state() is None

    @pytest.mark.asyncio
    async def test_search_resolve_by_username(
        self, mock_bot, message_factory, fsm_context_factory, async_session, sample_admin, sample_user
    ):
        """Search query resolves user by @username and renders inspection card."""
        state = fsm_context_factory(sample_admin.telegram_id)
        await state.set_state(AdminStates.search_query)

        msg_query = message_factory(f"@{sample_user.username}", user_id=sample_admin.telegram_id)
        await process_user_search_query(msg_query, session=async_session, state=state)

        assert await state.get_state() is None
        sent_texts = get_all_sent_texts(mock_bot)
        assert len(sent_texts) >= 1
        card_text = sent_texts[0]
        assert sample_user.full_name in card_text
        assert str(sample_user.telegram_id) in card_text
        assert "Карта Т-Банк Black" in card_text
        assert "Балансы клиента" in card_text

    @pytest.mark.asyncio
    async def test_search_resolve_by_numeric_id_and_card(
        self, mock_bot, message_factory, fsm_context_factory, async_session, sample_admin, sample_user
    ):
        """Search query resolves user by numeric ID and 16-digit card number."""
        state = fsm_context_factory(sample_admin.telegram_id)

        # 1. By Telegram ID
        await state.set_state(AdminStates.search_query)
        msg_id = message_factory(str(sample_user.telegram_id), user_id=sample_admin.telegram_id)
        await process_user_search_query(msg_id, session=async_session, state=state)
        assert await state.get_state() is None

        # 2. By 16-digit card number
        card_num = sample_user.cards[0].card_number
        await state.set_state(AdminStates.search_query)
        msg_card = message_factory(card_num, user_id=sample_admin.telegram_id)
        await process_user_search_query(msg_card, session=async_session, state=state)
        assert await state.get_state() is None

    @pytest.mark.asyncio
    async def test_search_nonexistent_user(
        self, mock_bot, message_factory, fsm_context_factory, async_session, sample_admin
    ):
        """Searching a nonexistent user reports error and retains search state."""
        state = fsm_context_factory(sample_admin.telegram_id)
        await state.set_state(AdminStates.search_query)

        msg_ghost = message_factory("@nonexistent_ghost_99999", user_id=sample_admin.telegram_id)
        await process_user_search_query(msg_ghost, session=async_session, state=state)

        assert await state.get_state() == AdminStates.search_query
        sent_texts = get_all_sent_texts(mock_bot)
        assert any("Пользователь не найден" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_admin_freeze_and_unfreeze_user_callback(
        self, mock_bot, callback_factory, async_session, sample_admin, sample_user
    ):
        """Admin freezes client account/cards with client notification, then unfreezes."""
        user_id = sample_user.id

        # 1. Freeze user
        cb_freeze = callback_factory(f"admin:user:freeze:{user_id}", user_id=sample_admin.telegram_id)
        await handle_admin_toggle_freeze_user(cb_freeze, session=async_session, bot=mock_bot)

        await async_session.refresh(sample_user)
        assert sample_user.is_blocked is True
        for acc in sample_user.accounts:
            await async_session.refresh(acc)
            assert acc.is_frozen is True

        # Check client notification
        freeze_notifs = get_push_notifications_for_user(mock_bot, sample_user.telegram_id)
        assert len(freeze_notifs) >= 1
        assert any("временно заморожен" in t for t in freeze_notifs)

        # Check audit log
        logs_res = await async_session.execute(
            select(AdminLog).where(AdminLog.target_user_id == user_id).order_by(AdminLog.created_at.desc())
        )
        latest_log = logs_res.scalars().first()
        assert latest_log.action == "FREEZE_USER"

        # 2. Unfreeze user
        cb_unfreeze = callback_factory(f"admin:user:unfreeze:{user_id}", user_id=sample_admin.telegram_id)
        await handle_admin_toggle_freeze_user(cb_unfreeze, session=async_session, bot=mock_bot)

        await async_session.refresh(sample_user)
        assert sample_user.is_blocked is False
        for acc in sample_user.accounts:
            await async_session.refresh(acc)
            assert acc.is_frozen is False

        unfreeze_notifs = get_push_notifications_for_user(mock_bot, sample_user.telegram_id)
        assert any("разморожен" in t for t in unfreeze_notifs)

    @pytest.mark.asyncio
    async def test_admin_user_ledger_and_audit_logs(
        self, mock_bot, callback_factory, async_session, sample_admin, sample_user
    ):
        """Admin views full ledger statement and audit logs."""
        # 1. Ledger statement
        cb_ledger = callback_factory(f"admin:user:ledger:{sample_user.id}", user_id=sample_admin.telegram_id)
        await handle_user_ledger(cb_ledger, session=async_session)
        edited_texts = get_all_edited_texts(mock_bot)
        assert any("Выписка операций клиента" in t for t in edited_texts)

        # 2. Audit logs view
        cb_audit = callback_factory("admin:audit:log", user_id=sample_admin.telegram_id)
        await handle_audit_logs(cb_audit, session=async_session)
        edited_texts = get_all_edited_texts(mock_bot)
        assert any("Журнал аудита" in t for t in edited_texts)


# ==============================================================================
# 3. Balance Adjustment Handlers Tests
# ==============================================================================

class TestAdminBalanceAdjustmentHandlers:
    """Verify FSM credit and debit, reason validation, overdraft checks, and client push."""

    @pytest.mark.asyncio
    async def test_credit_fsm_flow_with_audit_and_notification(
        self, mock_bot, message_factory, callback_factory, fsm_context_factory, async_session, sample_admin, sample_user
    ):
        """Admin credits 1,500.00 ₽ with reason, verifies balance increase and client push alert."""
        state = fsm_context_factory(sample_admin.telegram_id)

        # 1. Initiate Credit
        cb_credit = callback_factory(f"admin:user:credit:{sample_user.id}", user_id=sample_admin.telegram_id)
        await handle_admin_balance_initiate(cb_credit, session=async_session, state=state)
        assert await state.get_state() == AdminStates.balance_delta

        # 2. Enter amount
        msg_amt = message_factory("1500.00", user_id=sample_admin.telegram_id)
        await process_balance_delta(msg_amt, session=async_session, state=state)
        assert await state.get_state() == AdminStates.balance_reason

        # 3. Enter reason
        reason_text = "Премия за активное бета-тестирование"
        msg_reason = message_factory(reason_text, user_id=sample_admin.telegram_id)
        await process_balance_reason(msg_reason, session=async_session, state=state)

        # 4. Confirm adjustment
        init_balance = sample_user.accounts[0].balance
        cb_confirm = callback_factory("admin:adjust:confirm", user_id=sample_admin.telegram_id)
        await handle_adjustment_confirm(cb_confirm, session=async_session, state=state, bot=mock_bot)

        # Verify state cleared
        assert await state.get_state() is None

        # Verify balance updated
        await async_session.refresh(sample_user.accounts[0])
        assert sample_user.accounts[0].balance == init_balance + Decimal("1500.00")

        # Verify audit log recorded
        log_res = await async_session.execute(
            select(AdminLog).where(AdminLog.target_user_id == sample_user.id).order_by(AdminLog.created_at.desc())
        )
        log_entry = log_res.scalars().first()
        assert log_entry.action == "ADMIN_CREDIT"
        assert log_entry.reason == reason_text

        # Verify push notification dispatched to target user
        user_notifs = get_push_notifications_for_user(mock_bot, sample_user.telegram_id)
        assert len(user_notifs) >= 1
        assert any("+1,500.00 ₽" in t for t in user_notifs)
        assert any(reason_text in t for t in user_notifs)

    @pytest.mark.asyncio
    async def test_debit_fsm_flow_with_audit_and_notification(
        self, mock_bot, message_factory, callback_factory, fsm_context_factory, async_session, sample_admin, sample_user
    ):
        """Admin debits 500.00 ₽ with reason, verifies balance decrease and client push alert."""
        state = fsm_context_factory(sample_admin.telegram_id)
        init_balance = sample_user.accounts[0].balance

        # 1. Initiate Debit
        cb_debit = callback_factory(f"admin:user:debit:{sample_user.id}", user_id=sample_admin.telegram_id)
        await handle_admin_balance_initiate(cb_debit, session=async_session, state=state)

        # 2. Amount
        msg_amt = message_factory("500.00", user_id=sample_admin.telegram_id)
        await process_balance_delta(msg_amt, session=async_session, state=state)

        # 3. Reason
        msg_reason = message_factory("Корректировка ошибочного начисления", user_id=sample_admin.telegram_id)
        await process_balance_reason(msg_reason, session=async_session, state=state)

        # 4. Confirm
        cb_confirm = callback_factory("admin:adjust:confirm", user_id=sample_admin.telegram_id)
        await handle_adjustment_confirm(cb_confirm, session=async_session, state=state, bot=mock_bot)

        await async_session.refresh(sample_user.accounts[0])
        assert sample_user.accounts[0].balance == init_balance - Decimal("500.00")

        # Verify push notification
        user_notifs = get_push_notifications_for_user(mock_bot, sample_user.telegram_id)
        assert any("-500.00 ₽" in t for t in user_notifs)

    @pytest.mark.asyncio
    async def test_debit_overdraft_pre_check(
        self, mock_bot, message_factory, callback_factory, fsm_context_factory, async_session, sample_admin, sample_user
    ):
        """Debit amount exceeding current balance is rejected and state preserved."""
        state = fsm_context_factory(sample_admin.telegram_id)

        cb_debit = callback_factory(f"admin:user:debit:{sample_user.id}", user_id=sample_admin.telegram_id)
        await handle_admin_balance_initiate(cb_debit, session=async_session, state=state)

        # Client has 10,000.00 ₽; try debiting 999,999.00 ₽
        msg_amt = message_factory("999999.00", user_id=sample_admin.telegram_id)
        await process_balance_delta(msg_amt, session=async_session, state=state)

        assert await state.get_state() == AdminStates.balance_delta
        sent_texts = get_all_sent_texts(mock_bot)
        assert any("Недостаточно средств для списания" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_reason_validation_boundaries(
        self, mock_bot, message_factory, callback_factory, fsm_context_factory, async_session, sample_admin, sample_user
    ):
        """Reason length <5 or >255 characters is rejected with prompt."""
        state = fsm_context_factory(sample_admin.telegram_id)

        cb_credit = callback_factory(f"admin:user:credit:{sample_user.id}", user_id=sample_admin.telegram_id)
        await handle_admin_balance_initiate(cb_credit, session=async_session, state=state)

        msg_amt = message_factory("100.00", user_id=sample_admin.telegram_id)
        await process_balance_delta(msg_amt, session=async_session, state=state)

        # 1. Too short (< 5 chars)
        msg_short = message_factory("ok", user_id=sample_admin.telegram_id)
        await process_balance_reason(msg_short, session=async_session, state=state)
        assert await state.get_state() == AdminStates.balance_reason
        sent_texts = get_all_sent_texts(mock_bot)
        assert any("слишком короткая" in t for t in sent_texts)

        # 2. Too long (> 255 chars)
        msg_long = message_factory("A" * 260, user_id=sample_admin.telegram_id)
        await process_balance_reason(msg_long, session=async_session, state=state)
        assert await state.get_state() == AdminStates.balance_reason
        sent_texts = get_all_sent_texts(mock_bot)
        assert any("слишком длинная" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_reason_html_escaping(
        self, mock_bot, message_factory, callback_factory, fsm_context_factory, async_session, sample_admin, sample_user
    ):
        """Reason containing raw HTML tags is escaped via html.escape(..., quote=False)."""
        state = fsm_context_factory(sample_admin.telegram_id)

        cb_credit = callback_factory(f"admin:user:credit:{sample_user.id}", user_id=sample_admin.telegram_id)
        await handle_admin_balance_initiate(cb_credit, session=async_session, state=state)

        msg_amt = message_factory("50.00", user_id=sample_admin.telegram_id)
        await process_balance_delta(msg_amt, session=async_session, state=state)

        malicious_reason = "<script>alert('xss')</script> & <b>test</b>"
        msg_reason = message_factory(malicious_reason, user_id=sample_admin.telegram_id)
        await process_balance_reason(msg_reason, session=async_session, state=state)

        # Confirmation text must be escaped
        sent_texts = get_all_sent_texts(mock_bot)
        confirm_call = sent_texts[-1]
        assert "&lt;script&gt;" in confirm_call
        assert "<script>" not in confirm_call
        assert "&amp;" in confirm_call

        # Confirm and check push message
        cb_confirm = callback_factory("admin:adjust:confirm", user_id=sample_admin.telegram_id)
        await handle_adjustment_confirm(cb_confirm, session=async_session, state=state, bot=mock_bot)

        user_notifs = get_push_notifications_for_user(mock_bot, sample_user.telegram_id)
        assert len(user_notifs) >= 1
        push_text = user_notifs[-1]
        assert "&lt;script&gt;" in push_text
        assert "<script>" not in push_text

    @pytest.mark.asyncio
    async def test_push_notification_exception_resilience(
        self, mock_bot, message_factory, callback_factory, fsm_context_factory, async_session, sample_admin, sample_user
    ):
        """Push notification error does not roll back financial transaction or crash handler."""
        state = fsm_context_factory(sample_admin.telegram_id)
        init_balance = sample_user.accounts[0].balance

        cb_credit = callback_factory(f"admin:user:credit:{sample_user.id}", user_id=sample_admin.telegram_id)
        await handle_admin_balance_initiate(cb_credit, session=async_session, state=state)

        msg_amt = message_factory("250.00", user_id=sample_admin.telegram_id)
        await process_balance_delta(msg_amt, session=async_session, state=state)

        msg_reason = message_factory("Тестовое пополнение для проверки ошибки push", user_id=sample_admin.telegram_id)
        await process_balance_reason(msg_reason, session=async_session, state=state)

        # Mock send_message to raise exception when sending to client
        orig_send = mock_bot.send_message
        async def mock_fail_send(*args, **kwargs):
            if kwargs.get("chat_id") == sample_user.telegram_id:
                raise Exception("Network connection lost")
            return await orig_send(*args, **kwargs)

        with patch.object(mock_bot, "send_message", side_effect=mock_fail_send):
            cb_confirm = callback_factory("admin:adjust:confirm", user_id=sample_admin.telegram_id)
            await handle_adjustment_confirm(cb_confirm, session=async_session, state=state, bot=mock_bot)

        # Financial update still committed
        await async_session.refresh(sample_user.accounts[0])
        assert sample_user.accounts[0].balance == init_balance + Decimal("250.00")


# ==============================================================================
# 4. Broadcast Wizard Handlers Tests
# ==============================================================================

class TestAdminBroadcastWizardHandlers:
    """Verify broadcast wizard drafting, validation, preview, and confirmation."""

    @pytest.mark.asyncio
    async def test_broadcast_start_command_and_callback(
        self, mock_bot, message_factory, callback_factory, fsm_context_factory, async_session, sample_admin
    ):
        """Broadcast wizard starts and reports audience size."""
        state = fsm_context_factory(sample_admin.telegram_id)

        # 1. Via Command
        msg_start = message_factory("/broadcast", user_id=sample_admin.telegram_id)
        await handle_start_broadcast(msg_start, session=async_session, state=state)
        assert await state.get_state() == BroadcastStates.waiting_for_message
        sent_texts = get_all_sent_texts(mock_bot)
        assert any("Создание глобальной рассылки" in t for t in sent_texts)

        # 2. Via Callback
        await state.clear()
        cb_start = callback_factory("admin:broadcast:start", user_id=sample_admin.telegram_id)
        await handle_start_broadcast(cb_start, session=async_session, state=state)
        assert await state.get_state() == BroadcastStates.waiting_for_message

    @pytest.mark.asyncio
    async def test_broadcast_cancel_at_message_input(
        self, mock_bot, message_factory, fsm_context_factory, async_session, sample_admin
    ):
        """Sending /cancel during broadcast input aborts wizard cleanly."""
        state = fsm_context_factory(sample_admin.telegram_id)
        await state.set_state(BroadcastStates.waiting_for_message)

        msg_cancel = message_factory("/cancel", user_id=sample_admin.telegram_id)
        await process_broadcast_message(msg_cancel, session=async_session, state=state)

        assert await state.get_state() is None
        sent_texts = get_all_sent_texts(mock_bot)
        assert any("отменено" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_broadcast_empty_message_rejected(
        self, mock_bot, message_factory, fsm_context_factory, async_session, sample_admin
    ):
        """Empty or whitespace broadcast message is rejected."""
        state = fsm_context_factory(sample_admin.telegram_id)
        await state.set_state(BroadcastStates.waiting_for_message)

        msg_empty = message_factory("   ", user_id=sample_admin.telegram_id)
        await process_broadcast_message(msg_empty, session=async_session, state=state)

        assert await state.get_state() == BroadcastStates.waiting_for_message
        sent_texts = get_all_sent_texts(mock_bot)
        assert any("не может быть пустым" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_broadcast_preview_and_cancel_at_confirmation(
        self, mock_bot, message_factory, callback_factory, fsm_context_factory, async_session, sample_admin
    ):
        """Valid broadcast text renders preview, confirmation card, and supports cancel."""
        state = fsm_context_factory(sample_admin.telegram_id)
        await state.set_state(BroadcastStates.waiting_for_message)
        await state.update_data(recipient_count=10)

        msg_text = message_factory("<b>Внимание!</b> Проводятся плановые технические работы.", user_id=sample_admin.telegram_id)
        await process_broadcast_message(msg_text, session=async_session, state=state)

        assert await state.get_state() == BroadcastStates.confirm_broadcast
        sent_texts = get_all_sent_texts(mock_bot)
        assert any("Предпросмотр сообщения" in t for t in sent_texts)
        assert any("Подтверждение массовой рассылки" in t for t in sent_texts)

        # Cancel confirmation
        cb_cancel = callback_factory("admin:broadcast:cancel", user_id=sample_admin.telegram_id)
        await handle_cancel_broadcast(cb_cancel, session=async_session, state=state)
        assert await state.get_state() is None


# ==============================================================================
# 5. Broadcast Dispatch Loop & Service Engine Tests
# ==============================================================================

class TestAdminBroadcastDispatchLoopHandlers:
    """Verify rate-limited broadcast execution, exception handling, and summary."""

    @pytest.mark.asyncio
    async def test_broadcast_dispatch_all_success(
        self, mock_bot, async_session, sample_admin, sample_user, sample_recipient
    ):
        """Broadcast dispatches message to all users in test DB without errors."""
        result = await AdminService.execute_broadcast(
            bot=mock_bot,
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            message_text="<b>Уважаемые клиенты!</b> Новые ставки по накопительным счетам 18%!",
            delay=0.0,
        )

        assert result["total"] >= 3
        assert result["sent"] == result["total"]
        assert result["blocked"] == 0
        assert result["failed"] == 0
        assert result["duration_seconds"] >= 0.0

    @pytest.mark.asyncio
    async def test_broadcast_dispatch_handles_blocked_users_forbidden(
        self, mock_bot, async_session, sample_admin, sample_user, sample_recipient
    ):
        """Broadcast loop catches TelegramForbiddenError for blocked user and continues."""
        orig_send = mock_bot.send_message

        async def mock_forbidden_for_user(*args, **kwargs):
            if kwargs.get("chat_id") == sample_user.telegram_id:
                raise TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")
            return await orig_send(*args, **kwargs)

        with patch.object(mock_bot, "send_message", side_effect=mock_forbidden_for_user):
            result = await AdminService.execute_broadcast(
                bot=mock_bot,
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                message_text="Оповещение",
                delay=0.0,
            )

        assert result["blocked"] == 1
        assert result["failed"] == 1
        assert result["sent"] == result["total"] - 1

    @pytest.mark.asyncio
    async def test_broadcast_dispatch_handles_retry_after_backoff(
        self, mock_bot, async_session, sample_admin, sample_user
    ):
        """Broadcast loop catches TelegramRetryAfter, sleeps backoff, and retries."""
        call_count = 0
        orig_send = mock_bot.send_message

        async def mock_retry_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TelegramRetryAfter(method=None, message="Too Many Requests", retry_after=0.001)
            return await orig_send(*args, **kwargs)

        with patch.object(mock_bot, "send_message", side_effect=mock_retry_once):
            result = await AdminService.execute_broadcast(
                bot=mock_bot,
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                message_text="Сообщение с проверкой RetryAfter",
                delay=0.0,
            )

        assert result["retried"] == 1
        assert result["sent"] >= 1

    @pytest.mark.asyncio
    async def test_broadcast_dispatch_zero_recipients_graceful(
        self, mock_bot, async_session, sample_admin
    ):
        """Broadcast handles empty database or zero recipients without ZeroDivisionError."""
        with patch.object(AdminService, "get_all_recipient_ids", return_value=[]):
            result = await AdminService.execute_broadcast(
                bot=mock_bot,
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                message_text="Пустая рассылка",
                delay=0.0,
            )

        assert result["total"] == 0
        assert result["sent"] == 0
        assert result["failed"] == 0
        assert result["effective_speed"] == 0.0

    @pytest.mark.asyncio
    async def test_broadcast_audit_log_record_persistence(
        self, mock_bot, async_session, sample_admin, sample_user
    ):
        """Broadcast execution persists an audit record in AdminLog."""
        await AdminService.execute_broadcast(
            bot=mock_bot,
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            message_text="Проверка записи аудита рассылки",
            delay=0.0,
        )
        await async_session.commit()

        log_res = await async_session.execute(
            select(AdminLog).where(AdminLog.action == "BROADCAST").order_by(AdminLog.created_at.desc())
        )
        broadcast_log = log_res.scalars().first()
        assert broadcast_log is not None
        assert broadcast_log.admin_telegram_id == sample_admin.telegram_id
        assert "Глобальная рассылка" in broadcast_log.reason
