"""Empirical Verification & Adversarial Stress Tests for Milestone 2.

Focus Areas:
1. Investments Dynamic Stock Catalog, Buy/Sell FSM, Broker Fee, and Portfolio Valuation.
2. Cashback 4-of-8 Category Selector, Accrual Math, and 1-Click Bonus Payout.
3. Visual Formatters and Analytics.
4. Formatters: T-Bank Black Box Layout, Digital Receipts, and Unicode Progress Bars.
"""

import asyncio
import datetime
from decimal import Decimal
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Account, CashbackCategory, InvestmentPortfolio, Stock, Transaction, User
from app.services.banking import BankingService
from app.services.cashback import CashbackService
from app.services.investments import (
    InsufficientSharesError,
    InvestmentService,
)
from app.services.receipts import ReceiptService
from app.services.transfers import InsufficientFundsError


# ==============================================================================
# 1. INVESTMENTS DYNAMIC CATALOG, BUY/SELL FSM, BROKER FEE & PORTFOLIO VALUATION
# ==============================================================================

class TestInvestmentsEmpirical:
    """Rigorous empirical testing of parody stock market simulation."""

    def test_dynamic_pricing_floor_and_extreme_volatility(self):
        """Verify that dynamic GBM prices never drop below the 0.01 ₽ floor under extreme stress."""
        base_price = Decimal("150.00")
        current_price = Decimal("0.02")

        # Massive downward shock (epsilon = -10.0, high volatility)
        next_price = InvestmentService.calculate_next_price(
            current_price=current_price,
            base_price=base_price,
            volatility=0.99,
            mean_reversion_rate=0.01,
            delta_t=1.0,
            epsilon=-10.0,
        )
        assert next_price >= Decimal("0.01"), f"Price dropped below floor: {next_price}"
        assert next_price == Decimal("0.01")

        # 1,000 continuous random ticks from a penny stock
        price = Decimal("0.05")
        for _ in range(1000):
            price = InvestmentService.calculate_next_price(
                current_price=price,
                base_price=Decimal("1.50"),
                volatility=0.25,
                mean_reversion_rate=0.05,
            )
            assert price >= Decimal("0.01")

    @pytest.mark.asyncio
    async def test_stock_catalog_seeding_and_ticking(self, async_session):
        """Verify stock catalog seeding (6 default stocks) and dynamic price tick persistence."""
        stocks = await InvestmentService.list_stocks(async_session)
        assert len(stocks) == 6
        tickers = {s.ticker for s in stocks}
        assert tickers == {"SHAW", "MTCH", "BBRS", "DVAN", "GRECH", "MYK"}

        initial_prices = {s.ticker: s.current_price for s in stocks}

        # Tick prices and persist
        ticked_stocks = await InvestmentService.tick_stock_prices(async_session, delta_t=1.0)
        await async_session.commit()

        assert len(ticked_stocks) == 6
        for s in ticked_stocks:
            assert s.current_price >= Decimal("0.01")

    @pytest.mark.asyncio
    async def test_buy_shares_broker_fee_and_cost_basis(self, async_session, sample_user):
        """Verify 0.3% broker fee calculation, total cost debit, and weighted average price updates."""
        stock = await InvestmentService.get_stock(async_session, "SHAW")
        stock.current_price = Decimal("200.00")
        await async_session.commit()

        # Buy 5 shares @ 200.00 ₽ = 1,000.00 ₽ subtotal
        # Fee = 1,000.00 * 0.003 = 3.00 ₽. Total cost = 1,003.00 ₽.
        res1 = await InvestmentService.buy_stock(async_session, sample_user.id, "SHAW", 5)
        await async_session.commit()

        assert res1["shares"] == 5
        assert res1["fee"] == Decimal("3.00")
        assert res1["total_cost"] == Decimal("1003.00")
        assert res1["total_shares"] == 5
        assert res1["average_buy_price"] == Decimal("200.00")

        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        assert main_acc.balance == Decimal("10000.00") - Decimal("1003.00")

        # Price changes to 300.00 ₽
        stock.current_price = Decimal("300.00")
        await async_session.commit()

        # Buy 5 more shares @ 300.00 ₽ = 1,500.00 ₽ subtotal. Fee = 4.50 ₽. Total cost = 1,504.50 ₽.
        res2 = await InvestmentService.buy_stock(async_session, sample_user.id, "SHAW", 5)
        await async_session.commit()

        assert res2["total_shares"] == 10
        # Weighted average price: (5 * 200 + 5 * 300) / 10 = 250.00 ₽
        assert res2["average_buy_price"] == Decimal("250.00")
        assert res2["fee"] == Decimal("4.50")

    @pytest.mark.asyncio
    async def test_buy_shares_rejection_on_insufficient_funds_or_invalid_shares(
        self, async_session, sample_user
    ):
        """Verify invalid shares and insufficient balance rejections."""
        # Non-positive shares
        with pytest.raises(ValueError, match="положительным"):
            await InvestmentService.buy_stock(async_session, sample_user.id, "SHAW", 0)

        with pytest.raises(ValueError, match="положительным"):
            await InvestmentService.buy_stock(async_session, sample_user.id, "SHAW", -10)

        # Non-existent stock
        with pytest.raises(ValueError, match="не найдена"):
            await InvestmentService.buy_stock(async_session, sample_user.id, "NONEXISTENT", 5)

        # Insufficient funds (try to buy 1000 shares of DVAN @ 1337.00 ₽)
        with pytest.raises(InsufficientFundsError):
            await InvestmentService.buy_stock(async_session, sample_user.id, "DVAN", 1000)

    @pytest.mark.asyncio
    async def test_buy_shares_blocked_on_frozen_account(self, async_session, sample_user):
        """Verify buying shares fails when account is frozen."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        main_acc.is_frozen = True
        await async_session.commit()

        with pytest.raises(InsufficientFundsError):
            await InvestmentService.buy_stock(async_session, sample_user.id, "SHAW", 1)

    @pytest.mark.asyncio
    async def test_sell_shares_proceeds_fee_and_realized_pnl(self, async_session, sample_user):
        """Verify selling shares with fee deduction, balance credit, and realized P&L."""
        stock = await InvestmentService.get_stock(async_session, "GRECH")
        stock.current_price = Decimal("100.00")
        await async_session.commit()

        # 1. Buy 10 shares @ 100.00 ₽
        await InvestmentService.buy_stock(async_session, sample_user.id, "GRECH", 10)
        await async_session.commit()

        # 2. Stock price rises to 150.00 ₽
        stock.current_price = Decimal("150.00")
        await async_session.commit()

        # 3. Sell 4 shares:
        # Subtotal = 4 * 150.00 = 600.00 ₽
        # Fee = 600.00 * 0.003 = 1.80 ₽
        # Net proceeds = 600.00 - 1.80 = 598.20 ₽
        # Cost basis = 4 * 100.00 = 400.00 ₽
        # Realized P&L = 598.20 - 400.00 = +198.20 ₽
        res = await InvestmentService.sell_stock(async_session, sample_user.id, "GRECH", 4)
        await async_session.commit()

        assert res["shares"] == 4
        assert res["net_proceeds"] == Decimal("598.20")
        assert res["realized_pnl"] == Decimal("198.20")
        assert res["remaining_shares"] == 6

        # 4. Sell at a loss: price drops to 50.00 ₽
        stock.current_price = Decimal("50.00")
        await async_session.commit()

        # Sell remaining 6 shares:
        # Subtotal = 6 * 50.00 = 300.00 ₽
        # Fee = 300.00 * 0.003 = 0.90 ₽
        # Net proceeds = 300.00 - 0.90 = 299.10 ₽
        # Cost basis = 6 * 100.00 = 600.00 ₽
        # Realized P&L = 299.10 - 600.00 = -300.90 ₽
        loss_res = await InvestmentService.sell_stock(async_session, sample_user.id, "GRECH", 6)
        await async_session.commit()

        assert loss_res["shares"] == 6
        assert loss_res["net_proceeds"] == Decimal("299.10")
        assert loss_res["realized_pnl"] == Decimal("-300.90")
        assert loss_res["remaining_shares"] == 0

        # Portfolio should now have 0 shares and reset average buy price
        port_res = await async_session.execute(
            select(InvestmentPortfolio)
            .where(InvestmentPortfolio.user_id == sample_user.id)
            .where(InvestmentPortfolio.stock_id == stock.id)
        )
        port = port_res.scalars().first()
        assert port.shares == 0
        assert port.average_buy_price == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_sell_shares_insufficient_holdings_rejection(self, async_session, sample_user):
        """Verify selling more shares than owned or selling unowned stocks raises InsufficientSharesError."""
        # Unowned stock
        with pytest.raises(InsufficientSharesError):
            await InvestmentService.sell_stock(async_session, sample_user.id, "SHAW", 1)

        # Buy 2 shares then attempt to sell 5
        await InvestmentService.buy_stock(async_session, sample_user.id, "SHAW", 2)
        await async_session.commit()

        with pytest.raises(InsufficientSharesError, match="Недостаточно акций"):
            await InvestmentService.sell_stock(async_session, sample_user.id, "SHAW", 5)

    @pytest.mark.asyncio
    async def test_portfolio_valuation_calculation(self, async_session, sample_user):
        """Verify aggregate portfolio valuation, invested sum, and total P&L math."""
        # Empty portfolio
        empty_port = await InvestmentService.get_portfolio(async_session, sample_user.id)
        assert empty_port["total_value"] == Decimal("0.00")
        assert empty_port["total_invested"] == Decimal("0.00")
        assert empty_port["total_pnl"] == Decimal("0.00")
        assert empty_port["total_pnl_percent"] == 0.0
        assert len(empty_port["holdings"]) == 0

        # Buy 2 different stocks
        stock1 = await InvestmentService.get_stock(async_session, "SHAW")
        stock1.current_price = Decimal("100.00")
        stock2 = await InvestmentService.get_stock(async_session, "MTCH")
        stock2.current_price = Decimal("2.00")
        await async_session.commit()

        await InvestmentService.buy_stock(async_session, sample_user.id, "SHAW", 10)  # cost: 1000.00 (avg 100.00)
        await InvestmentService.buy_stock(async_session, sample_user.id, "MTCH", 50)  # cost: 100.00 (avg 2.00)
        await async_session.commit()

        # Price movements: SHAW -> 120.00 (+20%), MTCH -> 1.50 (-25%)
        stock1.current_price = Decimal("120.00")
        stock2.current_price = Decimal("1.50")
        await async_session.commit()

        port = await InvestmentService.get_portfolio(async_session, sample_user.id)
        # Expected value: (10 * 120) + (50 * 1.50) = 1200 + 75 = 1275.00 ₽
        # Expected invested: (10 * 100) + (50 * 2.00) = 1000 + 100 = 1100.00 ₽
        # Expected total P&L: 1275 - 1100 = +175.00 ₽
        # Expected P&L %: (175 / 1100) * 100 = 15.909... %
        assert port["total_value"] == Decimal("1275.00")
        assert port["total_invested"] == Decimal("1100.00")
        assert port["total_pnl"] == Decimal("175.00")
        assert round(port["total_pnl_percent"], 2) == 15.91

    @pytest.mark.asyncio
    async def test_invest_router_fsm_validation_and_cancellation(
        self, mock_bot, message_factory, callback_factory, fsm_context_factory, async_session, sample_user
    ):
        """Stress-test FSM share quantity inputs: non-numeric, zero, negative, cancel."""
        from app.bot.routers.invest import (
            handle_buy_prompt,
            handle_invest_cancel,
            process_invest_shares,
        )
        from app.bot.states.states import InvestStates

        state = fsm_context_factory(sample_user.telegram_id)

        # Trigger buy prompt for MTCH
        cb_buy = callback_factory("stock:buy:MTCH", user_id=sample_user.telegram_id)
        await handle_buy_prompt(cb_buy, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == InvestStates.waiting_for_shares

        # Test invalid input "abc"
        msg_invalid1 = message_factory("abc", user_id=sample_user.telegram_id)
        await process_invest_shares(msg_invalid1, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == InvestStates.waiting_for_shares  # remains in state

        # Test negative input "-10"
        msg_invalid2 = message_factory("-10", user_id=sample_user.telegram_id)
        await process_invest_shares(msg_invalid2, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == InvestStates.waiting_for_shares

        # Test zero "0"
        msg_invalid3 = message_factory("0", user_id=sample_user.telegram_id)
        await process_invest_shares(msg_invalid3, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == InvestStates.waiting_for_shares

        # Test cancel via reply keyboard "❌ Отмена"
        msg_cancel = message_factory("❌ Отмена", user_id=sample_user.telegram_id)
        await process_invest_shares(msg_cancel, session=async_session, user=sample_user, state=state)
        assert await state.get_state() is None

        # Test cancel via inline callback
        await state.set_state(InvestStates.confirm_trade)
        cb_cancel = callback_factory("invest:cancel", user_id=sample_user.telegram_id)
        await handle_invest_cancel(cb_cancel, state=state)
        assert await state.get_state() is None


# ==============================================================================
# 2. CASHBACK 4-OF-8 SELECTOR & 1-CLICK BONUS PAYOUT
# ==============================================================================

class TestCashbackEmpirical:
    """Rigorous empirical testing of monthly cashback selection and bonus payout."""

    @pytest.mark.asyncio
    async def test_cashback_categories_initialization(self, async_session):
        """Verify exactly 8 humorous categories are initialized."""
        cats = await CashbackService.get_available_categories(async_session)
        assert len(cats) == 8
        codes = {c.code for c in cats}
        assert "FOOD_FASTFOOD" in codes
        assert "PETS_HEALTH" in codes
        assert "COFFEE_CAFE" in codes
        assert "ALL_GENERAL" in codes

    @pytest.mark.asyncio
    async def test_category_selection_limit_and_permanence(self, async_session, sample_user):
        """Verify selecting > 4 categories is rejected, and locked categories cannot be changed."""
        cats = await CashbackService.get_available_categories(async_session)
        cat_ids = [c.id for c in cats]

        # 1. Reject > 4 categories
        with pytest.raises(ValueError, match="максимум 4"):
            await CashbackService.select_categories(
                async_session, sample_user.id, cat_ids[:5], month_year="2026-11"
            )

        # 2. Reject duplicate category IDs
        with pytest.raises(ValueError, match="не должны повторяться"):
            await CashbackService.select_categories(
                async_session, sample_user.id, [cat_ids[0], cat_ids[0], cat_ids[1], cat_ids[2]], month_year="2026-11"
            )

        # 3. Reject invalid category ID
        with pytest.raises(ValueError, match="не существует"):
            await CashbackService.select_categories(
                async_session, sample_user.id, [cat_ids[0], cat_ids[1], cat_ids[2], 99999], month_year="2026-11"
            )

        # 4. Valid selection of 4 categories
        await CashbackService.select_categories(
            async_session, sample_user.id, cat_ids[:4], month_year="2026-11"
        )
        await async_session.commit()

        selected = await CashbackService.get_user_categories(async_session, sample_user.id, month_year="2026-11")
        assert len(selected) == 4

        # 5. Attempting to change selection for the same month must fail
        with pytest.raises(ValueError, match="уже зафиксированы"):
            await CashbackService.select_categories(
                async_session, sample_user.id, cat_ids[2:6], month_year="2026-11"
            )

    @pytest.mark.asyncio
    async def test_cashback_accrual_calculation(self, async_session, sample_user):
        """Verify cashback percentage calculation for matched category vs general fallback."""
        cats = await CashbackService.get_available_categories(async_session)
        # Select: FOOD_FASTFOOD (10%), PETS_HEALTH (15%), COFFEE_CAFE (7%), ALL_GENERAL (1%)
        chosen = [c.id for c in cats if c.code in ["FOOD_FASTFOOD", "PETS_HEALTH", "COFFEE_CAFE", "ALL_GENERAL"]]
        await CashbackService.select_categories(async_session, sample_user.id, chosen, month_year="2026-12")
        await async_session.commit()

        # Purchase in FOOD_FASTFOOD (10%): 500.00 ₽ -> 50.00 ₽
        cb1 = await CashbackService.calculate_cashback(
            async_session, sample_user.id, Decimal("500.00"), "FOOD_FASTFOOD", month_year="2026-12"
        )
        assert cb1 == Decimal("50.00")

        # Purchase in PETS_HEALTH (15%): 1,234.56 ₽ -> 185.18 ₽ (rounded half up)
        cb2 = await CashbackService.calculate_cashback(
            async_session, sample_user.id, Decimal("1234.56"), "PETS_HEALTH", month_year="2026-12"
        )
        assert cb2 == Decimal("185.18")

        # Purchase in unselected category (e.g. TAXI_TRANSPORT) -> falls back to ALL_GENERAL (1%): 1,000.00 ₽ -> 10.00 ₽
        cb3 = await CashbackService.calculate_cashback(
            async_session, sample_user.id, Decimal("1000.00"), "TAXI_TRANSPORT", month_year="2026-12"
        )
        assert cb3 == Decimal("10.00")

        # Negative or 0 purchase
        cb4 = await CashbackService.calculate_cashback(
            async_session, sample_user.id, Decimal("-100.00"), "FOOD_FASTFOOD", month_year="2026-12"
        )
        assert cb4 == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_cashback_payout_cas_and_atomic_credit(self, async_session, sample_user):
        """Verify 1-click bonus payout atomically credits Main card and zeroes bonus balance."""
        # 1. Payout with 0 balance must fail
        sample_user.cashback_balance = Decimal("0.50")
        await async_session.commit()

        with pytest.raises(ValueError, match="минимум 1.00"):
            await CashbackService.payout_cashback(async_session, sample_user.id)

        # 2. Set balance to 750.25 ₽ and payout
        sample_user.cashback_balance = Decimal("750.25")
        await async_session.commit()

        initial_main = (await BankingService.get_user_main_account(async_session, sample_user.id)).balance

        payout_amt = await CashbackService.payout_cashback(async_session, sample_user.id)
        await async_session.commit()

        assert payout_amt == Decimal("750.25")
        await async_session.refresh(sample_user)
        assert sample_user.cashback_balance == Decimal("0.00")

        new_main = (await BankingService.get_user_main_account(async_session, sample_user.id)).balance
        assert new_main == initial_main + Decimal("750.25")

        # Transaction audit entry
        tx_res = await async_session.execute(
            select(Transaction).where(Transaction.tx_type == "CASHBACK_PAYOUT")
        )
        tx = tx_res.scalars().first()
        assert tx is not None
        assert tx.amount == Decimal("750.25")
        assert tx.auth_code.startswith("AUTH-")
        assert tx.fiscal_code.startswith("ФП-")

    @pytest.mark.asyncio
    async def test_cashback_router_fsm_toggle_and_confirm(
        self, callback_factory, fsm_context_factory, async_session, sample_user
    ):
        """Test cashback router checkbox toggling up to 4 and confirmation validation."""
        from app.bot.routers.cashback import handle_confirm_categories, handle_toggle_category
        from app.bot.states.states import CashbackStates

        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(CashbackStates.selecting_categories)
        await state.update_data(selected_ids=[])

        cats = await CashbackService.get_available_categories(async_session)

        # Toggle first 4 categories
        for c in cats[:4]:
            cb = callback_factory(f"cb:toggle:{c.id}", user_id=sample_user.telegram_id)
            await handle_toggle_category(cb, session=async_session, user=sample_user, state=state)

        data = await state.get_data()
        assert len(data["selected_ids"]) == 4

        # Attempt to toggle 5th category (must be blocked by router, remaining at 4)
        cb5 = callback_factory(f"cb:toggle:{cats[4].id}", user_id=sample_user.telegram_id)
        await handle_toggle_category(cb5, session=async_session, user=sample_user, state=state)
        data = await state.get_data()
        assert len(data["selected_ids"]) == 4

        # Untoggle 1 category -> becomes 3
        cb_untoggle = callback_factory(f"cb:toggle:{cats[0].id}", user_id=sample_user.telegram_id)
        await handle_toggle_category(cb_untoggle, session=async_session, user=sample_user, state=state)
        data = await state.get_data()
        assert len(data["selected_ids"]) == 3

        # Confirming with 3 categories must be rejected
        cb_confirm_fail = callback_factory("cb:confirm", user_id=sample_user.telegram_id)
        await handle_confirm_categories(cb_confirm_fail, session=async_session, user=sample_user, state=state)
        assert await state.get_state() == CashbackStates.selecting_categories  # remains selecting





# ==============================================================================
# 4. FORMATTERS: CARDS BOX LAYOUT, RECEIPTS, UNICODE PROGRESS BARS
# ==============================================================================

class TestFormattersEmpirical:
    """Rigorous empirical testing of visual formatting utilities."""

    def test_card_black_box_layout_masked_and_unmasked(self):
        """Verify card box drawing, PAN masking, CVC visibility, and status formatting."""
        from app.bot.formatters.cards import format_card_view

        card_data = {
            "card_number": "2200701234567890",
            "cvv": "789",
            "expiry_date": "10/30",
            "full_name": "Ivan Ivanov",
            "is_frozen": False,
        }

        # Masked view
        view_masked = format_card_view(card_data, unmasked=False, balance=Decimal("15000.50"))
        assert "2200 70•• •••• 7890" in view_masked
        assert "CVC: •••" in view_masked
        assert "🟢 Активна" in view_masked
        assert "15000.50 ₽" in view_masked
        assert "┌──────────────────────────────────────┐" in view_masked
        assert "└──────────────────────────────────────┘" in view_masked

        # Unmasked view
        view_unmasked = format_card_view(card_data, unmasked=True, balance=Decimal("15000.50"))
        assert "2200 7012 3456 7890" in view_unmasked
        assert "CVC: 789" in view_unmasked

        # Frozen view
        card_data["is_frozen"] = True
        view_frozen = format_card_view(card_data, unmasked=False)
        assert "❄️ Заморожена" in view_frozen

    def test_digital_receipt_formatting(self):
        """Verify receipt formatting contains fiscal code, auth code, amounts, and dates."""
        from app.bot.formatters.receipts import format_receipt_message

        tx = Transaction(
            id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            amount=Decimal("1250.00"),
            fee=Decimal("0.00"),
            category="Перевод",
            description="Возврат долга за обед",
            fiscal_code="ФП-1234567890",
            auth_code="AUTH-654321",
        )
        sender_user = User(full_name="Alice Smith", username="alice_s")
        recipient_user = User(full_name="Bob Jones", username="bob_j")

        receipt = format_receipt_message(
            transaction=tx,
            sender_user=sender_user,
            recipient_user=recipient_user,
            cashback_earned=Decimal("12.50"),
        )

        assert "КВИТАНЦИЯ ОБ ОПЕРАЦИИ #A1B2C3D4" in receipt
        assert "1250.00 ₽" in receipt
        assert "ФП-1234567890" in receipt
        assert "AUTH-654321" in receipt
        assert "Alice Smith (@alice_s)" in receipt
        assert "Bob Jones (@bob_j)" in receipt
        assert "Возврат долга за обед" in receipt
        assert "+12.50 ₽" in receipt

    @pytest.mark.parametrize(
        "percentage,expected_bar",
        [
            (0.0, "░░░░░░░░░░"),
            (10.0, "█░░░░░░░░░"),
            (20.0, "██░░░░░░░░"),
            (50.0, "█████░░░░░"),
            (70.0, "███████░░░"),
            (100.0, "██████████"),
            (-20.0, "░░░░░░░░░░"),  # Clamped to 0
            (150.0, "██████████"),  # Clamped to 100
        ],
    )
    def test_unicode_progress_bar_blocks(self, percentage: float, expected_bar: str):
        """Verify exact 10-block Unicode visual progress bars rendering."""
        assert ReceiptService.format_progress_bar(percentage, total_blocks=10) == expected_bar

    @pytest.mark.asyncio
    async def test_expense_analytics_report_generation(self, async_session, sample_user):
        """Verify visual monthly expense analytics generation with progress bars."""
        from app.bot.formatters.analytics import format_expense_analytics

        # 1. Empty month report
        empty_report = await format_expense_analytics(async_session, sample_user.id)
        assert "расходов еще не было" in empty_report

        # 2. Add expense transactions
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        cur_date = datetime.datetime.now()

        tx1 = Transaction(
            id="tx-food-1",
            tx_type="TRANSFER",
            amount=Decimal("3000.00"),
            category="Шаурма и фастфуд",
            description="Обед",
            sender_account_id=main_acc.id,
            created_at=cur_date,
            fiscal_code="ФП-1111111111",
            auth_code="AUTH-111111",
        )
        tx2 = Transaction(
            id="tx-taxi-1",
            tx_type="TRANSFER",
            amount=Decimal("1000.00"),
            category="Такси",
            description="Поездка",
            sender_account_id=main_acc.id,
            created_at=cur_date,
            fiscal_code="ФП-2222222222",
            auth_code="AUTH-222222",
        )
        async_session.add_all([tx1, tx2])
        await async_session.commit()

        report = await format_expense_analytics(async_session, sample_user.id)
        # Total: 4,000.00 ₽. Food: 75% (████████░░), Taxi: 25% (███░░░░░░░ or ██░░░░░░░░)
        assert "4000.00 ₽" in report
        assert "Шаурма и фастфуд: 3000.00 ₽ (75.0%)" in report
        assert "Такси: 1000.00 ₽ (25.0%)" in report
        assert "Комментарий Олега:" not in report


# ==============================================================================
# 5. ADVERSARIAL STRESS & CONCURRENCY SECURITY
# ==============================================================================

class TestAdversarialStressAndSecurity:
    """Adversarial stress-testing of concurrency, race conditions, and malformed inputs."""

    @pytest.mark.asyncio
    async def test_concurrent_stock_buys_overdraft_stress(self, test_engine, sample_user):
        """Stress-test two concurrent stock buy operations when balance suffices for only one."""
        from sqlalchemy.ext.asyncio import async_sessionmaker

        session_factory = async_sessionmaker(
            bind=test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )

        # Set user balance to 300.00 ₽. Stock costs 200.00 ₽ + 0.60 ₽ fee = 200.60 ₽.
        async with session_factory() as setup_session:
            main_acc = await BankingService.get_user_main_account(setup_session, sample_user.id)
            main_acc.balance = Decimal("300.00")
            stock = await InvestmentService.get_stock(setup_session, "SHAW")
            stock.current_price = Decimal("200.00")
            await setup_session.commit()

        # Execute 2 concurrent buy tasks
        async def _attempt_buy():
            async with session_factory() as sess:
                try:
                    res = await InvestmentService.buy_stock(sess, sample_user.id, "SHAW", 1)
                    await sess.commit()
                    return ("SUCCESS", res)
                except InsufficientFundsError:
                    await sess.rollback()
                    return ("INSUFFICIENT_FUNDS", None)
                except Exception as e:
                    await sess.rollback()
                    return (type(e).__name__, None)

        results = await asyncio.gather(_attempt_buy(), _attempt_buy())
        statuses = [r[0] for r in results]

        # Exactly 1 buy must succeed and 1 must fail due to overdraft protection
        assert statuses.count("SUCCESS") == 1, f"Expected 1 SUCCESS, got: {statuses}"
        assert statuses.count("INSUFFICIENT_FUNDS") == 1, f"Expected 1 INSUFFICIENT_FUNDS, got: {statuses}"

        # Verify final balance is non-negative and correctly debited
        async with session_factory() as check_session:
            final_acc = await BankingService.get_user_main_account(check_session, sample_user.id)
            assert final_acc.balance == Decimal("300.00") - Decimal("200.60")
            assert final_acc.balance >= Decimal("0.00")

    @pytest.mark.asyncio
    async def test_concurrent_cashback_payout_double_tap_stress(self, test_engine, sample_user):
        """Stress-test two concurrent 1-click bonus payout requests (CAS anti-duplication guard)."""
        from sqlalchemy.ext.asyncio import async_sessionmaker

        session_factory = async_sessionmaker(
            bind=test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )

        async with session_factory() as setup_session:
            u_res = await setup_session.execute(select(User).where(User.id == sample_user.id))
            u = u_res.scalars().first()
            u.cashback_balance = Decimal("500.00")
            await setup_session.commit()

        async def _attempt_payout():
            async with session_factory() as sess:
                try:
                    amt = await CashbackService.payout_cashback(sess, sample_user.id)
                    await sess.commit()
                    return ("SUCCESS", amt)
                except ValueError:
                    await sess.rollback()
                    return ("VALUE_ERROR", None)
                except Exception as e:
                    await sess.rollback()
                    return (type(e).__name__, None)

        results = await asyncio.gather(_attempt_payout(), _attempt_payout())
        statuses = [r[0] for r in results]

        # Exactly 1 payout succeeds, the other fails without double crediting
        assert statuses.count("SUCCESS") == 1, f"Expected 1 SUCCESS, got: {statuses}"
        assert statuses.count("VALUE_ERROR") == 1, f"Expected 1 VALUE_ERROR, got: {statuses}"

        async with session_factory() as check_session:
            u_check = (await check_session.execute(select(User).where(User.id == sample_user.id))).scalars().first()
            assert u_check.cashback_balance == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_router_forged_or_malformed_callback_data(
        self, callback_factory, fsm_context_factory, async_session, sample_user
    ):
        """Stress-test router handlers with forged or corrupted callback query payloads."""
        from app.bot.routers.cashback import handle_cashback_payout, handle_confirm_categories
        from app.bot.routers.invest import handle_execute_trade, handle_view_stock
        from app.bot.states.states import CashbackStates

        # 1. Invest execute trade with unknown stock ticker
        cb_unknown = callback_factory("invest:confirm:BUY:NONEXISTENT:5", user_id=sample_user.telegram_id)
        state_inv = fsm_context_factory(sample_user.telegram_id)
        await handle_execute_trade(cb_unknown, session=async_session, user=sample_user, state=state_inv)
        # Should gracefully answer alert and return to menu without unhandled exception

        # 2. Invest view stock with non-existent ticker
        cb_view_non = callback_factory("stock:view:UNKNOWN99", user_id=sample_user.telegram_id)
        await handle_view_stock(cb_view_non, session=async_session, user=sample_user)

        # 3. Cashback payout with 0 balance
        sample_user.cashback_balance = Decimal("0.00")
        await async_session.commit()
        cb_payout_zero = callback_factory("cb:payout", user_id=sample_user.telegram_id)
        await handle_cashback_payout(cb_payout_zero, session=async_session, user=sample_user)

        # 4. Cashback confirm with empty selection state
        state_cb = fsm_context_factory(sample_user.telegram_id)
        await state_cb.set_state(CashbackStates.selecting_categories)
        await state_cb.update_data(selected_ids=[])
        cb_confirm_empty = callback_factory("cb:confirm", user_id=sample_user.telegram_id)
        await handle_confirm_categories(cb_confirm_empty, session=async_session, user=sample_user, state=state_cb)
        assert await state_cb.get_state() == CashbackStates.selecting_categories
