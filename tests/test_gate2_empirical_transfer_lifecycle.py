"""Challenger Empirical Test Suite for Milestone 2 Gate 2:
Router Attachment Lifecycle and Transfer FSM Adversarial Stress-Testing.
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Dispatcher, Router
from aiogram.enums import ChatType
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User as TgUser

from app.bot.bot import create_bot, create_dispatcher, detach_router
from app.bot.routers.card import card_router
from app.bot.routers.cashback import cashback_router
from app.bot.routers.common import common_router
from app.bot.routers.invest import invest_router
from app.bot.routers.oleg import oleg_router
from app.bot.routers.savings import savings_router
from app.bot.routers.transfer import (
    handle_cancel_transfer_callback,
    handle_confirm_transfer,
    handle_start_transfer,
    process_transfer_amount,
    process_transfer_recipient,
    transfer_router,
)
from app.bot.states.states import TransferStates
from app.database.models import Account, Card, User
from app.services.banking import BankingService


# ==============================================================================
# Suite 1: Router Attachment Lifecycle Stress Tests
# ==============================================================================

class TestRouterLifecycleStress:
    """Stress tests for router detachment, re-attachment, and lifecycle resilience."""

    def test_repeated_create_dispatcher_50_cycles(self):
        """Stress test: 50 consecutive create_dispatcher() calls must succeed without collision."""
        dispatchers = []
        for _ in range(50):
            dp = create_dispatcher()
            dispatchers.append(dp)
            assert len(dp.sub_routers) >= 7

        last_dp = dispatchers[-1]
        router_names = {r.name for r in last_dp.sub_routers}
        expected = {
            "common_router",
            "card_router",
            "transfer_router",
            "savings_router",
            "invest_router",
            "cashback_router",
            "oleg_router",
        }
        assert expected.issubset(router_names)

    def test_detach_router_on_unattached_router(self):
        """detach_router on a virgin router with no parent must be a safe no-op."""
        test_r = Router(name="test_orphan")
        assert getattr(test_r, "_parent_router", None) is None
        detach_router(test_r)
        assert getattr(test_r, "_parent_router", None) is None

    def test_detach_router_when_parent_sub_routers_desynced(self):
        """detach_router handles desynchronized sub_routers without raising ValueError."""
        parent = MagicMock()
        parent.sub_routers = []  # router not present in list
        test_r = Router(name="test_desync")
        test_r._parent_router = parent

        detach_router(test_r)
        assert test_r._parent_router is None

    def test_detach_router_when_parent_has_no_sub_routers_attribute(self):
        """detach_router handles malformed parent without sub_routers attribute."""
        test_r = Router(name="test_malformed_parent")
        test_r._parent_router = object()  # plain object, no sub_routers attribute

        detach_router(test_r)
        assert test_r._parent_router is None

    def test_dispatcher_sub_router_isolation(self):
        """Verify each new dispatcher becomes the exclusive parent of the routers."""
        dp1 = create_dispatcher()
        dp2 = create_dispatcher()

        # All routers should now point to dp2 as their _parent_router
        for r in dp2.sub_routers:
            assert r._parent_router is dp2

        # dp1's sub_routers should have had them removed during dp2 initialization
        for r in dp2.sub_routers:
            assert r not in dp1.sub_routers


# ==============================================================================
# Suite 2: Transfer FSM Adversarial Input & Robustness Stress Tests
# ==============================================================================

class TestTransferFsmAdversarialStress:
    """Deep adversarial input fuzzing and edge cases for Transfer FSM."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "malformed_amount",
        [
            "0",
            "0.00",
            "-1",
            "-50.00",
            "-0.01",
            "0.99",               # Below minimum 1.00
            "1000000.01",         # Above maximum 1,000,000.00
            "99999999999999999",  # Overflow
            "abc",
            "пятьсот",
            "",
            "   ",
            "NaN",
            "Infinity",
            "-Infinity",
            "<script>alert(1)</script>",
            "100.00.00",
            "100..00",
            "100,00,00",
        ],
    )
    async def test_transfer_amount_invalid_inputs_rejected(
        self,
        malformed_amount,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """All non-numeric, negative, sub-min, or above-max amounts must be safely rejected."""
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_amount)
        await state.update_data(
            recipient_account_id=999,
            recipient_full_name="Тест Получатель",
            recipient_username="@test_rec",
        )

        msg = message_factory(malformed_amount, user_id=sample_user.telegram_id)
        await process_transfer_amount(msg, session=async_session, user=sample_user, state=state)

        # State must remain in waiting_for_amount, not advance to confirm
        current_state = await state.get_state()
        assert current_state == TransferStates.waiting_for_amount

        # User must receive an error prompt recorded by mock_bot
        sent_texts = [getattr(call[0][0], "text", "") for call in mock_bot.call_args_list if call.args]
        assert any("❌" in t for t in sent_texts)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "valid_input,expected_amount",
        [
            ("1.00", "1.00"),              # Minimum boundary
            ("1", "1.00"),                 # Integer without decimals
            ("1000000.00", "1000000.00"),  # Maximum boundary (if balance permits)
            ("500,50", "500.50"),          # Comma decimal separator
            (" 250.75 ₽ ", "250.75"),      # Russian ruble sign and spaces
            ("1 000.00", "1000.00"),       # Thousand space separator
        ],
    )
    async def test_transfer_amount_valid_flexible_formatting(
        self,
        valid_input,
        expected_amount,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Standard and flexibly formatted valid amounts within balance must advance to confirm state."""
        # Ensure sample_user has enough balance for test inputs up to 1,000,000.00
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        main_acc.balance = Decimal("2000000.00")
        await async_session.commit()

        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_amount)
        await state.update_data(
            recipient_account_id=999,
            recipient_full_name="Боб Получатель",
            recipient_username="@bob_rec",
        )

        msg = message_factory(valid_input, user_id=sample_user.telegram_id)
        await process_transfer_amount(msg, session=async_session, user=sample_user, state=state)

        assert await state.get_state() == TransferStates.confirm
        data = await state.get_data()
        assert data.get("amount") == expected_amount

    @pytest.mark.asyncio
    async def test_transfer_amount_missing_all_fsm_keys(
        self,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """process_transfer_amount with completely empty FSM state data must not raise KeyError."""
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_amount)
        # Empty state data: no recipient_full_name, no recipient_username, no recipient_account_id

        msg = message_factory("150.00", user_id=sample_user.telegram_id)
        await process_transfer_amount(msg, session=async_session, user=sample_user, state=state)

        # Should transition safely to confirm state with fallback labels
        assert await state.get_state() == TransferStates.confirm
        sent_texts = [getattr(call[0][0], "text", "") for call in mock_bot.call_args_list if call.args]
        combined = " ".join(sent_texts)
        assert "Клиент Т-Банка" in combined
        assert "нет username" in combined

    @pytest.mark.asyncio
    async def test_transfer_amount_corrupt_non_string_fsm_keys(
        self,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """process_transfer_amount with non-string state keys must convert safely to string without crash."""
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_amount)
        await state.update_data(
            recipient_full_name=123456,  # integer instead of str
            recipient_username=None,     # None instead of str
        )

        msg = message_factory("200.00", user_id=sample_user.telegram_id)
        await process_transfer_amount(msg, session=async_session, user=sample_user, state=state)

        assert await state.get_state() == TransferStates.confirm
        sent_texts = [getattr(call[0][0], "text", "") for call in mock_bot.call_args_list if call.args]
        combined = " ".join(sent_texts)
        assert "123456" in combined

    @pytest.mark.asyncio
    async def test_transfer_amount_html_injection_fuzzing(
        self,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Fuzzing recipient names with HTML tags (<, >, &, \", ') must be strictly escaped in confirm text."""
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_amount)
        await state.update_data(
            recipient_full_name="<script>alert('pwned')</script> & Co",
            recipient_username="@<b>hacker</b>",
        )

        msg = message_factory("100.00", user_id=sample_user.telegram_id)
        await process_transfer_amount(msg, session=async_session, user=sample_user, state=state)

        assert await state.get_state() == TransferStates.confirm
        sent_texts = [getattr(call[0][0], "text", "") for call in mock_bot.call_args_list if call.args]
        confirm_text = " ".join(sent_texts)
        # Raw '<script>' or '<b>' must NOT be present
        assert "<script>" not in confirm_text
        assert "<b>hacker</b>" not in confirm_text
        # Escaped versions MUST be present
        assert "&lt;script&gt;" in confirm_text
        assert "&amp; Co" in confirm_text
        assert "@&lt;b&gt;hacker&lt;/b&gt;" in confirm_text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "empty_data_variant",
        [
            {},                                     # Completely empty
            {"amount": "100.00"},                   # Missing recipient_account_id
            {"recipient_account_id": 999},          # Missing amount
            {"recipient_account_id": 999, "amount": None},  # None amount
        ],
    )
    async def test_handle_confirm_transfer_missing_keys_graceful_abort(
        self,
        empty_data_variant,
        mock_bot,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """handle_confirm_transfer with missing essential data aborts gracefully with alert and clears state."""
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.confirm)
        if empty_data_variant:
            await state.update_data(**empty_data_variant)

        cb = callback_factory("trf:confirm", user_id=sample_user.telegram_id)
        await handle_confirm_transfer(cb, session=async_session, user=sample_user, state=state)

        # Check AnswerCallbackQuery was emitted with alert
        alerts = [
            getattr(call[0][0], "text", "")
            for call in mock_bot.call_args_list
            if type(call[0][0]).__name__ == "AnswerCallbackQuery"
        ]
        assert any("Сессия перевода устарела" in a for a in alerts)
        assert await state.get_state() is None

    @pytest.mark.asyncio
    async def test_handle_confirm_transfer_bot_push_notification_exception_resilience(
        self,
        mock_bot,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
        sample_recipient,
    ):
        """If query.bot.send_message raises an exception during recipient push, transaction must NOT fail."""
        rec_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.confirm)
        await state.update_data(
            recipient_account_id=rec_acc.id,
            recipient_user_id=sample_recipient.id,
            recipient_telegram_id=sample_recipient.telegram_id,
            recipient_full_name=sample_recipient.full_name,
            recipient_username=f"@{sample_recipient.username}",
            amount="250.00",
        )

        # Force mock_bot.send_message to raise network error
        mock_bot.send_message.side_effect = Exception("Telegram API timeout / network disconnected")

        cb = callback_factory("trf:confirm", user_id=sample_user.telegram_id)
        await handle_confirm_transfer(cb, session=async_session, user=sample_user, state=state)

        # State must be cleared
        assert await state.get_state() is None

        # Verify database transaction actually committed despite push notification failure
        await async_session.refresh(rec_acc)
        assert rec_acc.balance == Decimal("1250.00")

    @pytest.mark.asyncio
    async def test_handle_confirm_transfer_message_edit_exception_fallback(
        self,
        mock_bot,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
        sample_recipient,
    ):
        """If query.message.edit_text fails, fallback message.answer must execute and flow completes."""
        rec_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.confirm)
        await state.update_data(
            recipient_account_id=rec_acc.id,
            recipient_user_id=sample_recipient.id,
            recipient_telegram_id=sample_recipient.telegram_id,
            recipient_full_name=sample_recipient.full_name,
            recipient_username=f"@{sample_recipient.username}",
            amount="100.00",
        )

        cb = callback_factory("trf:confirm", user_id=sample_user.telegram_id)

        # Force mock_bot to raise when EditMessageText is called
        orig_return = mock_bot.return_value

        def bot_effect(method, *args, **kwargs):
            if type(method).__name__ == "EditMessageText":
                raise Exception("TelegramBadRequest: message to edit not found")
            return orig_return

        mock_bot.side_effect = bot_effect

        await handle_confirm_transfer(cb, session=async_session, user=sample_user, state=state)

        # Verification: SendMessage was dispatched as fallback
        sent_methods = [type(call[0][0]).__name__ for call in mock_bot.call_args_list if call.args]
        assert "SendMessage" in sent_methods
        assert await state.get_state() is None

    @pytest.mark.asyncio
    async def test_handle_confirm_transfer_concurrency_overdraft_at_confirmation(
        self,
        mock_bot,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
        sample_recipient,
    ):
        """If balance drops to zero between entering amount and tapping confirm, transfer fails cleanly."""
        rec_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)

        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.confirm)
        await state.update_data(
            recipient_account_id=rec_acc.id,
            recipient_user_id=sample_recipient.id,
            recipient_telegram_id=sample_recipient.telegram_id,
            recipient_full_name=sample_recipient.full_name,
            recipient_username=f"@{sample_recipient.username}",
            amount="500.00",
        )

        # Concurrently drain sender balance before confirmation
        main_acc.balance = Decimal("0.00")
        await async_session.commit()

        cb = callback_factory("trf:confirm", user_id=sample_user.telegram_id)
        await handle_confirm_transfer(cb, session=async_session, user=sample_user, state=state)

        # Must report insufficient funds, clear state, and not modify balances
        alerts = [
            getattr(call[0][0], "text", "")
            for call in mock_bot.call_args_list
            if type(call[0][0]).__name__ == "AnswerCallbackQuery"
        ]
        assert any("Недостаточно средств" in a for a in alerts)
        assert await state.get_state() is None
        await async_session.refresh(rec_acc)
        assert rec_acc.balance == Decimal("1000.00")

    @pytest.mark.asyncio
    async def test_handle_confirm_transfer_card_frozen_mid_flow(
        self,
        mock_bot,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
        sample_recipient,
    ):
        """If card is frozen between entering amount and confirmation, transfer fails cleanly."""
        rec_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)

        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.confirm)
        await state.update_data(
            recipient_account_id=rec_acc.id,
            recipient_user_id=sample_recipient.id,
            recipient_telegram_id=sample_recipient.telegram_id,
            recipient_full_name=sample_recipient.full_name,
            recipient_username=f"@{sample_recipient.username}",
            amount="500.00",
        )

        # Freeze sender account
        main_acc.is_frozen = True
        await async_session.commit()

        cb = callback_factory("trf:confirm", user_id=sample_user.telegram_id)
        await handle_confirm_transfer(cb, session=async_session, user=sample_user, state=state)

        alerts = [
            getattr(call[0][0], "text", "")
            for call in mock_bot.call_args_list
            if type(call[0][0]).__name__ == "AnswerCallbackQuery"
        ]
        assert any("Карта заморожена" in a for a in alerts)
        assert await state.get_state() is None
        await async_session.refresh(rec_acc)
        assert rec_acc.balance == Decimal("1000.00")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "adversarial_recipient_query",
        [
            "",
            "   ",
            "A" * 5000,                                 # Extreme string length
            "SELECT * FROM users WHERE 1=1;",           # SQL injection attempt
            "<img src=x onerror=alert('hack')>",        # HTML / XSS attempt
            "🤖👾💀👻👽",                                 # Pure emojis
            "@non_existent_user_99999_xyz",             # Non-existent username
            "+79990000000",                             # Non-existent phone
            "2200700000000000",                         # Non-existent card
            "999999999",                                # Non-existent telegram ID
        ],
    )
    async def test_process_transfer_recipient_adversarial_queries(
        self,
        adversarial_recipient_query,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Malformed or non-existent recipient queries must be rejected without state transition."""
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_recipient)

        msg = message_factory(adversarial_recipient_query, user_id=sample_user.telegram_id)
        await process_transfer_recipient(msg, session=async_session, user=sample_user, state=state)

        # Must remain in waiting_for_recipient
        assert await state.get_state() == TransferStates.waiting_for_recipient
        sent_texts = [getattr(call[0][0], "text", "") for call in mock_bot.call_args_list if call.args]
        assert any("❌ Получатель не найден" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_cancel_transfer_callback_clears_state_and_edits_message(
        self,
        mock_bot,
        callback_factory,
        fsm_context_factory,
        sample_user,
    ):
        """handle_cancel_transfer_callback cancels flow, clears FSM state, and edits message."""
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.confirm)
        await state.update_data(amount="500.00", recipient_account_id=123)

        cb = callback_factory("trf:cancel", user_id=sample_user.telegram_id)
        await handle_cancel_transfer_callback(cb, state=state)

        assert await state.get_state() is None
        edited_texts = [
            getattr(call[0][0], "text", "")
            for call in mock_bot.call_args_list
            if type(call[0][0]).__name__ == "EditMessageText"
        ]
        assert any("❌ Перевод отменен" in t for t in edited_texts)
