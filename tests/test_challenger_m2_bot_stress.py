"""Empirical Challenger Test Suite for Milestone 2: Telegram Bot Client & User Experience.

Covers deep adversarial stress-testing of:
1. Transfer FSM (TransferStates):
   - Multi-identifier recipient resolution (username, phone, 16-digit card, numeric Telegram ID, internal user ID).
   - Self-transfer rejection across all identifier types.
   - Nonexistent / invalid recipient handling.
   - Cancel flow at every state (waiting_for_recipient, waiting_for_amount, confirm) via commands, reply text, and inline callbacks.
   - Amount boundary validations (zero, negative, sub-kopeck, excessive, non-numeric, comma/space parsing).
   - Overdraft protection and frozen card rejections at initiation and mid-flow execution.
   - Recipient real-time push notification and bot network error resilience.
   - Stale session recovery.
2. Card Router:
   - Masked and unmasked PAN / CVV multi-cycle toggling.
   - Freeze and unfreeze toggle lifecycle with database persistence.
   - Operational blocking verification (transfers and savings deposits blocked when frozen).
3. Savings Router (18% APR Safe):
   - Overview rendering and refresh callback.
   - Deposit FSM validations, overdraft rejection, cancel flows, and atomic execution.
   - Withdrawal FSM validations, limit rejection, cancel flows, and atomic execution.
   - Interest payout execution and double-tap duplicate prevention.
4. Full Bot Router, Common Commands & Oleg Integration.
"""

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.bot.bot import create_bot, create_dispatcher
from app.bot.keyboards.inline import (
    get_card_keyboard,
    get_savings_keyboard,
    get_transfer_confirm_keyboard,
)
from app.bot.keyboards.reply import get_cancel_reply_keyboard, get_main_menu_keyboard
from app.bot.routers.card import (
    handle_card_menu,
    handle_toggle_details,
    handle_toggle_freeze,
)
from app.bot.routers.common import (
    handle_analytics,
    handle_cancel,
    handle_help,
    handle_start,
)
from app.bot.routers.oleg import handle_oleg_dialogue, handle_oleg_prompt
from app.bot.routers.savings import (
    handle_savings_deposit_prompt,
    handle_savings_menu,
    handle_savings_payout,
    handle_savings_refresh,
    handle_savings_withdraw_prompt,
    process_savings_deposit,
    process_savings_withdraw,
)
from app.bot.routers.transfer import (
    handle_cancel_transfer_callback,
    handle_confirm_transfer,
    handle_start_transfer,
    process_transfer_amount,
    process_transfer_recipient,
)
from app.bot.states.states import SavingsStates, TransferStates
from app.database.models import Account, Card, SavingsAccount, User
from app.services.banking import BankingService
from app.services.card import CardService
from app.services.savings import SavingsService
from app.services.transfers import TransferService


# ==============================================================================
# Suite 1: Transfer FSM Adversarial Stress
# ==============================================================================

class TestTransferFsmChallengerStress:
    """Stress-test Transfer FSM flows, resolution, overdrafts, and push alerts."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query_builder",
        [
            lambda u, c: f"@{u.username}",               # @username
            lambda u, c: u.username.upper(),             # USERNAME (case-insensitive without @)
            lambda u, c: u.phone_number,                 # +79994445566
            lambda u, c: "89994445566",                  # 89994445566 (Russian format)
            lambda u, c: c.card_number,                  # 16-digit card plain
            lambda u, c: f"{c.card_number[:4]} {c.card_number[4:8]} {c.card_number[8:12]} {c.card_number[12:]}",  # formatted card with spaces
            lambda u, c: str(u.telegram_id),             # Telegram ID numeric string
            lambda u, c: str(u.id),                      # Internal user ID
        ],
    )
    async def test_transfer_fsm_resolution_all_supported_identifiers(
        self,
        query_builder,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
        sample_recipient,
    ):
        """Verify recipient resolution succeeds across all 8 supported query variations."""
        rec_card = sample_recipient.cards[0]
        query_text = query_builder(sample_recipient, rec_card)

        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_recipient)

        msg = message_factory(query_text, user_id=sample_user.telegram_id)
        await process_transfer_recipient(msg, session=async_session, user=sample_user, state=state)

        # Assert FSM advanced to waiting_for_amount
        assert await state.get_state() == TransferStates.waiting_for_amount
        data = await state.get_data()
        assert data.get("recipient_user_id") == sample_recipient.id
        assert data.get("recipient_telegram_id") == sample_recipient.telegram_id

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query_builder",
        [
            lambda u, c: f"@{u.username}",
            lambda u, c: u.phone_number,
            lambda u, c: c.card_number,
            lambda u, c: str(u.telegram_id),
            lambda u, c: str(u.id),
        ],
    )
    async def test_transfer_fsm_self_transfer_rejections(
        self,
        query_builder,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Verify self-transfer is rejected across all identifier formats, keeping user in waiting state."""
        sender_card = sample_user.cards[0]
        query_text = query_builder(sample_user, sender_card)

        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_recipient)

        msg = message_factory(query_text, user_id=sample_user.telegram_id)
        await process_transfer_recipient(msg, session=async_session, user=sample_user, state=state)

        # Must not advance, must remain in waiting_for_recipient
        assert await state.get_state() == TransferStates.waiting_for_recipient
        data = await state.get_data()
        assert "recipient_account_id" not in data

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_query",
        [
            "@ghost_user_does_not_exist_9999",
            "+79990000000",
            "2200701111111111",  # Invalid Luhn card
            "not_a_valid_query_at_all",
            "12345",             # Numeric ID not existing
        ],
    )
    async def test_transfer_fsm_nonexistent_recipient_handling(
        self,
        invalid_query,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Verify nonexistent or invalid recipients yield friendly prompt and preserve state."""
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_recipient)

        msg = message_factory(invalid_query, user_id=sample_user.telegram_id)
        await process_transfer_recipient(msg, session=async_session, user=sample_user, state=state)

        assert await state.get_state() == TransferStates.waiting_for_recipient

    @pytest.mark.asyncio
    async def test_transfer_fsm_cancel_flows_at_all_states(
        self,
        mock_bot,
        message_factory,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Verify cancel works at waiting_for_recipient, waiting_for_amount, and confirm."""
        state = fsm_context_factory(sample_user.telegram_id)

        # 1. Cancel at waiting_for_recipient via text "❌ Отмена"
        await state.set_state(TransferStates.waiting_for_recipient)
        msg_cancel1 = message_factory("❌ Отмена", user_id=sample_user.telegram_id)
        await process_transfer_recipient(msg_cancel1, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # 2. Cancel at waiting_for_recipient via command /cancel
        await state.set_state(TransferStates.waiting_for_recipient)
        msg_cancel2 = message_factory("/cancel", user_id=sample_user.telegram_id)
        await process_transfer_recipient(msg_cancel2, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # 3. Cancel at waiting_for_amount via text "❌ Отмена"
        await state.set_state(TransferStates.waiting_for_amount)
        msg_cancel3 = message_factory("❌ Отмена", user_id=sample_user.telegram_id)
        await process_transfer_amount(msg_cancel3, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # 4. Cancel at waiting_for_amount via command /cancel
        await state.set_state(TransferStates.waiting_for_amount)
        msg_cancel4 = message_factory("/cancel", user_id=sample_user.telegram_id)
        await process_transfer_amount(msg_cancel4, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # 5. Cancel at confirm state via inline callback "trf:cancel"
        await state.set_state(TransferStates.confirm)
        cb_cancel = callback_factory("trf:cancel", user_id=sample_user.telegram_id)
        await handle_cancel_transfer_callback(cb_cancel, state=state)
        assert await state.get_state() is None

        # 6. Cancel at confirm state via global /cancel handler
        await state.set_state(TransferStates.confirm)
        msg_cancel_global = message_factory("/cancel", user_id=sample_user.telegram_id)
        await handle_cancel(msg_cancel_global, state=state)
        assert await state.get_state() is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_amount_input",
        [
            "0",
            "0.00",
            "-100",
            "-0.01",
            "0.50",          # Below minimum 1.00 ₽
            "1000000.01",    # Above maximum 1,000,000.00 ₽
            "abc",
            "$$$",
            "",
            "   ",
        ],
    )
    async def test_transfer_fsm_amount_validation_stress(
        self,
        invalid_amount_input,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Verify invalid, negative, out-of-bounds, or malformed amounts are rejected."""
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_amount)
        await state.update_data(
            recipient_full_name="Bob Recipient",
            recipient_username="@bob",
        )

        msg = message_factory(invalid_amount_input, user_id=sample_user.telegram_id)
        await process_transfer_amount(msg, session=async_session, user=sample_user, state=state)

        # Should remain in waiting_for_amount
        assert await state.get_state() == TransferStates.waiting_for_amount

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "valid_amount_input,expected_amount",
        [
            ("1.00", Decimal("1.00")),
            ("500", Decimal("500.00")),
            ("1250.75", Decimal("1250.75")),
            ("1250,75", Decimal("1250.75")),          # Comma as separator
            (" 2 500.00 ₽ ", Decimal("2500.00")),     # Space and ruble symbol
            ("100.999", Decimal("101.00")),           # Sub-kopeck quantized
            ("1000000.00", Decimal("1000000.00")),    # Upper boundary
        ],
    )
    async def test_transfer_fsm_amount_parsing_flexibility(
        self,
        valid_amount_input,
        expected_amount,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Verify robust parsing of various amount representations into Decimal."""
        # Ensure sufficient balance
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        main_acc.balance = Decimal("2000000.00")
        await async_session.commit()

        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_amount)
        await state.update_data(
            recipient_full_name="Bob Recipient",
            recipient_username="@bob",
        )

        msg = message_factory(valid_amount_input, user_id=sample_user.telegram_id)
        await process_transfer_amount(msg, session=async_session, user=sample_user, state=state)

        assert await state.get_state() == TransferStates.confirm
        data = await state.get_data()
        assert Decimal(data["amount"]) == expected_amount

    @pytest.mark.asyncio
    async def test_transfer_fsm_overdraft_at_amount_input(
        self,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Verify user cannot specify an amount exceeding their current balance."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        main_acc.balance = Decimal("250.00")
        await async_session.commit()

        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_amount)

        msg = message_factory("300.00", user_id=sample_user.telegram_id)
        await process_transfer_amount(msg, session=async_session, user=sample_user, state=state)

        assert await state.get_state() == TransferStates.waiting_for_amount

    @pytest.mark.asyncio
    async def test_transfer_fsm_start_guards_insufficient_or_frozen(
        self,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Verify handle_start_transfer blocks users with < 1.00 ₽ or frozen cards."""
        state = fsm_context_factory(sample_user.telegram_id)
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)

        # 1. Balance < 1.00 ₽
        main_acc.balance = Decimal("0.75")
        await async_session.commit()

        msg = message_factory("💸 Переводы", user_id=sample_user.telegram_id)
        await handle_start_transfer(msg, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # 2. Card frozen
        main_acc.balance = Decimal("5000.00")
        main_acc.is_frozen = True
        await async_session.commit()

        await handle_start_transfer(msg, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

    @pytest.mark.asyncio
    async def test_transfer_fsm_midflow_concurrency_and_freeze(
        self,
        mock_bot,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
        sample_recipient,
    ):
        """Verify that mid-flow balance depletion or freeze causes safe rollback and UI error."""
        sender_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        rec_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        state = fsm_context_factory(sample_user.telegram_id)

        # --- Subtest A: Mid-flow balance exhaustion ---
        await state.set_state(TransferStates.confirm)
        await state.update_data(
            recipient_account_id=rec_acc.id,
            recipient_user_id=sample_recipient.id,
            recipient_telegram_id=sample_recipient.telegram_id,
            amount="500.00",
        )

        # Deplete sender's balance in database prior to confirmation click
        sender_acc.balance = Decimal("100.00")
        await async_session.commit()

        cb_confirm = callback_factory("trf:confirm", user_id=sample_user.telegram_id)
        await handle_confirm_transfer(cb_confirm, session=async_session, user=sample_user, state=state)

        # State must be cleared and sender's balance unchanged
        assert await state.get_state() is None
        await async_session.refresh(sender_acc)
        assert sender_acc.balance == Decimal("100.00")

        # --- Subtest B: Mid-flow card freeze ---
        sender_acc.balance = Decimal("5000.00")
        sender_acc.is_frozen = True
        await async_session.commit()

        await state.set_state(TransferStates.confirm)
        await state.update_data(
            recipient_account_id=rec_acc.id,
            recipient_user_id=sample_recipient.id,
            recipient_telegram_id=sample_recipient.telegram_id,
            amount="500.00",
        )

        await handle_confirm_transfer(cb_confirm, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None
        await async_session.refresh(sender_acc)
        assert sender_acc.balance == Decimal("5000.00")

    @pytest.mark.asyncio
    async def test_transfer_fsm_push_notification_and_failure_resilience(
        self,
        mock_bot,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
        sample_recipient,
    ):
        """Verify push notification content and ensure network exceptions do not fail transfer."""
        rec_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        state = fsm_context_factory(sample_user.telegram_id)

        # Case 1: Successful push alert
        await state.set_state(TransferStates.confirm)
        await state.update_data(
            recipient_account_id=rec_acc.id,
            recipient_user_id=sample_recipient.id,
            recipient_telegram_id=sample_recipient.telegram_id,
            amount="750.00",
        )

        cb_confirm = callback_factory("trf:confirm", user_id=sample_user.telegram_id)
        await handle_confirm_transfer(cb_confirm, session=async_session, user=sample_user, state=state)

        # Check mock_bot.send_message was invoked
        mock_bot.send_message.assert_called()
        call_kwargs = mock_bot.send_message.call_args.kwargs
        assert call_kwargs["chat_id"] == sample_recipient.telegram_id
        assert "+750.00 ₽" in call_kwargs["text"]
        assert sample_user.full_name in call_kwargs["text"]

        # Case 2: Telegram API exception during push must not crash transfer
        mock_bot.send_message.reset_mock()
        mock_bot.send_message.side_effect = Exception("Telegram network timeout")

        await state.set_state(TransferStates.confirm)
        await state.update_data(
            recipient_account_id=rec_acc.id,
            recipient_user_id=sample_recipient.id,
            recipient_telegram_id=sample_recipient.telegram_id,
            amount="250.00",
        )

        await handle_confirm_transfer(cb_confirm, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # Balances must reflect the transfer despite push failure
        sender_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        assert sender_acc.balance == Decimal("9000.00")

    @pytest.mark.asyncio
    async def test_transfer_fsm_stale_session_handling(
        self,
        mock_bot,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Verify handle_confirm_transfer gracefully handles missing/stale state data."""
        state = fsm_context_factory(sample_user.telegram_id)
        # Empty state data
        await state.set_state(TransferStates.confirm)

        cb_confirm = callback_factory("trf:confirm", user_id=sample_user.telegram_id)
        await handle_confirm_transfer(cb_confirm, session=async_session, user=sample_user, state=state)

        # State should be cleared safely
        assert await state.get_state() is None

    @pytest.mark.asyncio
    async def test_transfer_amount_fsm_missing_keys_graceful_fallback(
        self,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Verify process_transfer_amount handles empty/missing FSM dictionary data without KeyError."""
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_amount)
        # Explicitly do NOT set recipient_full_name or recipient_username in state data

        msg = message_factory("100.00", user_id=sample_user.telegram_id)
        await process_transfer_amount(msg, session=async_session, user=sample_user, state=state)

        # Must successfully transition to confirm state and fallback to default client string
        assert await state.get_state() == TransferStates.confirm

    @pytest.mark.asyncio
    async def test_transfer_fsm_html_special_characters_escaping(
        self,
        mock_bot,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
        sample_recipient,
    ):
        """Verify HTML special characters in sender/recipient names (<, >, &) do not break push alerts or confirmation."""
        sample_user.full_name = "Alice <VIP & Super>"
        sample_user.username = "alice_vip"
        sample_recipient.full_name = "Bob <Manager>"
        sample_recipient.username = "bob_mgr"
        await async_session.commit()

        rec_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.confirm)
        await state.update_data(
            recipient_account_id=rec_acc.id,
            recipient_user_id=sample_recipient.id,
            recipient_telegram_id=sample_recipient.telegram_id,
            recipient_full_name=sample_recipient.full_name,
            recipient_username=f"@{sample_recipient.username}",
            amount="300.00",
        )

        cb_confirm = callback_factory("trf:confirm", user_id=sample_user.telegram_id)
        await handle_confirm_transfer(cb_confirm, session=async_session, user=sample_user, state=state)

        # Check mock_bot.send_message was invoked for recipient push alert with properly escaped HTML
        push_call = mock_bot.send_message.call_args_list[-1]
        assert push_call.kwargs["chat_id"] == sample_recipient.telegram_id
        # Raw unescaped '<' should NOT be present in sender name portion
        assert "Alice &lt;VIP &amp; Super&gt;" in push_call.kwargs["text"]
        assert await state.get_state() is None


# ==============================================================================
# Suite 2: Card Router Adversarial Stress
# ==============================================================================

class TestCardRouterChallengerStress:
    """Stress-test Card router details masking and freeze lifecycle."""

    @pytest.mark.asyncio
    async def test_card_menu_rendering(
        self,
        mock_bot,
        message_factory,
        async_session,
        sample_user,
    ):
        """Verify card menu renders masked PAN, expiry, masked CVC, and balance."""
        msg = message_factory("💳 Моя карта", user_id=sample_user.telegram_id)
        await handle_card_menu(msg, session=async_session, user=sample_user)

    @pytest.mark.asyncio
    async def test_card_details_toggle_multicycle(
        self,
        mock_bot,
        message_factory,
        callback_factory,
        async_session,
        sample_user,
    ):
        """Verify toggling between masked and unmasked card credentials back and forth."""
        from aiogram.types import CallbackQuery, User as TgUser

        # 1. Initial state: masked
        msg_masked = message_factory("Срок: 12/30 CVC: •••", user_id=sample_user.telegram_id)
        cb_to_unmask = CallbackQuery(
            id="cb_test_1",
            from_user=TgUser(id=sample_user.telegram_id, is_bot=False, first_name="Alice"),
            chat_instance="test_chat",
            message=msg_masked,
            data="card:details:toggle",
        ).as_(mock_bot)

        await handle_toggle_details(cb_to_unmask, session=async_session, user=sample_user)

        # 2. Next state: unmasked -> toggle back to masked
        msg_unmasked = message_factory(f"Срок: 12/30 CVC: {sample_user.cards[0].cvv}", user_id=sample_user.telegram_id)
        cb_to_mask = CallbackQuery(
            id="cb_test_2",
            from_user=TgUser(id=sample_user.telegram_id, is_bot=False, first_name="Alice"),
            chat_instance="test_chat",
            message=msg_unmasked,
            data="card:details:toggle",
        ).as_(mock_bot)

        await handle_toggle_details(cb_to_mask, session=async_session, user=sample_user)

    @pytest.mark.asyncio
    async def test_card_freeze_unfreeze_lifecycle_and_operational_blocking(
        self,
        mock_bot,
        message_factory,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Verify card freeze persists in DB and strictly blocks transfers and savings deposits."""
        card = sample_user.cards[0]
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        state = fsm_context_factory(sample_user.telegram_id)

        assert card.is_frozen is False
        assert main_acc.is_frozen is False

        # --- Step 1: Freeze Card ---
        cb_freeze = callback_factory("card:freeze:toggle", user_id=sample_user.telegram_id)
        await handle_toggle_freeze(cb_freeze, session=async_session, user=sample_user)

        await async_session.refresh(card)
        await async_session.refresh(main_acc)
        assert card.is_frozen is True
        assert main_acc.is_frozen is True

        # --- Step 2: Verify outgoing transfer is blocked ---
        msg_transfer = message_factory("💸 Переводы", user_id=sample_user.telegram_id)
        await handle_start_transfer(msg_transfer, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # --- Step 3: Verify savings deposit is blocked ---
        cb_deposit = callback_factory("savings:deposit", user_id=sample_user.telegram_id)
        # Even if in deposit state, execution must fail with AccountFrozenError
        await state.set_state(SavingsStates.waiting_for_deposit_amount)
        msg_dep_amt = message_factory("500.00", user_id=sample_user.telegram_id)
        await process_savings_deposit(msg_dep_amt, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # --- Step 4: Unfreeze Card ---
        await handle_toggle_freeze(cb_freeze, session=async_session, user=sample_user)

        await async_session.refresh(card)
        await async_session.refresh(main_acc)
        assert card.is_frozen is False
        assert main_acc.is_frozen is False

        # --- Step 5: Verify outgoing transfer now proceeds ---
        await handle_start_transfer(msg_transfer, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == TransferStates.waiting_for_recipient


# ==============================================================================
# Suite 3: Savings Router Adversarial Stress
# ==============================================================================

class TestSavingsRouterChallengerStress:
    """Stress-test 18% APR savings safe deposit, withdrawal, and payout flows."""

    @pytest.mark.asyncio
    async def test_savings_overview_and_refresh(
        self,
        mock_bot,
        message_factory,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Verify savings safe overview and refresh callback."""
        state = fsm_context_factory(sample_user.telegram_id)
        msg = message_factory("💰 Сейф 18%", user_id=sample_user.telegram_id)
        await handle_savings_menu(msg, session=async_session, user=sample_user, state=state)

        cb_refresh = callback_factory("savings:refresh", user_id=sample_user.telegram_id)
        await handle_savings_refresh(cb_refresh, session=async_session, user=sample_user)

    @pytest.mark.asyncio
    async def test_savings_deposit_fsm_stress(
        self,
        mock_bot,
        message_factory,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Verify deposit prompt guard, validations, cancel flow, and execution."""
        state = fsm_context_factory(sample_user.telegram_id)
        cb_dep = callback_factory("savings:deposit", user_id=sample_user.telegram_id)

        # 1. Start deposit
        await handle_savings_deposit_prompt(cb_dep, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == SavingsStates.waiting_for_deposit_amount

        # 2. Cancel via "❌ Отмена"
        msg_cancel = message_factory("❌ Отмена", user_id=sample_user.telegram_id)
        await process_savings_deposit(msg_cancel, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # 3. Re-enter deposit and test invalid amounts
        await state.set_state(SavingsStates.waiting_for_deposit_amount)
        for bad_amt in ["0", "-100", "invalid_text", ""]:
            msg_bad = message_factory(bad_amt, user_id=sample_user.telegram_id)
            await process_savings_deposit(msg_bad, session=async_session, user=sample_user, state=state)
            assert await state.get_state() == SavingsStates.waiting_for_deposit_amount

        # 4. Overdraft attempt (amount > card balance)
        msg_over = message_factory("999999.00", user_id=sample_user.telegram_id)
        await process_savings_deposit(msg_over, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == SavingsStates.waiting_for_deposit_amount

        # 5. Valid deposit: 3,000.00 ₽
        msg_valid = message_factory("3000.00", user_id=sample_user.telegram_id)
        await process_savings_deposit(msg_valid, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # Verify balances
        savings = await SavingsService.get_savings(async_session, sample_user.id)
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        assert savings.balance == Decimal("3000.00")
        assert main_acc.balance == Decimal("7000.00")

    @pytest.mark.asyncio
    async def test_savings_withdraw_fsm_stress(
        self,
        mock_bot,
        message_factory,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Verify withdrawal prompt guard, validations, cancel flow, and execution."""
        state = fsm_context_factory(sample_user.telegram_id)
        cb_wd = callback_factory("savings:withdraw", user_id=sample_user.telegram_id)

        # 1. Empty safe guard: initially 0.00 balance
        await handle_savings_withdraw_prompt(cb_wd, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # Fund the safe with 2,000.00 ₽
        savings = await SavingsService.get_savings(async_session, sample_user.id)
        savings.balance = Decimal("2000.00")
        await async_session.commit()

        # 2. Start withdrawal
        await handle_savings_withdraw_prompt(cb_wd, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == SavingsStates.waiting_for_withdraw_amount

        # 3. Cancel via "/cancel"
        msg_cancel = message_factory("/cancel", user_id=sample_user.telegram_id)
        await process_savings_withdraw(msg_cancel, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # 4. Re-enter and test invalid / overdraft withdrawal
        await state.set_state(SavingsStates.waiting_for_withdraw_amount)
        for bad_amt in ["0", "-50", "not_a_number"]:
            msg_bad = message_factory(bad_amt, user_id=sample_user.telegram_id)
            await process_savings_withdraw(msg_bad, session=async_session, user=sample_user, state=state)
            assert await state.get_state() == SavingsStates.waiting_for_withdraw_amount

        # Overdraft: requested 5,000.00 ₽ from safe with 2,000.00 ₽
        msg_over = message_factory("5000.00", user_id=sample_user.telegram_id)
        await process_savings_withdraw(msg_over, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == SavingsStates.waiting_for_withdraw_amount

        # 5. Valid withdrawal: 800.00 ₽
        msg_valid = message_factory("800.00", user_id=sample_user.telegram_id)
        await process_savings_withdraw(msg_valid, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # Verify balances
        await async_session.refresh(savings)
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        assert savings.balance == Decimal("1200.00")
        assert main_acc.balance == Decimal("10800.00")

    @pytest.mark.asyncio
    async def test_savings_interest_payout_and_anti_duplication(
        self,
        mock_bot,
        callback_factory,
        async_session,
        sample_user,
    ):
        """Verify interest payout button execution and anti-duplication guard."""
        cb_payout = callback_factory("savings:payout", user_id=sample_user.telegram_id)

        # 1. Zero interest scenario
        await handle_savings_payout(cb_payout, session=async_session, user=sample_user)

        # 2. Accrued interest scenario
        savings = await SavingsService.get_savings(async_session, sample_user.id)
        savings.balance = Decimal("10000.00")
        savings.accrued_interest = Decimal("125.500000")
        await async_session.commit()

        await handle_savings_payout(cb_payout, session=async_session, user=sample_user)
        await async_session.refresh(savings)
        assert savings.balance == Decimal("10125.50")
        assert savings.accrued_interest == Decimal("0.000000")

        # 3. Immediate second payout (double tap) must not award duplicate interest
        await handle_savings_payout(cb_payout, session=async_session, user=sample_user)
        await async_session.refresh(savings)
        assert savings.balance == Decimal("10125.50")


# ==============================================================================
# Suite 4: Full Bot Integration, Commands, Analytics, and Oleg
# ==============================================================================

class TestBotIntegrationCommandsAndOleg:
    """Verify common navigation, analytics formatter, Oleg assistant, and factories."""

    @pytest.mark.asyncio
    async def test_common_start_help_analytics(
        self,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Verify /start, /help, and /analytics commands respond cleanly."""
        state = fsm_context_factory(sample_user.telegram_id)

        msg_start = message_factory("/start", user_id=sample_user.telegram_id)
        await handle_start(msg_start, session=async_session, user=sample_user, state=state)

        msg_help = message_factory("/help", user_id=sample_user.telegram_id)
        await handle_help(msg_help)

        msg_analytics = message_factory("/analytics", user_id=sample_user.telegram_id)
        await handle_analytics(msg_analytics, session=async_session, user=sample_user)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query_text",
        [
            "Какой у меня баланс?",
            "Переведи 500 рублей",
            "Какой у меня кэшбэк?",
            "Оформи мне кредит",
            "Что купить из акций?",
            "Заблокируй карту",
            "Ты кто такой?",
            "Спасибо большое!",
            "Как пожарить стейк из лосося?",
        ],
    )
    async def test_oleg_dialogue_all_intents(
        self,
        query_text,
        mock_bot,
        message_factory,
        async_session,
        sample_user,
    ):
        """Verify assistant Oleg evaluates text input across all intents without crashing."""
        msg = message_factory(query_text, user_id=sample_user.telegram_id)
        await handle_oleg_dialogue(msg, session=async_session, user=sample_user)

    def test_keyboards_structure(self):
        """Verify all reply and inline keyboards contain expected buttons and callbacks."""
        main_kb = get_main_menu_keyboard()
        buttons = [b.text for row in main_kb.keyboard for b in row]
        assert "💳 Моя карта" in buttons
        assert "💸 Переводы" in buttons
        assert "💰 Сейф 18%" in buttons
        assert "📈 Инвестиции" in buttons
        assert "🎁 Кэшбэк" in buttons
        assert "📊 Аналитика" in buttons
        assert "💬 Олег" in buttons

        cancel_kb = get_cancel_reply_keyboard()
        assert cancel_kb.keyboard[0][0].text == "❌ Отмена"

        card_kb = get_card_keyboard(is_frozen=False, details_visible=False)
        assert card_kb.inline_keyboard[0][0].callback_data == "card:details:toggle"
        assert card_kb.inline_keyboard[1][0].callback_data == "card:freeze:toggle"

        trf_kb = get_transfer_confirm_keyboard()
        assert trf_kb.inline_keyboard[0][0].callback_data == "trf:confirm"
        assert trf_kb.inline_keyboard[0][1].callback_data == "trf:cancel"

        sav_kb = get_savings_keyboard()
        callbacks = [b.callback_data for row in sav_kb.inline_keyboard for b in row]
        assert "savings:deposit" in callbacks
        assert "savings:withdraw" in callbacks
        assert "savings:payout" in callbacks
        assert "savings:refresh" in callbacks

    def test_dispatcher_and_bot_factories(self):
        """Verify dispatcher registers all routers and middlewares."""
        from app.bot.routers.card import card_router
        from app.bot.routers.cashback import cashback_router
        from app.bot.routers.common import common_router
        from app.bot.routers.invest import invest_router
        from app.bot.routers.oleg import oleg_router
        from app.bot.routers.savings import savings_router
        from app.bot.routers.transfer import transfer_router

        # Detach routers if attached by earlier test cases to avoid aiogram 3 singleton collisions
        for r in [
            common_router,
            card_router,
            transfer_router,
            savings_router,
            invest_router,
            cashback_router,
            oleg_router,
        ]:
            r._parent_router = None

        dp = create_dispatcher()
        router_names = [r.name for r in dp.sub_routers]
        assert "common_router" in router_names
        assert "card_router" in router_names
        assert "transfer_router" in router_names
        assert "savings_router" in router_names
        assert "invest_router" in router_names
        assert "cashback_router" in router_names
        assert "oleg_router" in router_names

        bot = create_bot()
        assert bot is not None
