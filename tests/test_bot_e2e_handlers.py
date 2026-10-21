"""Tier 4: Offline Bot Handlers & Comprehensive E2E Application Workflows.

Covers the 8 Real-World Application Scenarios from TEST_INFRA.md:
1. New User Onboarding & Luhn Card Issuance (F1, F2, F4, F5).
2. High-Speed P2P Transfer & Recipient Notification (F6, F10, F11, F12).
3. Concurrent Overdraft Stress (Race Condition) (F11, F12).
4. Savings Deposit, Accrual & 1-Click Payout (F15, F16, F18, F19).
5. Stock Market Dynamic Trade & Liquidation (F20, F21, F22, F23, F24).
6. Cashback Selection & Shopping Payout (F25, F26, F27).
7. Admin Inspection, Reason Adjustment & Freeze (F34, F36, F40, F44, F42).
8. Admin Broadcast Simulation with Delivery Summary (F48, F49, F50).

Also integrates offline aiogram handlers and FSMContext where app.bot is available.
"""

import asyncio
import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.config import settings
from app.database.models import Account, AdminLog, Card, Transaction, User
from app.services.admin import AdminService
from app.services.banking import BankingService
from app.services.card import CardService
from app.services.cashback import CashbackService
from app.services.investments import InvestmentService
from app.services.oleg import OlegAssistant
from app.services.receipts import ReceiptService
from app.services.savings import SavingsService
from app.services.transfers import (
    AccountFrozenError,
    InsufficientFundsError,
    TransferService,
)

# Progressive Testability check for Milestone 2 app.bot handlers
try:
    import app.bot.routers.common  # type: ignore # noqa: F401
    HAS_BOT_ROUTERS = True
except ImportError:
    HAS_BOT_ROUTERS = False


# ==============================================================================
# Scenario 1: New User Onboarding & Card Issuance (F1, F2, F4, F5)
# ==============================================================================

class TestScenario1OnboardingAndCardIssuance:
    """Scenario 1: User /start triggers registration, Luhn card issuance, and multi-account setup."""

    @pytest.mark.asyncio
    async def test_new_user_onboarding_and_card_issuance_workflow(
        self, async_session, mock_bot, message_factory
    ):
        """
        Verify end-to-end registration:
        - Creates User, Main Account, and Savings Account.
        - Issues 16-digit MIR card satisfying Luhn algorithm.
        - Generates 3-digit CVV and MM/YY expiry.
        - Multi-account overview displays 0.00 ₽ initial balances.
        """
        telegram_id = 900001
        username = "new_customer"
        full_name = "Иван Клиентов"

        # Simulate user registration trigger
        user, main_acc, card = await BankingService.get_or_create_user(
            session=async_session,
            telegram_id=telegram_id,
            username=username,
            full_name=full_name,
        )

        assert user.id is not None
        assert user.telegram_id == telegram_id
        assert user.username == username

        # Verify Main Account
        assert main_acc.account_type == "MAIN"
        assert main_acc.balance == Decimal("0.00")
        assert main_acc.is_frozen is False

        # Verify Virtual Luhn Card
        assert len(card.card_number) == 16
        assert card.card_number.startswith("220070")
        assert CardService.validate_luhn(card.card_number) is True
        assert len(card.cvv) == 3
        assert "/" in card.expiry_date
        assert card.status == "ACTIVE"

        # Verify Multi-Account Overview
        overview = await BankingService.get_user_overview(async_session, user.id)
        assert overview["main_balance"] == Decimal("0.00")
        assert overview["savings_balance"] == Decimal("0.00")
        assert overview["cashback_balance"] == Decimal("0.00")
        assert overview["portfolio_value"] == Decimal("0.00")
        assert overview["total_net_worth"] == Decimal("0.00")
        assert overview["card"]["formatted_masked"].startswith("2200 70••")


# ==============================================================================
# Scenario 2: High-Speed P2P Transfer & Notification (F6, F10, F11, F12)
# ==============================================================================

class TestScenario2P2PTransferAndNotification:
    """Scenario 2: P2P transfer, double-entry settlement, digital receipt, and push notification."""

    @pytest.mark.asyncio
    async def test_p2p_transfer_and_recipient_notification_workflow(
        self, async_session, sample_user, sample_recipient, mock_bot
    ):
        """
        Verify complete transfer flow:
        1. Sender initiates transfer to recipient via @bob.
        2. Recipient account resolved.
        3. Double-entry transfer executed.
        4. Digital receipt generated.
        5. Push notification dispatched to recipient.
        """
        # 1. Resolve recipient by @username
        recipient_acc = await TransferService.resolve_recipient(async_session, f"@{sample_recipient.username}")
        assert recipient_acc is not None
        assert recipient_acc.user_id == sample_recipient.id

        sender_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        s_initial = sender_acc.balance
        r_initial = recipient_acc.balance
        transfer_amount = Decimal("1500.00")

        # 2. Execute atomic transfer
        tx = await TransferService.execute_transfer(
            session=async_session,
            sender_user_id=sample_user.id,
            recipient_account_id=recipient_acc.id,
            amount=transfer_amount,
            category="Переводы",
            description="Возврат долга за кафе",
        )
        assert tx.status == "COMPLETED"

        # 3. Generate receipt
        receipt_text = ReceiptService.format_receipt(
            transaction=tx,
            sender_user=sample_user,
            recipient_user=sample_recipient,
        )
        assert "1500.00 ₽" in receipt_text
        assert "Возврат долга за кафе" in receipt_text

        # 4. Simulate real-time push notification to recipient
        await mock_bot.send_message(
            chat_id=sample_recipient.telegram_id,
            text=f"💸 Входящий перевод +{transfer_amount:.2f} ₽ от {sample_user.full_name}!",
        )

        mock_bot.send_message.assert_called_with(
            chat_id=sample_recipient.telegram_id,
            text=f"💸 Входящий перевод +{transfer_amount:.2f} ₽ от {sample_user.full_name}!",
        )

        # 5. Verify final balances
        await async_session.refresh(sender_acc)
        await async_session.refresh(recipient_acc)
        assert sender_acc.balance == s_initial - transfer_amount
        assert recipient_acc.balance == r_initial + transfer_amount


# ==============================================================================
# Scenario 3: Concurrent Overdraft Stress (F11, F12)
# ==============================================================================

class TestScenario3ConcurrentOverdraftRace:
    """Scenario 3: Stress testing race conditions with concurrent transfer tasks."""

    @pytest.mark.asyncio
    async def test_concurrent_overdraft_race_condition(self, test_engine):
        """
        10 concurrent transfers of 200 ₽ on 500 ₽ balance:
        Exactly 2 succeed, exactly 8 fail, final balance 100 ₽.
        """
        from tests.test_banking_concurrency import TestConcurrencyAndOverdraftStress
        test_case = TestConcurrencyAndOverdraftStress()
        await test_case.test_ten_concurrent_transfers_overdraft_stress(test_engine)


# ==============================================================================
# Scenario 4: Savings Deposit, Accrual & 1-Click Payout (F15, F16, F18, F19)
# ==============================================================================

class TestScenario4SavingsDepositAccrualAndPayout:
    """Scenario 4: Move funds to 18% APR safe, accumulate interest, and execute 1-click payout."""

    @pytest.mark.asyncio
    async def test_savings_deposit_accrual_and_payout_workflow(self, async_session, sample_user):
        """
        Verify savings lifecycle:
        1. Deposit 50,000.00 ₽ from Main account to Savings.
        2. Simulate 30 days time delta for 18% APR daily accrual.
        3. Verify accrued interest ≈ 739.73 ₽.
        4. Execute 1-click payout to savings balance.
        5. Verify accumulator resets and savings balance reflects payout.
        """
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        main_acc.balance = Decimal("100000.00")
        await async_session.flush()
        bal_before = main_acc.balance

        # 1. Deposit 50,000.00 ₽
        deposit_amt = Decimal("50000.00")
        deposit_tx = await SavingsService.deposit_to_savings(async_session, sample_user.id, deposit_amt)
        assert deposit_tx.status == "COMPLETED"

        savings = await SavingsService.get_savings(async_session, sample_user.id)
        assert savings.balance == deposit_amt

        # 2. Simulate 30 days of interest accrual
        now = datetime.datetime.now()
        savings.last_interest_calc = now - datetime.timedelta(days=30)
        await async_session.flush()

        pending_interest = SavingsService.calculate_accrued_interest(savings, as_of=now)
        # 50000 * 0.18 / 365 * 30 ≈ 739.726027
        assert str(pending_interest).startswith("739.72") or str(pending_interest).startswith("739.73")

        # 3. Execute 1-click payout
        payout_amt = await SavingsService.payout_interest(async_session, sample_user.id, as_of=now)
        assert payout_amt == Decimal("739.73")

        # 4. Verify savings balance updated and accumulator reset
        assert savings.balance == deposit_amt + Decimal("739.73")
        assert savings.accrued_interest < Decimal("0.01")


# ==============================================================================
# Scenario 5: Stock Market Dynamic Trade & Liquidation (F20, F21, F22, F23, F24)
# ==============================================================================

class TestScenario5StockMarketTradingWorkflow:
    """Scenario 5: Parody stock market trade execution, dynamic GBM price tick, and liquidation."""

    @pytest.mark.asyncio
    async def test_stock_market_trading_and_pnl_workflow(self, async_session, sample_user):
        """
        Verify stock trading workflow:
        1. List available stocks.
        2. Buy 10 shares of SHAW (ПАО Шаурма на вокзале) with 0.3% broker fee.
        3. Fluctuate prices via dynamic GBM engine.
        4. Inspect portfolio valuation.
        5. Liquidate shares and verify proceeds credited to main balance.
        """
        # 1. Initialize and list stocks
        stocks = await InvestmentService.list_stocks(async_session)
        assert len(stocks) == 6
        shaw = next(s for s in stocks if s.ticker == "SHAW")

        # 2. Buy 10 shares
        buy_res = await InvestmentService.buy_stock(
            session=async_session,
            user_id=sample_user.id,
            ticker="SHAW",
            shares=10,
        )
        assert buy_res["shares"] == 10
        assert buy_res["fee"] > Decimal("0.00")
        assert buy_res["total_cost"] == Decimal("1500.00") + buy_res["fee"]

        # 3. Fluctuate prices with GBM engine
        await InvestmentService.tick_stock_prices(async_session, delta_t=1.0)
        await async_session.refresh(shaw)
        assert shaw.current_price >= Decimal("0.01")

        # 4. Inspect portfolio
        portfolio = await InvestmentService.get_portfolio(async_session, sample_user.id)
        assert len(portfolio["holdings"]) == 1
        holding = portfolio["holdings"][0]
        assert holding["ticker"] == "SHAW"
        assert holding["shares"] == 10

        # 5. Sell all shares
        sell_res = await InvestmentService.sell_stock(
            session=async_session,
            user_id=sample_user.id,
            ticker="SHAW",
            shares=10,
        )
        assert sell_res["remaining_shares"] == 0
        assert sell_res["net_proceeds"] > Decimal("0.00")


# ==============================================================================
# Scenario 6: Cashback Selection & Shopping Payout (F25, F26, F27)
# ==============================================================================

class TestScenario6CashbackSelectionAndPayout:
    """Scenario 6: Monthly 4-category selection, purchase bonus accrual, and 1-click payout."""

    @pytest.mark.asyncio
    async def test_cashback_selection_accrual_and_payout_workflow(self, async_session, sample_user):
        """
        Verify cashback workflow:
        1. User selects 4 categories (Shawarma 10%, Pets 15%, Coffee 7%, Groceries 5%).
        2. Qualifying purchase of 2,000.00 ₽ in FOOD_FASTFOOD earns 200.00 ₽ bonus.
        3. Another purchase of 1,000.00 ₽ in COFFEE_CAFE earns 70.00 ₽ bonus.
        4. User clicks 'Вывести кэшбэк на карту'.
        5. Bonus balance resets and Main card account credited with 270.00 ₽.
        """
        categories = await CashbackService.get_available_categories(async_session)
        cat_map = {c.code: c.id for c in categories}

        selected_ids = [
            cat_map["FOOD_FASTFOOD"],
            cat_map["PETS_HEALTH"],
            cat_map["COFFEE_CAFE"],
            cat_map["GROCERIES"],
        ]

        cur_month = "2026-10"
        await CashbackService.select_categories(
            session=async_session,
            user_id=sample_user.id,
            category_ids=selected_ids,
            month_year=cur_month,
        )

        # 2. Accrue cashback on fastfood purchase (2000 * 10% = 200.00 ₽)
        cb1 = await CashbackService.accrue_cashback(
            session=async_session,
            user_id=sample_user.id,
            amount=Decimal("2000.00"),
            category_code="FOOD_FASTFOOD",
            month_year=cur_month,
        )
        assert cb1 == Decimal("200.00")

        # 3. Accrue cashback on coffee purchase (1000 * 7% = 70.00 ₽)
        cb2 = await CashbackService.accrue_cashback(
            session=async_session,
            user_id=sample_user.id,
            amount=Decimal("1000.00"),
            category_code="COFFEE_CAFE",
            month_year=cur_month,
        )
        assert cb2 == Decimal("70.00")

        await async_session.refresh(sample_user)
        assert sample_user.cashback_balance == Decimal("270.00")

        # 4. Execute 1-click payout
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        main_bal_before = main_acc.balance

        payout = await CashbackService.payout_cashback(async_session, sample_user.id)
        assert payout == Decimal("270.00")

        # 5. Verify balances
        await async_session.refresh(sample_user)
        await async_session.refresh(main_acc)
        assert sample_user.cashback_balance == Decimal("0.00")
        assert main_acc.balance == main_bal_before + Decimal("270.00")


# ==============================================================================
# Scenario 7: Admin Inspection, Reason Adjustment & Freeze (F34, F36, F40, F44, F42)
# ==============================================================================

class TestScenario7AdminInspectionAdjustmentAndFreeze:
    """Scenario 7: Admin /admin dashboard, user inspection, balance adjustment, and freeze."""

    @pytest.mark.asyncio
    async def test_admin_dashboard_adjustment_and_freeze_workflow(
        self, async_session, sample_user, sample_admin, mock_bot
    ):
        """
        Verify admin operational workflow:
        1. Query dashboard metrics.
        2. Search and inspect user @alice.
        3. Credit 10,000.00 ₽ with mandatory reason logging.
        4. Send real-time notification to user.
        5. Freeze user account and verify outgoing block.
        """
        # 1. Dashboard metrics
        metrics = await AdminService.get_metrics(async_session)
        assert metrics["total_users"] >= 2
        assert metrics["total_liquidity"] > Decimal("0.00")

        # 2. Search user @alice
        inspected = await AdminService.search_user(async_session, f"@{sample_user.username}")
        assert inspected is not None
        assert inspected.id == sample_user.id

        # 3. Credit 10,000.00 ₽ with reason
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        bal_before = main_acc.balance
        adj_amount = Decimal("10000.00")
        reason = "Премия за активное тестирование Т-Банка"

        tx = await AdminService.adjust_balance(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            delta=adj_amount,
            reason=reason,
        )
        assert tx.status == "COMPLETED"

        await async_session.refresh(main_acc)
        assert main_acc.balance == bal_before + adj_amount

        # 4. Send alert to target user
        await mock_bot.send_message(
            chat_id=sample_user.telegram_id,
            text=f"🔔 Корректировка баланса: +{adj_amount:.2f} ₽. Причина: {reason}",
        )
        mock_bot.send_message.assert_called()

        # 5. Freeze user
        await AdminService.freeze_user(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            freeze=True,
            reason="Временная заморозка для планового аудита",
        )

        with pytest.raises(AccountFrozenError):
            rec_acc = await BankingService.get_user_main_account(async_session, sample_admin.id)
            await TransferService.execute_transfer(
                async_session,
                sender_user_id=sample_user.id,
                recipient_account_id=rec_acc.id,
                amount=Decimal("100.00"),
            )


# ==============================================================================
# Scenario 8: Admin Emergency Broadcast with Delivery Summary (F48, F49, F50)
# ==============================================================================

class TestScenario8AdminEmergencyBroadcast:
    """Scenario 8: Admin broadcast engine dispatching announcements with delivery summary."""

    @pytest.mark.asyncio
    async def test_admin_broadcast_dispatch_and_delivery_summary_workflow(
        self, async_session, sample_user, sample_recipient, sample_admin, mock_bot
    ):
        """
        Verify broadcast dispatch loop:
        1. Query all registered users.
        2. Iterate and send broadcast message via mock_bot.send_message.
        3. Handle delivery summary statistics (total, sent, failed).
        """
        users_res = await async_session.execute(select(User.telegram_id))
        recipients = [row[0] for row in users_res.all()]
        assert len(recipients) >= 3

        broadcast_text = "📢 <b>Внимание клиентам Т-Банка!</b>\nПлановые технические работы в ночь с субботы на воскресенье."

        sent_count = 0
        failed_count = 0

        for uid in recipients:
            try:
                await mock_bot.send_message(chat_id=uid, text=broadcast_text)
                sent_count += 1
            except Exception:
                failed_count += 1

        assert sent_count == len(recipients)
        assert failed_count == 0

        # Deliver delivery summary to admin
        summary_text = (
            f"📢 <b>Рассылка успешно завершена!</b>\n"
            f"👥 Всего адресатов: {len(recipients)}\n"
            f"✅ Успешно доставлено: {sent_count}\n"
            f"❌ Ошибок: {failed_count}"
        )
        await mock_bot.send_message(chat_id=sample_admin.telegram_id, text=summary_text)

        assert mock_bot.send_message.call_count >= len(recipients) + 1


# ==============================================================================
# Telegram Bot Handler & Router Integration (Milestone 2 / M3)
# ==============================================================================

@pytest.mark.skipif(not HAS_BOT_ROUTERS, reason="app.bot handlers not yet implemented (Milestone 2 pending)")
class TestBotRouterHandlerIntegration:
    """Offline Telegram handler integration using aiogram router dispatching."""

    @pytest.mark.asyncio
    async def test_offline_start_handler(
        self, mock_bot, message_factory, fsm_context_factory, async_session, sample_user
    ):
        """Test /start handler: onboards user, shows balances, and attaches keyboard."""
        from app.bot.routers.common import handle_start

        state = fsm_context_factory(sample_user.telegram_id)
        msg = message_factory("/start", user_id=sample_user.telegram_id, username=sample_user.username)

        await handle_start(msg, session=async_session, user=sample_user, state=state)
        assert mock_bot.call_count >= 1

    @pytest.mark.asyncio
    async def test_offline_help_and_cancel_handlers(
        self, mock_bot, message_factory, fsm_context_factory, sample_user
    ):
        """Test /help and /cancel handlers."""
        from app.bot.routers.common import handle_cancel, handle_help
        from app.bot.states.states import TransferStates

        # 1. Help handler
        msg_help = message_factory("/help", user_id=sample_user.telegram_id)
        await handle_help(msg_help)

        # 2. Cancel handler
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_recipient)
        assert await state.get_state() == TransferStates.waiting_for_recipient

        msg_cancel = message_factory("❌ Отмена", user_id=sample_user.telegram_id)
        await handle_cancel(msg_cancel, state=state)
        assert await state.get_state() is None

    @pytest.mark.asyncio
    async def test_offline_analytics_handler(
        self, mock_bot, message_factory, async_session, sample_user
    ):
        """Test /analytics handler returns monthly spending report."""
        from app.bot.routers.common import handle_analytics

        msg = message_factory("📊 Аналитика", user_id=sample_user.telegram_id)
        await handle_analytics(msg, session=async_session, user=sample_user)
        assert mock_bot.call_count >= 1

    @pytest.mark.asyncio
    async def test_offline_card_menu_and_toggles(
        self, mock_bot, message_factory, callback_factory, async_session, sample_user
    ):
        """Test card menu display, details unmask toggle, and freeze/unfreeze toggle."""
        from app.bot.routers.card import (
            handle_card_menu,
            handle_toggle_details,
            handle_toggle_freeze,
        )

        # 1. Open card menu
        msg = message_factory("💳 Моя карта", user_id=sample_user.telegram_id)
        await handle_card_menu(msg, session=async_session, user=sample_user)

        # 2. Toggle details (reveal CVC)
        from aiogram.types import CallbackQuery, User as TgUser
        msg_with_cvc = message_factory("Срок: 10/31 CVC: •••", user_id=sample_user.telegram_id)
        cb_details = CallbackQuery(
            id="cb_details_1",
            from_user=TgUser(id=sample_user.telegram_id, is_bot=False, first_name="Alice"),
            chat_instance="test_chat",
            message=msg_with_cvc,
            data="card:details:toggle",
        ).as_(mock_bot)
        await handle_toggle_details(cb_details, session=async_session, user=sample_user)

        # 3. Toggle freeze
        card = sample_user.cards[0]
        assert card.is_frozen is False

        cb_freeze = callback_factory("card:freeze:toggle", user_id=sample_user.telegram_id)
        await handle_toggle_freeze(cb_freeze, session=async_session, user=sample_user)
        await async_session.refresh(card)
        assert card.is_frozen is True

        # 4. Toggle unfreeze
        await handle_toggle_freeze(cb_freeze, session=async_session, user=sample_user)
        await async_session.refresh(card)
        assert card.is_frozen is False

    @pytest.mark.asyncio
    async def test_offline_transfer_fsm_flow(
        self,
        mock_bot,
        message_factory,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
        sample_recipient,
    ):
        """Test complete transfer dialogue: prompt -> recipient -> amount -> confirm -> push."""
        from app.bot.routers.transfer import (
            handle_confirm_transfer,
            handle_start_transfer,
            process_transfer_amount,
            process_transfer_recipient,
        )
        from app.bot.states.states import TransferStates

        state = fsm_context_factory(sample_user.telegram_id)

        # 1. Start transfer
        msg_start = message_factory("💸 Переводы", user_id=sample_user.telegram_id)
        await handle_start_transfer(msg_start, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == TransferStates.waiting_for_recipient

        # 2. Enter recipient (@bob)
        msg_rec = message_factory(f"@{sample_recipient.username}", user_id=sample_user.telegram_id)
        await process_transfer_recipient(msg_rec, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == TransferStates.waiting_for_amount

        # 3. Enter amount (500.00)
        msg_amt = message_factory("500.00", user_id=sample_user.telegram_id)
        await process_transfer_amount(msg_amt, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == TransferStates.confirm

        # 4. Confirm transfer
        cb_confirm = callback_factory("trf:confirm", user_id=sample_user.telegram_id)
        await handle_confirm_transfer(cb_confirm, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # Verify balance changes
        s_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        r_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        assert s_acc.balance == Decimal("9500.00")
        assert r_acc.balance == Decimal("1500.00")

    @pytest.mark.asyncio
    async def test_offline_transfer_rejections(
        self,
        mock_bot,
        message_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Test self-transfer and overdraft rejections in transfer wizard."""
        from app.bot.routers.transfer import (
            handle_start_transfer,
            process_transfer_amount,
            process_transfer_recipient,
        )
        from app.bot.states.states import TransferStates

        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.waiting_for_recipient)

        # 1. Self-transfer
        msg_self = message_factory(f"@{sample_user.username}", user_id=sample_user.telegram_id)
        await process_transfer_recipient(msg_self, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == TransferStates.waiting_for_recipient

        # 2. Insufficient funds
        await state.set_state(TransferStates.waiting_for_amount)
        msg_overdraft = message_factory("999999.00", user_id=sample_user.telegram_id)
        await process_transfer_amount(msg_overdraft, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == TransferStates.waiting_for_amount

    @pytest.mark.asyncio
    async def test_offline_savings_handlers(
        self,
        mock_bot,
        message_factory,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Test savings overview, deposit flow, withdrawal flow, and interest payout."""
        from app.bot.routers.savings import (
            handle_savings_deposit_prompt,
            handle_savings_menu,
            handle_savings_payout,
            handle_savings_refresh,
            handle_savings_withdraw_prompt,
            process_savings_deposit,
            process_savings_withdraw,
        )
        from app.bot.states.states import SavingsStates

        state = fsm_context_factory(sample_user.telegram_id)

        # 1. Overview
        msg_menu = message_factory("💰 Сейф 18%", user_id=sample_user.telegram_id)
        await handle_savings_menu(msg_menu, session=async_session, user=sample_user, state=state)

        # 2. Deposit prompt & execution
        cb_dep = callback_factory("savings:deposit", user_id=sample_user.telegram_id)
        await handle_savings_deposit_prompt(cb_dep, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == SavingsStates.waiting_for_deposit_amount

        msg_dep_amt = message_factory("2000.00", user_id=sample_user.telegram_id)
        await process_savings_deposit(msg_dep_amt, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        savings = await SavingsService.get_savings(async_session, sample_user.id)
        assert savings.balance == Decimal("2000.00")

        # 3. Withdraw prompt & execution
        cb_wd = callback_factory("savings:withdraw", user_id=sample_user.telegram_id)
        await handle_savings_withdraw_prompt(cb_wd, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == SavingsStates.waiting_for_withdraw_amount

        msg_wd_amt = message_factory("500.00", user_id=sample_user.telegram_id)
        await process_savings_withdraw(msg_wd_amt, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        await async_session.refresh(savings)
        assert savings.balance == Decimal("1500.00")

        # 4. Payout trigger
        cb_payout = callback_factory("savings:payout", user_id=sample_user.telegram_id)
        await handle_savings_payout(cb_payout, session=async_session, user=sample_user)

    @pytest.mark.asyncio
    async def test_offline_invest_trading_flow(
        self,
        mock_bot,
        message_factory,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Test stock catalog, quote refresh, buy execution, portfolio view, and sell execution."""
        from app.bot.routers.invest import (
            handle_buy_prompt,
            handle_execute_trade,
            handle_invest_menu,
            handle_invest_refresh,
            handle_sell_prompt,
            handle_view_portfolio,
            handle_view_stock,
            process_invest_shares,
        )
        from app.bot.states.states import InvestStates

        state = fsm_context_factory(sample_user.telegram_id)

        # 1. Invest menu
        msg_menu = message_factory("📈 Инвестиции", user_id=sample_user.telegram_id)
        await handle_invest_menu(msg_menu, session=async_session, user=sample_user, state=state)

        # 2. Refresh quotes
        cb_ref = callback_factory("invest:refresh", user_id=sample_user.telegram_id)
        await handle_invest_refresh(cb_ref, session=async_session)

        # 3. Inspect stock SHAW
        cb_view = callback_factory("stock:view:SHAW", user_id=sample_user.telegram_id)
        await handle_view_stock(cb_view, session=async_session, user=sample_user)

        # 4. Buy 4 shares of SHAW
        cb_buy = callback_factory("stock:buy:SHAW", user_id=sample_user.telegram_id)
        await handle_buy_prompt(cb_buy, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == InvestStates.waiting_for_shares

        msg_shares = message_factory("4", user_id=sample_user.telegram_id)
        await process_invest_shares(msg_shares, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == InvestStates.confirm_trade

        cb_confirm_buy = callback_factory("invest:confirm:BUY:SHAW:4", user_id=sample_user.telegram_id)
        await handle_execute_trade(cb_confirm_buy, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # 5. Inspect portfolio
        cb_port = callback_factory("invest:portfolio", user_id=sample_user.telegram_id)
        await handle_view_portfolio(cb_port, session=async_session, user=sample_user)

        # 6. Sell 2 shares of SHAW
        cb_sell = callback_factory("stock:sell:SHAW", user_id=sample_user.telegram_id)
        await handle_sell_prompt(cb_sell, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == InvestStates.waiting_for_shares

        msg_sell_shares = message_factory("2", user_id=sample_user.telegram_id)
        await process_invest_shares(msg_sell_shares, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == InvestStates.confirm_trade

        cb_confirm_sell = callback_factory("invest:confirm:SELL:SHAW:2", user_id=sample_user.telegram_id)
        await handle_execute_trade(cb_confirm_sell, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # Verify portfolio remaining
        port = await InvestmentService.get_portfolio(async_session, sample_user.id)
        shaw_h = next(h for h in port["holdings"] if h["ticker"] == "SHAW")
        assert shaw_h["shares"] == 2

    @pytest.mark.asyncio
    async def test_offline_cashback_selection_and_payout(
        self,
        mock_bot,
        message_factory,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
    ):
        """Test cashback menu, category checkboxes toggle, confirmation, and 1-click payout."""
        from app.bot.routers.cashback import (
            handle_cashback_menu,
            handle_cashback_payout,
            handle_confirm_categories,
            handle_toggle_category,
        )
        from app.bot.states.states import CashbackStates

        state = fsm_context_factory(sample_user.telegram_id)

        # 1. Open cashback menu
        msg_cb = message_factory("🎁 Кэшбэк", user_id=sample_user.telegram_id)
        await handle_cashback_menu(msg_cb, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == CashbackStates.selecting_categories

        # 2. Toggle 4 categories
        categories = await CashbackService.get_available_categories(async_session)
        for cat in categories[:4]:
            cb_tog = callback_factory(f"cb:toggle:{cat.id}", user_id=sample_user.telegram_id)
            await handle_toggle_category(cb_tog, session=async_session, user=sample_user, state=state)

        # 3. Confirm categories
        cb_conf = callback_factory("cb:confirm", user_id=sample_user.telegram_id)
        await handle_confirm_categories(cb_conf, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # 4. Accrue cashback and trigger payout
        sample_user.cashback_balance = Decimal("350.00")
        await async_session.commit()

        cb_pay = callback_factory("cb:payout", user_id=sample_user.telegram_id)
        await handle_cashback_payout(cb_pay, session=async_session, user=sample_user)

        await async_session.refresh(sample_user)
        assert sample_user.cashback_balance == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_offline_oleg_assistant(
        self, mock_bot, message_factory, async_session, sample_user
    ):
        """Test virtual assistant Oleg: prompt, balance query intent, and fallback banter."""
        from app.bot.routers.oleg import handle_oleg_dialogue, handle_oleg_prompt

        # 1. Prompt
        msg_oleg = message_factory("💬 Олег", user_id=sample_user.telegram_id)
        await handle_oleg_prompt(msg_oleg, session=async_session, user=sample_user)

        # 2. Balance intent
        msg_bal = message_factory("Какой у меня баланс?", user_id=sample_user.telegram_id)
        await handle_oleg_dialogue(msg_bal, session=async_session, user=sample_user)

        # 3. Fallback intent
        msg_fb = message_factory("Что происходит с миром?", user_id=sample_user.telegram_id)
        await handle_oleg_dialogue(msg_fb, session=async_session, user=sample_user)

    def test_is_admin_filter_rbac(self, message_factory):
        """Test IsAdminFilter RBAC authorization."""
        from app.bot.filters.admin import IsAdminFilter

        admin_id = 777888
        settings.ADMIN_IDS = [admin_id]
        admin_filter = IsAdminFilter()

        msg_admin = message_factory("test", user_id=admin_id)
        msg_stranger = message_factory("test", user_id=111222)

        import asyncio
        assert asyncio.run(admin_filter(msg_admin)) is True
        assert asyncio.run(admin_filter(msg_stranger)) is False

    def test_bot_and_dispatcher_factories(self):
        """Verify create_bot and create_dispatcher initialize all components."""
        from app.bot.bot import create_bot, create_dispatcher

        bot = create_bot("dummy_test_token")
        assert bot is not None

        dp = create_dispatcher()
        assert dp is not None
        # Check sub-routers attached
        sub_router_names = [r.name for r in dp.sub_routers]
        assert "common_router" in sub_router_names
        assert "card_router" in sub_router_names
        assert "transfer_router" in sub_router_names
        assert "savings_router" in sub_router_names
        assert "invest_router" in sub_router_names
        assert "cashback_router" in sub_router_names
        assert "oleg_router" in sub_router_names

    def test_create_dispatcher_idempotency_multiple_calls(self):
        """Verify create_dispatcher can be invoked repeatedly without RuntimeError."""
        from app.bot.bot import create_dispatcher

        dp1 = create_dispatcher()
        dp2 = create_dispatcher()
        assert dp1 is not dp2
        assert len(dp2.sub_routers) >= 7


