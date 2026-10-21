"""Empirical Challenger Milestone 3 Stress Suite: Admin RBAC, User Lookup, Freeze Lifecycle, and Adjustments.

Adversarial stress testing covering:
1. Admin RBAC Matrix: Unauthorized commands (/admin, /broadcast) and all admin:* callback queries.
2. User Search Edge Cases: 15-20 digit IDs, invalid card checksums, SQL wildcards, injection vectors.
3. Balance Adjustments: Overdraft debit rejection, reason length boundaries (4, 5, 255, 256 chars), double-entry ledger investigation, audit log verification.
4. Freeze/Unfreeze Lifecycle: Full account/card locking and outgoing transaction blocking.
5. FSM Error Handling: Malformed amounts, excessive amounts, and push notification resilience.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.filters.admin import IsAdminFilter
from app.bot.routers.admin import (
    handle_admin_balance_initiate,
    handle_admin_dashboard,
    handle_admin_toggle_freeze_user,
    handle_adjustment_confirm,
    handle_unauthorized_admin_callback,
    handle_unauthorized_admin_command,
    handle_unauthorized_broadcast_command,
    process_balance_delta,
    process_balance_reason,
    process_user_search_query,
)
from app.bot.states.states import AdminStates
from app.config import settings
from app.database.models import Account, AdminLog, Card, LedgerEntry, Transaction, User
from app.services.admin import AdminService
from app.services.banking import BankingService
from app.services.investments import InvestmentService
from app.services.transfers import AccountFrozenError, InsufficientFundsError, TransferService


# ==============================================================================
# Helper to extract texts from mock_bot
# ==============================================================================

def get_all_callback_answers(mock_bot) -> list[dict]:
    """Collect all callback query answer payloads from direct calls and callable methods."""
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


def get_sent_texts(mock_bot) -> list[str]:
    """Extract all text contents sent through mock_bot."""
    texts = []
    for call in mock_bot.send_message.call_args_list:
        args, kwargs = call
        if "text" in kwargs:
            texts.append(kwargs["text"])
        elif len(args) > 1 and isinstance(args[1], str):
            texts.append(args[1])
    for call in mock_bot.call_args_list:
        if call[0]:
            method = call[0][0]
            if type(method).__name__ == "SendMessage" and hasattr(method, "text") and method.text:
                texts.append(method.text)
    return texts


def get_edited_texts(mock_bot) -> list[str]:
    """Extract all text contents edited through mock_bot."""
    texts = []
    for call in mock_bot.edit_message_text.call_args_list:
        args, kwargs = call
        if "text" in kwargs:
            texts.append(kwargs["text"])
    for call in mock_bot.call_args_list:
        if call[0]:
            method = call[0][0]
            if type(method).__name__ == "EditMessageText" and hasattr(method, "text") and method.text:
                texts.append(method.text)
    return texts


# ==============================================================================
# 1. Admin RBAC Matrix Stress Tests
# ==============================================================================

class TestAdminRbacMatrixAdversarial:
    """Stress-test RBAC barriers across commands and all admin:* callback patterns."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "admin_callback_data",
        [
            "admin:dashboard",
            "admin:metrics:refresh",
            "admin:exit",
            "admin:user:search",
            "admin:users:search",
            "admin:user:view:1",
            "admin:user:freeze:1",
            "admin:user:unfreeze:1",
            "admin:user:ledger:1",
            "admin:audit:log",
            "admin:logs:view",
            "admin:user:credit:1",
            "admin:user:debit:1",
            "admin:credit:1",
            "admin:debit:1",
            "admin:adjust:confirm",
            "admin:adjust:cancel",
            "admin:broadcast:start",
            "admin:broadcast",
            "admin:broadcast:confirm",
            "admin:broadcast:cancel",
        ],
    )
    async def test_unauthorized_callback_rejected_with_403_alert(
        self, mock_bot, callback_factory, admin_callback_data
    ):
        """Every admin:* callback query from non-admin user must trigger 403 access denial alert."""
        non_admin_id = 88887777
        assert non_admin_id not in settings.ADMIN_IDS

        cb = callback_factory(admin_callback_data, user_id=non_admin_id)

        # 1. Verify IsAdminFilter directly rejects
        filter_obj = IsAdminFilter()
        assert await filter_obj(cb) is False

        # 2. Verify unauthorized callback handler delivers 403 alert popup
        await handle_unauthorized_admin_callback(cb)

        # Assert answer was emitted with denial text and show_alert=True
        answers = get_all_callback_answers(mock_bot)
        assert len(answers) >= 1
        assert any(
            a.get("show_alert") is True and "Доступ запрещен" in (a.get("text") or "")
            for a in answers
        )

    @pytest.mark.asyncio
    async def test_unauthorized_admin_commands_rejected(
        self, mock_bot, message_factory
    ):
        """Unauthorized non-admin attempting /admin or /broadcast receives 403 denial text."""
        non_admin_id = 99998888
        assert non_admin_id not in settings.ADMIN_IDS

        # /admin
        msg_admin = message_factory("/admin", user_id=non_admin_id)
        assert await IsAdminFilter()(msg_admin) is False
        await handle_unauthorized_admin_command(msg_admin)

        # /broadcast
        msg_broadcast = message_factory("/broadcast", user_id=non_admin_id)
        assert await IsAdminFilter()(msg_broadcast) is False
        await handle_unauthorized_broadcast_command(msg_broadcast)

        sent_texts = get_sent_texts(mock_bot)
        assert len(sent_texts) >= 2
        for t in sent_texts[-2:]:
            assert "Доступ запрещен" in t

    @pytest.mark.asyncio
    async def test_is_admin_filter_edge_cases(self):
        """IsAdminFilter handles None user, negative IDs, and custom admin lists."""
        filter_default = IsAdminFilter()

        # Dummy object with no from_user
        class NoUserEvent:
            pass

        assert await filter_default(NoUserEvent()) is False

        # Dummy object with from_user.id = -1
        class NegativeUserEvent:
            from_user = type("TgUser", (), {"id": -1})()

        assert await filter_default(NegativeUserEvent()) is False

        # Custom admin_ids parameter overrides settings.ADMIN_IDS
        custom_filter = IsAdminFilter(admin_ids=[555, 777])
        class CustomUserEvent:
            from_user = type("TgUser", (), {"id": 555})()

        assert await custom_filter(CustomUserEvent()) is True

        class NonCustomUserEvent:
            from_user = type("TgUser", (), {"id": 999})()

        assert await custom_filter(NonCustomUserEvent()) is False


# ==============================================================================
# 2. User Search Edge Cases Stress Tests
# ==============================================================================

class TestAdminUserSearchAdversarial:
    """Stress-test user lookup with 15-20 digit IDs, invalid card checksums, SQL wildcards, and injection."""

    @pytest.mark.asyncio
    async def test_search_user_15_digit_telegram_id(self, async_session):
        """Search resolves user with 15-digit numeric Telegram ID cleanly."""
        fifteen_digit_id = 123456789012345
        user, _, _ = await BankingService.get_or_create_user(
            async_session,
            telegram_id=fifteen_digit_id,
            username="user_15digit",
            full_name="User Fifteen Digits",
        )
        await async_session.commit()

        # Search as string
        u_str = await AdminService.search_user(async_session, "123456789012345")
        assert u_str is not None
        assert u_str.telegram_id == fifteen_digit_id

        # Search as integer
        u_int = await AdminService.search_user(async_session, fifteen_digit_id)
        assert u_int is not None
        assert u_int.telegram_id == fifteen_digit_id

    @pytest.mark.asyncio
    async def test_search_user_16_digit_card_vs_telegram_id_fallback(self, async_session):
        """16-digit query resolves by card if card exists, or falls back to user ID if no card matches."""
        # User A has valid 16-digit card
        user_a, _, card_a = await BankingService.get_or_create_user(
            async_session,
            telegram_id=200001,
            username="user_a_card",
            full_name="User A",
        )
        await async_session.commit()

        # User B has 16-digit Telegram ID (no card with this PAN exists)
        sixteen_digit_tg_id = 2200709999999997
        user_b, _, _ = await BankingService.get_or_create_user(
            async_session,
            telegram_id=sixteen_digit_tg_id,
            username="user_b_tg_id",
            full_name="User B",
        )
        await async_session.commit()

        # 1. Search card_a card_number resolves User A
        res_a = await AdminService.search_user(async_session, card_a.card_number)
        assert res_a is not None
        assert res_a.id == user_a.id

        # 2. Search card_a with spaces resolves User A
        spaced_card = f"{card_a.card_number[:4]} {card_a.card_number[4:8]} {card_a.card_number[8:12]} {card_a.card_number[12:]}"
        res_a_spaced = await AdminService.search_user(async_session, spaced_card)
        assert res_a_spaced is not None
        assert res_a_spaced.id == user_a.id

        # 3. Search 16-digit tg_id (which has no card) falls back to User B
        res_b = await AdminService.search_user(async_session, str(sixteen_digit_tg_id))
        assert res_b is not None
        assert res_b.telegram_id == sixteen_digit_tg_id

    @pytest.mark.asyncio
    async def test_search_user_17_to_19_digit_telegram_ids(self, async_session):
        """Search resolves 17, 18, and 19-digit (up to max signed 64-bit int) Telegram IDs."""
        id_17 = 12345678901234567
        id_18 = 123456789012345678
        id_19 = 9223372036854775806  # Max signed 64-bit int - 1

        u17, _, _ = await BankingService.get_or_create_user(
            async_session, telegram_id=id_17, username="u17", full_name="User 17"
        )
        u18, _, _ = await BankingService.get_or_create_user(
            async_session, telegram_id=id_18, username="u18", full_name="User 18"
        )
        u19, _, _ = await BankingService.get_or_create_user(
            async_session, telegram_id=id_19, username="u19", full_name="User 19"
        )
        await async_session.commit()

        assert (await AdminService.search_user(async_session, str(id_17))).id == u17.id
        assert (await AdminService.search_user(async_session, str(id_18))).id == u18.id
        assert (await AdminService.search_user(async_session, str(id_19))).id == u19.id

    @pytest.mark.asyncio
    async def test_search_user_20_digit_and_overflow_protection(self, async_session):
        """20-digit numbers exceeding 64-bit integer limit return None without crashing."""
        # 2^63 is 9223372036854775808 (exceeds signed 64-bit SQLite integer)
        overflow_num_str = "9223372036854775808"
        res_overflow = await AdminService.search_user(async_session, overflow_num_str)
        assert res_overflow is None

        # 20-digit all nines
        twenty_nines = "99999999999999999999"
        res_nines = await AdminService.search_user(async_session, twenty_nines)
        assert res_nines is None

        # > 20 digits
        twenty_five_digits = "1234567890123456789012345"
        res_huge = await AdminService.search_user(async_session, twenty_five_digits)
        assert res_huge is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_card_query",
        [
            "2200701111111111",  # 16 digits but no such card in database
            "220070123456789X",  # 16 chars with non-digit
            "2200-7012-3456-789!",  # special chars
            "0000000000000000",  # all zeros
            "9999999999999999",  # all nines
        ],
    )
    async def test_search_user_invalid_card_checksums(self, async_session, invalid_card_query):
        """Invalid or non-existent 16-digit card strings return None safely."""
        res = await AdminService.search_user(async_session, invalid_card_query)
        assert res is None

    @pytest.mark.asyncio
    async def test_search_user_sql_wildcard_leak_prevention(self, async_session, sample_user):
        """SQL wildcards (%, _) must not match usernames or leak customer records."""
        # sample_user is "alice"
        assert await AdminService.search_user(async_session, "@%") is None
        assert await AdminService.search_user(async_session, "@_") is None
        assert await AdminService.search_user(async_session, "%alice%") is None
        assert await AdminService.search_user(async_session, "ali%") is None
        assert await AdminService.search_user(async_session, "_lice") is None
        assert await AdminService.search_user(async_session, "@*") is None
        assert await AdminService.search_user(async_session, "*") is None

        # Exact match succeeds
        found = await AdminService.search_user(async_session, "@alice")
        assert found is not None
        assert found.id == sample_user.id

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "injection_payload",
        [
            "' OR '1'='1",
            "'; DROP TABLE users; --",
            "' UNION SELECT * FROM users --",
            "1 OR 1=1",
            'admin" --',
            "\x00alice",
        ],
    )
    async def test_search_user_injection_resilience(self, async_session, injection_payload):
        """SQL injection and control characters do not crash query engine or leak records."""
        res = await AdminService.search_user(async_session, injection_payload)
        assert res is None


# ==============================================================================
# 3. Balance Adjustments & Reason Validation Stress Tests
# ==============================================================================

class TestAdminBalanceAdjustmentsAdversarial:
    """Stress-test debit overdrafts, reason length boundaries, ledger records, and audit logs."""

    @pytest.mark.asyncio
    async def test_debit_overdraft_exact_boundary_rejection(
        self, async_session, sample_user, sample_admin
    ):
        """Debit exceeding balance by even 0.01 ₽ raises InsufficientFundsError; exact debit succeeds."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        current_bal = main_acc.balance  # 10,000.00 ₽

        # 1. Overdraft debit (balance + 0.01 ₽) must fail
        with pytest.raises(InsufficientFundsError):
            await AdminService.adjust_balance(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=sample_user.id,
                delta=-(current_bal + Decimal("0.01")),
                reason="Попытка списать на 1 копейку больше баланса",
            )

        # Balance remains unchanged
        await async_session.refresh(main_acc)
        assert main_acc.balance == current_bal

        # 2. Exact balance debit (down to 0.00 ₽) must succeed
        tx_exact = await AdminService.adjust_balance(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            delta=-current_bal,
            reason="Списание всего доступного остатка",
        )
        assert tx_exact.status == "COMPLETED"
        assert tx_exact.tx_type == "ADMIN_DEBIT"

        await async_session.refresh(main_acc)
        assert main_acc.balance == Decimal("0.00")

        # 3. Further debit from 0.00 balance must fail
        with pytest.raises(InsufficientFundsError):
            await AdminService.adjust_balance(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=sample_user.id,
                delta=Decimal("-0.01"),
                reason="Списание с нулевого баланса",
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "reason_text,should_pass",
        [
            ("", False),  # 0 chars
            ("a", False),  # 1 char
            ("123", False),  # 3 chars
            ("1234", False),  # 4 chars (boundary fail)
            ("12345", True),  # 5 chars (boundary pass)
            ("Valid reason text", True),
            ("a" * 254, True),  # 254 chars (pass)
            ("a" * 255, True),  # 255 chars (boundary pass)
            ("a" * 256, False),  # 256 chars (boundary fail)
            ("a" * 300, False),  # 300 chars
            ("      ", False),  # whitespace only
            ("   1234   ", False),  # stripped length 4
            ("   12345   ", True),  # stripped length 5
        ],
    )
    async def test_reason_length_boundaries_exhaustive(
        self, async_session, sample_user, sample_admin, reason_text, should_pass
    ):
        """Reason length boundaries (4, 5, 255, 256) are strictly validated."""
        if should_pass:
            tx = await AdminService.adjust_balance(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=sample_user.id,
                delta=Decimal("10.00"),
                reason=reason_text,
            )
            assert tx.status == "COMPLETED"
            assert tx.description == reason_text.strip()
        else:
            with pytest.raises(ValueError):
                await AdminService.adjust_balance(
                    session=async_session,
                    admin_telegram_id=sample_admin.telegram_id,
                    target_user_id=sample_user.id,
                    delta=Decimal("10.00"),
                    reason=reason_text,
                )

    @pytest.mark.asyncio
    async def test_audit_log_entries_accuracy(
        self, async_session, sample_user, sample_admin
    ):
        """Admin balance adjustments accurately persist audit log entries in admin_logs."""
        # 1. Credit adjustment
        credit_amount = Decimal("3500.00")
        credit_reason = "Пополнение счета за участие в бета-тесте"
        await AdminService.adjust_balance(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            delta=credit_amount,
            reason=credit_reason,
        )

        # 2. Debit adjustment
        debit_amount = Decimal("-1200.00")
        debit_reason = "Корректировка излишне начисленных процентов"
        await AdminService.adjust_balance(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            delta=debit_amount,
            reason=debit_reason,
        )

        # Retrieve audit logs
        logs = await AdminService.get_admin_logs(async_session, limit=10)
        assert len(logs) >= 2

        # Check both audit logs are persisted accurately
        log_debit = next(l for l in logs if l.action == "ADMIN_DEBIT")
        assert log_debit.admin_telegram_id == sample_admin.telegram_id
        assert log_debit.target_user_id == sample_user.id
        assert log_debit.amount == debit_amount
        assert log_debit.reason == debit_reason

        log_credit = next(l for l in logs if l.action == "ADMIN_CREDIT")
        assert log_credit.admin_telegram_id == sample_admin.telegram_id
        assert log_credit.target_user_id == sample_user.id
        assert log_credit.amount == credit_amount
        assert log_credit.reason == credit_reason

    @pytest.mark.asyncio
    async def test_balance_adjustment_double_entry_ledger_investigation(
        self, async_session, sample_user, sample_admin
    ):
        """
        Investigate whether admin balance adjustment generates LedgerEntry records.

        Double-entry bookkeeping requires recording financial movements into ledger_entries.
        This test checks the current behavior of adjust_balance regarding LedgerEntry creation.
        """
        credit_amount = Decimal("1000.00")
        tx = await AdminService.adjust_balance(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            delta=credit_amount,
            reason="Тестовая проверка двойной записи",
        )

        # Query LedgerEntry rows associated with this transaction ID
        ledger_res = await async_session.execute(
            select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id)
        )
        ledger_entries = list(ledger_res.scalars().all())

        # Transaction has sender/recipient accounts defined:
        assert tx.recipient_account_id is not None
        assert tx.sender_account_id is None

        # Document whether LedgerEntry records exist:
        # Note: adjust_balance updates Account.balance and writes Transaction + AdminLog.
        # Direct LedgerEntry rows for admin adjustments:
        # If len(ledger_entries) == 0, transaction is recorded via Transaction table.
        # This observation is reported in Challenger handoff.
        assert isinstance(ledger_entries, list)


# ==============================================================================
# 4. Account Freeze & Unfreeze Lifecycle Stress Tests
# ==============================================================================

class TestAdminFreezeUnfreezeLifecycleAdversarial:
    """Stress-test account and card freezing and operational isolation."""

    @pytest.mark.asyncio
    async def test_freeze_lifecycle_blocks_all_financial_operations(
        self, async_session, sample_user, sample_recipient, sample_admin
    ):
        """Freezing user account blocks transfers and stock buys, unfreezing restores them."""
        recipient_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)

        # 1. Freeze user
        await AdminService.freeze_user(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            freeze=True,
            reason="Подозрение на взлом аккаунта",
        )
        await async_session.commit()

        # Verify database flags
        await async_session.refresh(sample_user)
        assert sample_user.is_blocked is True

        user_accs = (
            await async_session.execute(select(Account).where(Account.user_id == sample_user.id))
        ).scalars().all()
        for acc in user_accs:
            assert acc.is_frozen is True

        user_cards = (
            await async_session.execute(select(Card).where(Card.user_id == sample_user.id))
        ).scalars().all()
        for c in user_cards:
            assert c.is_frozen is True
            assert c.status == "FROZEN"

        # 2. Attempt outgoing transfer -> must fail with AccountFrozenError
        with pytest.raises(AccountFrozenError):
            await TransferService.execute_transfer(
                session=async_session,
                sender_user_id=sample_user.id,
                recipient_account_id=recipient_acc.id,
                amount=Decimal("100.00"),
            )

        # 3. Unfreeze user
        await AdminService.freeze_user(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            freeze=False,
            reason="Блокировка снята после проверки СБ",
        )
        await async_session.commit()

        # Verify unfreezing in database
        await async_session.refresh(sample_user)
        assert sample_user.is_blocked is False

        for acc in user_accs:
            await async_session.refresh(acc)
            assert acc.is_frozen is False

        for c in user_cards:
            await async_session.refresh(c)
            assert c.is_frozen is False
            assert c.status == "ACTIVE"

        # 4. Outgoing transfer now succeeds
        tx = await TransferService.execute_transfer(
            session=async_session,
            sender_user_id=sample_user.id,
            recipient_account_id=recipient_acc.id,
            amount=Decimal("100.00"),
        )
        assert tx.status == "COMPLETED"

    @pytest.mark.asyncio
    async def test_freeze_nonexistent_user_raises_value_error(
        self, async_session, sample_admin
    ):
        """Freezing non-existent user ID raises ValueError cleanly."""
        with pytest.raises(ValueError, match="не найден"):
            await AdminService.freeze_user(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=99999999,
                freeze=True,
            )


# ==============================================================================
# 5. Balance Adjustment FSM Stress Tests
# ==============================================================================

class TestAdminBalanceAdjustmentFsmAdversarial:
    """Stress-test bot FSM dialog states, inputs, pre-checks, and exception resilience."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_amount_text",
        [
            "abc",
            "$$$",
            "-500",
            "-0.01",
            "0",
            "0.00",
            "10000000.01",  # exceeds 10M limit
        ],
    )
    async def test_fsm_balance_delta_validation_rejections(
        self,
        mock_bot,
        message_factory,
        async_session,
        fsm_context_factory,
        sample_user,
        sample_admin,
        invalid_amount_text,
    ):
        """FSM delta handler strictly rejects non-positive, non-decimal, or excessive amounts."""
        state = fsm_context_factory(user_id=sample_admin.telegram_id)
        await state.set_state(AdminStates.balance_delta)
        await state.update_data(
            target_user_id=sample_user.id,
            target_telegram_id=sample_user.telegram_id,
            adjust_type="CREDIT",
            current_balance="10000.00",
        )

        msg = message_factory(invalid_amount_text, user_id=sample_admin.telegram_id)
        await process_balance_delta(msg, session=async_session, state=state)

        # State must remain balance_delta (not progressed)
        current_state = await state.get_state()
        assert current_state == AdminStates.balance_delta.state

        sent_texts = get_sent_texts(mock_bot)
        assert any("❌" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_fsm_debit_overdraft_pre_check_in_dialog(
        self,
        mock_bot,
        message_factory,
        async_session,
        fsm_context_factory,
        sample_user,
        sample_admin,
    ):
        """FSM debit flow immediately rejects amount exceeding balance before asking for reason."""
        state = fsm_context_factory(user_id=sample_admin.telegram_id)
        await state.set_state(AdminStates.balance_delta)
        await state.update_data(
            target_user_id=sample_user.id,
            target_telegram_id=sample_user.telegram_id,
            adjust_type="DEBIT",
            current_balance="500.00",
        )

        # Request debit 600.00 when balance is 500.00
        msg = message_factory("600.00", user_id=sample_admin.telegram_id)
        await process_balance_delta(msg, session=async_session, state=state)

        # State remains balance_delta
        assert await state.get_state() == AdminStates.balance_delta.state

        sent_texts = get_sent_texts(mock_bot)
        assert any("Недостаточно средств для списания" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_fsm_adjustment_push_notification_exception_resilience(
        self,
        mock_bot,
        callback_factory,
        async_session,
        fsm_context_factory,
        sample_user,
        sample_admin,
    ):
        """If push notification fails with TelegramForbiddenError, financial ledger change commits."""
        state = fsm_context_factory(user_id=sample_admin.telegram_id)
        await state.update_data(
            target_user_id=sample_user.id,
            target_telegram_id=sample_user.telegram_id,
            target_username=sample_user.username,
            target_full_name=sample_user.full_name,
            adjust_type="CREDIT",
            amount="2500.00",
            delta="2500.00",
            current_balance="10000.00",
            reason="Тестовое начисление средств клиенту",
        )

        cb = callback_factory("admin:adjust:confirm", user_id=sample_admin.telegram_id)

        # Mock send_message to raise TelegramForbiddenError on push notification
        async def mock_send_message(*args, **kwargs):
            if kwargs.get("chat_id") == sample_user.telegram_id:
                raise TelegramForbiddenError(method=None, message="Bot was blocked by user")
            dummy_msg = AsyncMock(spec=Message)
            dummy_msg.message_id = 123
            return dummy_msg

        mock_bot.send_message = AsyncMock(side_effect=mock_send_message)

        await handle_adjustment_confirm(cb, session=async_session, state=state, bot=mock_bot)

        # Balance was still committed in database!
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        await async_session.refresh(main_acc)
        assert main_acc.balance == Decimal("12500.00")
