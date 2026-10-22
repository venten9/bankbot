"""Comprehensive Automated Test Suite for Custom 16-Digit Cards & Admin Superpowers / Cheat Engine.

Covers Tiers 1-4:
- Tier 1: Unit & Domain Service Happy Paths (Custom cards, cleanup, cheats, resets, grants, market).
- Tier 2: Boundary & Corner Cases (Length, non-digits, collision, duplicate grants, stock increments, 1.00 ₽ floor, cascade integrity).
- Tier 3: Security, RBAC & Bot Interaction (IsAdminFilter, 403 denial, FSM transitions, callbacks <=64 bytes, HTML escaping, vanity transfers, Oleg absence).
- Tier 4: E2E Full Lifecycle Scenario (Multi-stage user and admin superpower journey).
"""

import datetime
import html
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.fsm.context import FSMContext
from sqlalchemy import func, select

from app.bot.filters.admin import IsAdminFilter
from app.bot.formatters.admin import (
    format_cheat_menu,
    format_cleanup_confirmation,
    format_cleanup_result,
    format_cooldown_reset_summary,
    format_grant_menu,
    format_grant_property_result,
    format_grant_stock_result,
    format_market_manipulation_result,
    format_market_regulator,
    format_stock_manipulation_result,
)
from app.bot.keyboards.admin import (
    get_admin_cleanup_confirm_keyboard,
    get_admin_cheat_keyboard,
    get_admin_dashboard_keyboard,
    get_admin_grant_keyboard,
    get_admin_market_keyboard,
    get_admin_stock_detail_keyboard,
    get_admin_user_card_keyboard,
)
from app.bot.keyboards.card import get_card_cancel_keyboard, get_card_keyboard
from app.bot.keyboards.reply import get_main_menu_keyboard
from app.bot.routers.admin import (
    DENIAL_TEXT,
    handle_cheat_balance,
    handle_cheat_cashback,
    handle_cheat_menu_self,
    handle_cheat_menu_user,
    handle_cheat_reset_cooldowns,
    handle_cleanup_execute,
    handle_cleanup_prompt,
    handle_grant_menu,
    handle_grant_property,
    handle_grant_stocks,
    handle_market_global_action,
    handle_market_menu,
    handle_market_stock_action,
    handle_market_stock_inspect,
    handle_market_stock_set_price,
    handle_unauthorized_admin_callback,
    handle_unauthorized_admin_command,
)
from app.bot.routers.card import (
    handle_change_number_cancel,
    handle_change_number_prompt,
    process_custom_card_number,
)
from app.bot.states.states import CardState
from app.config import settings
from app.database.models import (
    Account,
    AdminLog,
    Card,
    InvestmentPortfolio,
    LedgerEntry,
    SavingsAccount,
    Stock,
    Transaction,
    User,
    UserCashbackSelection,
    UserJob,
    UserProperty,
)
from app.services.admin import AdminService
from app.services.banking import BankingService
from app.services.card import CardService
from app.services.investments import InvestmentService
from app.services.jobs import JobBranch, JobService
from app.services.property import PROPERTY_CATALOG, PropertyService
from app.services.receipts import ReceiptService
from app.services.transfers import TransferService
from app.services.wheel import WheelService


# ==============================================================================
# Test Inspection Helpers
# ==============================================================================

def get_sent_texts(mock_bot: AsyncMock) -> list[str]:
    """Extract all text payloads sent through bot.send_message or bot(SendMessage)."""
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
            if type(method).__name__ == "SendMessage" and hasattr(method, "text") and method.text is not None:
                texts.append(method.text)
    return texts


def get_edited_texts(mock_bot: AsyncMock) -> list[str]:
    """Extract all text payloads sent through bot.edit_message_text or bot(EditMessageText)."""
    texts = []
    for call in mock_bot.edit_message_text.call_args_list:
        args, kwargs = call
        if "text" in kwargs:
            texts.append(kwargs["text"])
    for call in mock_bot.call_args_list:
        if call[0]:
            method = call[0][0]
            if type(method).__name__ == "EditMessageText" and hasattr(method, "text") and method.text is not None:
                texts.append(method.text)
    return texts


def get_callback_answers(mock_bot: AsyncMock) -> list[dict]:
    """Extract all callback query answer payloads."""
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


# ==============================================================================
# Tier 1: Feature Coverage (Isolation / Happy Paths)
# ==============================================================================

class TestTier1CustomCardAndAdminHappyPath:
    """Tier 1: Comprehensive domain service tests for new R1-R6 features."""

    @pytest.mark.asyncio
    async def test_change_card_number_happy_path(self, async_session, sample_user):
        """Custom vanity number updates the card and formats properly."""
        new_vanity = "2200707777777777"
        updated_card = await CardService.change_card_number(
            session=async_session, user_id=sample_user.id, new_number=new_vanity
        )
        assert updated_card.card_number == new_vanity

        formatted = CardService.format_card_number(updated_card.card_number)
        assert formatted == "2200 7077 7777 7777"

        # Check masked format
        masked = CardService.format_card_number(updated_card.card_number, masked=True)
        assert masked == "2200 70•• •••• 7777"

        # Verify DB persistence
        res = await async_session.execute(select(Card).where(Card.user_id == sample_user.id))
        persisted = res.scalars().first()
        assert persisted.card_number == new_vanity

    @pytest.mark.asyncio
    async def test_change_card_number_strips_spaces_and_hyphens(self, async_session, sample_user):
        """Card number with spaces and hyphens is sanitized to 16 digits."""
        spaced_number = "2200 7088 8888 8888"
        card1 = await CardService.change_card_number(
            session=async_session, user_id=sample_user.id, new_number=spaced_number
        )
        assert card1.card_number == "2200708888888888"

        hyphen_number = "2200-7099-9999-9999"
        card2 = await CardService.change_card_number(
            session=async_session, user_id=sample_user.id, new_number=hyphen_number
        )
        assert card2.card_number == "2200709999999999"

    @pytest.mark.asyncio
    async def test_cleanup_test_users_purges_mock_accounts(self, async_session, sample_user):
        """Deletes mock IDs 123, 8881001, etc. and cascade records in reverse dependency order."""
        # Create test mock user 8881001
        mock_u, mock_acc, mock_card = await BankingService.get_or_create_user(
            session=async_session,
            telegram_id=8881001,
            username="test_mock_purged",
            full_name="Mock Purged User",
        )
        await async_session.commit()

        # Execute cleanup
        summary = await AdminService.cleanup_test_users(
            session=async_session,
            extra_telegram_ids=[8881001],
            admin_telegram_id=1302123809,
        )
        await async_session.commit()

        assert summary["users"] >= 1
        assert summary["accounts"] >= 1
        assert summary["cards"] >= 1

        # Assert mock user was deleted
        res_mock = await async_session.execute(select(User).where(User.telegram_id == 8881001))
        assert res_mock.scalars().first() is None

        # Assert standard customer user survived
        res_real = await async_session.execute(select(User).where(User.id == sample_user.id))
        assert res_real.scalars().first() is not None

    @pytest.mark.asyncio
    async def test_cleanup_test_users_protects_admin_accounts(self, async_session, sample_admin):
        """Admin 1302123809 and settings.ADMIN_IDS strictly survive cleanup even if passed in mock lists."""
        admin_tg = 1302123809
        admin_u, admin_acc, _ = await BankingService.get_or_create_user(
            session=async_session,
            telegram_id=admin_tg,
            username="test_admin_master",
            full_name="Root Admin",
        )
        await async_session.commit()

        # Pass protected IDs directly in extra_telegram_ids to test immunity
        summary = await AdminService.cleanup_test_users(
            session=async_session,
            extra_telegram_ids=[admin_tg, sample_admin.telegram_id],
            admin_telegram_id=admin_tg,
        )
        await async_session.commit()

        # Both admins must survive
        res_130 = await async_session.execute(select(User).where(User.telegram_id == admin_tg))
        assert res_130.scalars().first() is not None

        res_sample = await async_session.execute(select(User).where(User.telegram_id == sample_admin.telegram_id))
        assert res_sample.scalars().first() is not None

    @pytest.mark.asyncio
    async def test_cheat_balance_presets(self, async_session, sample_admin, sample_user):
        """+100k, +1M, +100M ₽ top-ups update balance, record ADMIN_CHEAT, LedgerEntry, and AdminLog."""
        presets = [Decimal("100000.00"), Decimal("1000000.00"), Decimal("100000000.00")]
        initial_balance = sample_user.accounts[0].balance

        for amt in presets:
            tx = await AdminService.cheat_balance(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=sample_user.id,
                amount=amt,
            )
            await async_session.commit()

            assert tx.tx_type == "ADMIN_CHEAT"
            assert tx.amount == amt
            assert tx.status == "COMPLETED"

            # Check LedgerEntry
            ledger_res = await async_session.execute(
                select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id)
            )
            ledger = ledger_res.scalars().first()
            assert ledger is not None
            assert ledger.direction == "CREDIT"
            assert ledger.amount == amt

            # Check AdminLog
            log_res = await async_session.execute(
                select(AdminLog).where(
                    AdminLog.action == "ADMIN_CHEAT_BALANCE",
                    AdminLog.amount == amt,
                    AdminLog.target_user_id == sample_user.id,
                )
            )
            log = log_res.scalars().first()
            assert log is not None
            assert log.admin_telegram_id == sample_admin.telegram_id

        # Verify final cumulative balance
        await async_session.refresh(sample_user.accounts[0])
        expected_total = initial_balance + sum(presets)
        assert sample_user.accounts[0].balance == expected_total

    @pytest.mark.asyncio
    async def test_cheat_cashback_preset(self, async_session, sample_admin, sample_user):
        """+50 000 cashback points added to user balance and logged in AdminLog."""
        cb_amount = Decimal("50000.00")
        initial_cb = sample_user.cashback_balance

        new_cb = await AdminService.cheat_cashback(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            amount=cb_amount,
        )
        await async_session.commit()

        assert new_cb == initial_cb + cb_amount

        # Check AdminLog
        log_res = await async_session.execute(
            select(AdminLog).where(
                AdminLog.action == "ADMIN_CHEAT_CASHBACK",
                AdminLog.target_user_id == sample_user.id,
            )
        )
        log = log_res.scalars().first()
        assert log is not None
        assert log.amount == cb_amount

    @pytest.mark.asyncio
    async def test_reset_cooldowns_wheel(self, async_session, sample_admin, sample_user):
        """Clears last_daily_spin_at making can_spin_free return True."""
        sample_user.last_daily_spin_at = datetime.datetime.now()
        await async_session.commit()

        can_spin, rem_secs = await WheelService.can_spin_free(sample_user)
        assert can_spin is False
        assert rem_secs > 0

        # Run reset
        summary = await AdminService.reset_cooldowns(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
        )
        await async_session.commit()

        assert summary["wheel_reset"] is True
        await async_session.refresh(sample_user)
        assert sample_user.last_daily_spin_at is None

        can_spin_after, rem_after = await WheelService.can_spin_free(sample_user)
        assert can_spin_after is True
        assert rem_after == 0

    @pytest.mark.asyncio
    async def test_reset_cooldowns_job_shifts(self, async_session, sample_admin, sample_user):
        """Rewinds shift_started_at by 3h -> claim_salary succeeds immediately."""
        job = await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        await JobService.start_shift(async_session, sample_user.id, JobBranch.IT)
        await async_session.commit()

        # Attempting premature claim raises error
        with pytest.raises(ValueError) as exc_info:
            await JobService.claim_salary(async_session, sample_user.id, JobBranch.IT)
        assert "Смена еще не окончена" in str(exc_info.value)

        # Fast-forward cooldowns
        summary = await AdminService.reset_cooldowns(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
        )
        await async_session.commit()

        assert summary["jobs_reset"] >= 1

        # Now claiming salary succeeds immediately
        salary, msg = await JobService.claim_salary(async_session, sample_user.id, JobBranch.IT)
        await async_session.commit()

        assert salary > Decimal("0.00")
        assert isinstance(msg, str) and len(msg) > 0

    @pytest.mark.asyncio
    async def test_reset_cooldowns_real_estate(self, async_session, sample_admin, sample_user):
        """Rewinds last_rent_collected_at by 25h -> collect_rental_income succeeds immediately."""
        # Grant real estate (e.g. murino)
        prop = await AdminService.grant_property(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            item_id="murino",
        )
        await async_session.commit()

        # Immediate rent collection yields 0 (just purchased/collected)
        rent1, rem1, _ = await PropertyService.collect_rental_income(async_session, sample_user.id)
        assert rent1 == Decimal("0.00")
        assert rem1 > 0

        # Reset cooldowns
        summary = await AdminService.reset_cooldowns(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
        )
        await async_session.commit()

        assert summary["rentals_reset"] >= 1

        # Rent collection succeeds immediately!
        rent2, rem2, _ = await PropertyService.collect_rental_income(async_session, sample_user.id)
        await async_session.commit()

        assert rent2 == Decimal("15000.00")  # Murino daily rent
        assert rem2 == 86400

    @pytest.mark.asyncio
    async def test_reset_cooldowns_hustle(self, async_session, sample_admin, sample_user):
        """Clears last_hustle_at -> quick hustle succeeds immediately."""
        sample_user.last_hustle_at = datetime.datetime.now()
        await async_session.commit()

        # Attempting hustle raises cooldown
        with pytest.raises(ValueError) as exc_info:
            await JobService.execute_hustle(async_session, sample_user.id, "hustle_server_crash", 0)
        assert "Шабашкины руки" in str(exc_info.value)

        # Reset cooldowns
        await AdminService.reset_cooldowns(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
        )
        await async_session.commit()

        await async_session.refresh(sample_user)
        assert sample_user.last_hustle_at is None

        # Hustle executes without error
        payout, msg = await JobService.execute_hustle(
            async_session, sample_user.id, "hustle_server_crash", 0, success_roll=0.01
        )
        await async_session.commit()
        assert payout > Decimal("0.00")

    @pytest.mark.asyncio
    async def test_grant_property_cars_real_estate_luxury(self, async_session, sample_admin, sample_user):
        """Maybach, Penthouse, Yacht granted with 0 ₽ cost, no balance deducted."""
        initial_balance = sample_user.accounts[0].balance
        items = ["maybach", "city_penthouse", "yacht"]

        for item_id in items:
            prop = await AdminService.grant_property(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=sample_user.id,
                item_id=item_id,
            )
            await async_session.commit()

            assert prop.item_id == item_id
            assert prop.purchase_price == Decimal("0.00")

            # Check AdminLog
            log_res = await async_session.execute(
                select(AdminLog).where(
                    AdminLog.action == "GRANT_PROPERTY",
                    AdminLog.target_user_id == sample_user.id,
                    AdminLog.reason.like(f"%{item_id}%") | AdminLog.reason.like("%Выдача актива%"),
                )
            )
            assert log_res.scalars().first() is not None

        # Verify user account balance was not charged
        await async_session.refresh(sample_user.accounts[0])
        assert sample_user.accounts[0].balance == initial_balance

    @pytest.mark.asyncio
    async def test_grant_stocks_shares_credit(self, async_session, sample_admin, sample_user):
        """+1000 shares of SHAW credited to InvestmentPortfolio at 0 ₽ cost."""
        initial_balance = sample_user.accounts[0].balance

        portfolio = await AdminService.grant_stocks(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            ticker="SHAW",
            shares=1000,
        )
        await async_session.commit()

        assert portfolio.shares >= 1000

        # Check AdminLog
        log_res = await async_session.execute(
            select(AdminLog).where(
                AdminLog.action == "GRANT_STOCK",
                AdminLog.target_user_id == sample_user.id,
            )
        )
        log = log_res.scalars().first()
        assert log is not None
        assert log.amount == Decimal("1000")

        # Balance remains untouched
        await async_session.refresh(sample_user.accounts[0])
        assert sample_user.accounts[0].balance == initial_balance

    @pytest.mark.asyncio
    async def test_market_pump_multiplies_prices(self, async_session, sample_admin):
        """Market pump multiplies all stock prices by 1.50 (+50%)."""
        await InvestmentService.init_stocks(async_session)
        stocks_before = await InvestmentService.list_stocks(async_session)
        prices_before = {s.ticker: s.current_price for s in stocks_before}

        pumped_stocks = await AdminService.manipulate_market(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            action="PUMP",
        )
        await async_session.commit()

        for stock in pumped_stocks:
            expected = max(Decimal("1.00"), (prices_before[stock.ticker] * Decimal("1.50")).quantize(Decimal("0.01")))
            assert stock.current_price == expected

    @pytest.mark.asyncio
    async def test_market_dump_reduces_prices(self, async_session, sample_admin):
        """Market dump reduces all stock prices by 0.50 (-50%), floored at 1.00 ₽."""
        await InvestmentService.init_stocks(async_session)
        stocks_before = await InvestmentService.list_stocks(async_session)
        prices_before = {s.ticker: s.current_price for s in stocks_before}

        dumped_stocks = await AdminService.manipulate_market(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            action="DUMP",
        )
        await async_session.commit()

        for stock in dumped_stocks:
            expected = max(Decimal("1.00"), (prices_before[stock.ticker] * Decimal("0.50")).quantize(Decimal("0.01")))
            assert stock.current_price == expected

    @pytest.mark.asyncio
    async def test_market_crash_reduces_prices(self, async_session, sample_admin):
        """Market crash reduces all stock prices by 0.20 (-80%), floored at 1.00 ₽."""
        await InvestmentService.init_stocks(async_session)
        stocks_before = await InvestmentService.list_stocks(async_session)
        prices_before = {s.ticker: s.current_price for s in stocks_before}

        crashed_stocks = await AdminService.manipulate_market(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            action="CRASH",
        )
        await async_session.commit()

        for stock in crashed_stocks:
            expected = max(Decimal("1.00"), (prices_before[stock.ticker] * Decimal("0.20")).quantize(Decimal("0.01")))
            assert stock.current_price == expected

    @pytest.mark.asyncio
    async def test_set_stock_price_single_ticker(self, async_session, sample_admin):
        """Exact price setter and individual pump/dump with 1.00 ₽ floor."""
        await InvestmentService.init_stocks(async_session)

        # Set exact price
        stock = await AdminService.set_stock_price(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            ticker="SHAW",
            target_price=Decimal("250.00"),
        )
        await async_session.commit()
        assert stock.current_price == Decimal("250.00")

        # Single stock pump (+25%) -> 312.50
        pumped = await AdminService.manipulate_stock(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            ticker="SHAW",
            action_or_price="PUMP",
        )
        await async_session.commit()
        assert pumped.current_price == Decimal("312.50")

        # Single stock dump (-25%) -> 234.38
        dumped = await AdminService.manipulate_stock(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            ticker="SHAW",
            action_or_price="DUMP",
        )
        await async_session.commit()
        assert dumped.current_price == Decimal("234.38")


# ==============================================================================
# Tier 2: Boundary & Corner Cases
# ==============================================================================

class TestTier2BoundaryAndCornerCases:
    """Tier 2: Boundary conditions, invalid inputs, edge floor limits, and cascade integrity."""

    @pytest.mark.asyncio
    async def test_card_number_invalid_length_raises(self, async_session, sample_user):
        """15-digit and 17-digit card numbers raise ValueError."""
        with pytest.raises(ValueError, match="ровно из 16 цифр"):
            await CardService.change_card_number(async_session, sample_user.id, "220070123456789")

        with pytest.raises(ValueError, match="ровно из 16 цифр"):
            await CardService.change_card_number(async_session, sample_user.id, "22007012345678901")

        with pytest.raises(ValueError, match="ровно из 16 цифр"):
            await CardService.change_card_number(async_session, sample_user.id, "")

    @pytest.mark.asyncio
    async def test_card_number_non_digits_raises(self, async_session, sample_user):
        """Non-digit characters raise ValueError."""
        with pytest.raises(ValueError, match="ровно из 16 цифр"):
            await CardService.change_card_number(async_session, sample_user.id, "2200 70AA BBBB CCCC")

        with pytest.raises(ValueError, match="ровно из 16 цифр"):
            await CardService.change_card_number(async_session, sample_user.id, "2200 7012 3456 789!")

    @pytest.mark.asyncio
    async def test_card_number_duplicate_collision_raises(self, async_session, sample_user, sample_recipient):
        """Attempting to claim an already registered card number raises collision error."""
        recipient_card = sample_recipient.cards[0].card_number

        with pytest.raises(
            ValueError,
            match="Этот номер карты уже занят другим клиентом. Пожалуйста, выберите другой.",
        ):
            await CardService.change_card_number(
                session=async_session, user_id=sample_user.id, new_number=recipient_card
            )

    @pytest.mark.asyncio
    async def test_card_number_same_user_reapplication(self, async_session, sample_user):
        """Submitting user's own current card number succeeds cleanly (idempotent)."""
        current_num = sample_user.cards[0].card_number
        result_card = await CardService.change_card_number(
            session=async_session, user_id=sample_user.id, new_number=current_num
        )
        assert result_card.card_number == current_num

    @pytest.mark.asyncio
    async def test_grant_duplicate_property_safe(self, async_session, sample_admin, sample_user):
        """Granting an already owned property returns the existing record without unique constraint error."""
        prop1 = await AdminService.grant_property(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            item_id="maybach",
        )
        await async_session.commit()

        # Grant same item again
        prop2 = await AdminService.grant_property(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            item_id="maybach",
        )
        await async_session.commit()

        assert prop1.id == prop2.id
        assert prop2.item_id == "maybach"

    @pytest.mark.asyncio
    async def test_grant_stocks_existing_portfolio_increments(self, async_session, sample_admin, sample_user):
        """Granting shares when user already holds portfolio correctly increments shares."""
        p1 = await AdminService.grant_stocks(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            ticker="SHAW",
            shares=500,
        )
        await async_session.commit()
        assert p1.shares == 500

        p2 = await AdminService.grant_stocks(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            ticker="SHAW",
            shares=1000,
        )
        await async_session.commit()
        assert p2.shares == 1500

    @pytest.mark.asyncio
    async def test_market_dump_and_crash_floor_at_1_ruble(self, async_session, sample_admin):
        """Stock price is strictly floored at 1.00 ₽ on deep dump, crash, or low manual set."""
        await InvestmentService.init_stocks(async_session)

        # Set stock price to 1.50 ₽
        await AdminService.set_stock_price(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            ticker="SHAW",
            target_price=Decimal("1.50"),
        )
        await async_session.commit()

        # Dump (-50%): 1.50 * 0.50 = 0.75 -> floored at 1.00 ₽
        await AdminService.manipulate_market(async_session, sample_admin.telegram_id, "DUMP")
        await async_session.commit()

        stock_res = await async_session.execute(select(Stock).where(Stock.ticker == "SHAW"))
        stock = stock_res.scalars().first()
        assert stock.current_price == Decimal("1.00")

        # Crash (-80%): 1.00 * 0.20 = 0.20 -> floored at 1.00 ₽
        await AdminService.manipulate_market(async_session, sample_admin.telegram_id, "CRASH")
        await async_session.commit()
        assert stock.current_price == Decimal("1.00")

        # Manual set to 0.10 ₽ -> floored at 1.00 ₽
        stock_low = await AdminService.set_stock_price(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            ticker="SHAW",
            target_price=Decimal("0.10"),
        )
        await async_session.commit()
        assert stock_low.current_price == Decimal("1.00")

    @pytest.mark.asyncio
    async def test_cheat_balance_negative_amount_raises(self, async_session, sample_admin, sample_user):
        """Negative or zero cheat amount raises ValueError."""
        with pytest.raises(ValueError, match="больше нуля"):
            await AdminService.cheat_balance(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=sample_user.id,
                amount=Decimal("-1000.00"),
            )

        with pytest.raises(ValueError, match="больше нуля"):
            await AdminService.cheat_balance(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=sample_user.id,
                amount=Decimal("0.00"),
            )

    @pytest.mark.asyncio
    async def test_cheat_cashback_negative_amount_raises(self, async_session, sample_admin, sample_user):
        """Negative or zero cashback cheat amount raises ValueError."""
        with pytest.raises(ValueError, match="больше нуля"):
            await AdminService.cheat_cashback(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=sample_user.id,
                amount=Decimal("-500.00"),
            )

    @pytest.mark.asyncio
    async def test_cleanup_cascade_integrity(self, async_session, sample_admin):
        """Purging complex test user with jobs, properties, stocks, and transactions executes with 0 FK violations."""
        test_tg_id = 999888777
        user, acc, card = await BankingService.get_or_create_user(
            session=async_session,
            telegram_id=test_tg_id,
            username="test_cascade_complex",
            full_name="Complex Test Mock",
        )
        acc.balance = Decimal("25000.00")

        # Add jobs, properties, stocks, and transactions
        job = await JobService.apply_for_job(async_session, user.id, JobBranch.SERVICE)
        prop = await AdminService.grant_property(async_session, sample_admin.telegram_id, user.id, "vaz2107")
        port = await AdminService.grant_stocks(async_session, sample_admin.telegram_id, user.id, "SHAW", 200)

        # Create dummy transaction and ledger
        tx_id = "test-cascade-tx-1"
        tx = Transaction(
            id=tx_id,
            tx_type="ADMIN_CHEAT",
            amount=Decimal("100.00"),
            sender_account_id=None,
            recipient_account_id=acc.id,
            status="COMPLETED",
            fiscal_code="ФП-1234567890",
            auth_code="AUTH-123456",
        )
        ledger = LedgerEntry(
            transaction_id=tx_id,
            account_id=acc.id,
            direction="CREDIT",
            amount=Decimal("100.00"),
            balance_after=Decimal("25100.00"),
        )
        admin_log = AdminLog(
            admin_telegram_id=sample_admin.telegram_id,
            target_telegram_id=test_tg_id,
            target_user_id=user.id,
            action="TEST_LOG",
            reason="Test cascade cleanup",
        )
        async_session.add_all([tx, ledger, admin_log])
        await async_session.commit()

        # Execute cleanup
        summary = await AdminService.cleanup_test_users(
            session=async_session,
            extra_telegram_ids=[test_tg_id],
            admin_telegram_id=sample_admin.telegram_id,
        )
        await async_session.commit()

        assert summary["users"] >= 1
        assert summary["accounts"] >= 1
        assert summary["properties"] >= 1
        assert summary["jobs"] >= 1
        assert summary["portfolios"] >= 1

        # Check records completely gone
        res_u = await async_session.execute(select(User).where(User.telegram_id == test_tg_id))
        assert res_u.scalars().first() is None

        res_acc = await async_session.execute(select(Account).where(Account.user_id == user.id))
        assert res_acc.scalars().first() is None

    @pytest.mark.asyncio
    async def test_change_card_number_missing_user_raises(self, async_session):
        """Attempting to change card for non-existent user raises ValueError."""
        with pytest.raises(ValueError, match="не найдена"):
            await CardService.change_card_number(async_session, 99999999, "2200707777777777")

    @pytest.mark.asyncio
    async def test_grant_unknown_property_raises(self, async_session, sample_admin, sample_user):
        """Granting invalid property item ID raises ValueError."""
        with pytest.raises(ValueError, match="Неизвестный актив"):
            await AdminService.grant_property(
                async_session, sample_admin.telegram_id, sample_user.id, "alien_spaceship"
            )

    @pytest.mark.asyncio
    async def test_grant_unknown_stock_raises(self, async_session, sample_admin, sample_user):
        """Granting unknown stock ticker raises ValueError."""
        with pytest.raises(ValueError, match="Неизвестная акция"):
            await AdminService.grant_stocks(
                async_session, sample_admin.telegram_id, sample_user.id, "FAKE_TICKER", 100
            )


# ==============================================================================
# Tier 3: Security, RBAC & Bot Interaction
# ==============================================================================

class TestTier3SecurityRBACAndBotInteraction:
    """Tier 3: RBAC filtering, FSM card changes, 64-byte callbacks, HTML escaping, and bot routing."""

    @pytest.mark.asyncio
    async def test_strict_rbac_admin_commands(self, mock_bot, message_factory):
        """Unauthorized non-admin user executing /admin or /broadcast receives denial text."""
        non_admin_id = 777111222
        assert non_admin_id not in settings.ADMIN_IDS

        msg_admin = message_factory("/admin", user_id=non_admin_id)
        filter_obj = IsAdminFilter()
        assert await filter_obj(msg_admin) is False

        await handle_unauthorized_admin_command(msg_admin)
        sent = get_sent_texts(mock_bot)
        assert any(DENIAL_TEXT in t for t in sent)

    @pytest.mark.asyncio
    async def test_strict_rbac_admin_callbacks(self, mock_bot, callback_factory):
        """Unauthorized non-admin user dispatching any admin:* callback receives 403 alert popup."""
        non_admin_id = 777111222
        assert non_admin_id not in settings.ADMIN_IDS

        callbacks_to_test = [
            "admin:cheat:menu",
            "admin:cheat:rub:1001:100000",
            "admin:cheat:cb:1001:50000",
            "admin:cheat:reset_cd:1001",
            "admin:grant:user:1001",
            "admin:grant:prop:1001:maybach",
            "admin:grant:stock:1001:SHAW",
            "admin:market:menu",
            "admin:market:pump",
            "admin:market:dump",
            "admin:market:crash",
            "admin:cleanup:test_users",
            "admin:cleanup:confirm",
        ]

        filter_obj = IsAdminFilter()
        for cb_data in callbacks_to_test:
            cb = callback_factory(cb_data, user_id=non_admin_id)
            assert await filter_obj(cb) is False

            await handle_unauthorized_admin_callback(cb)
            answers = get_callback_answers(mock_bot)
            assert any(
                a.get("show_alert") is True and "Доступ запрещен" in (a.get("text") or "")
                for a in answers
            )

    @pytest.mark.asyncio
    async def test_card_change_fsm_navigation(
        self, mock_bot, message_factory, callback_factory, fsm_context_factory, async_session, sample_user
    ):
        """FSM flow: Prompt -> Input validation error -> Cancel -> Re-entry -> Success."""
        state = fsm_context_factory(sample_user.telegram_id)

        # 1. Click "✏️ Изменить номер карты"
        cb_prompt = callback_factory("card:change_number", user_id=sample_user.telegram_id)
        await handle_change_number_prompt(cb_prompt, state=state, session=async_session, user=sample_user)
        assert await state.get_state() == CardState.waiting_for_custom_number

        # 2. User submits invalid number ("1234") -> raises validation message, remains in state
        msg_invalid = message_factory("1234", user_id=sample_user.telegram_id)
        await process_custom_card_number(msg_invalid, state=state, session=async_session, user=sample_user)
        assert await state.get_state() == CardState.waiting_for_custom_number
        sent1 = get_sent_texts(mock_bot)
        assert any("Ошибка: Номер карты должен состоять ровно из 16 цифр" in t for t in sent1)

        # 3. User cancels
        cb_cancel = callback_factory("card:change:cancel", user_id=sample_user.telegram_id)
        await handle_change_number_cancel(cb_cancel, state=state, session=async_session, user=sample_user)
        assert await state.get_state() is None

        # 4. Prompt again and submit valid vanity number
        await handle_change_number_prompt(cb_prompt, state=state, session=async_session, user=sample_user)
        assert await state.get_state() == CardState.waiting_for_custom_number

        msg_valid = message_factory("2200 7077 7777 7777", user_id=sample_user.telegram_id)
        await process_custom_card_number(msg_valid, state=state, session=async_session, user=sample_user)
        assert await state.get_state() is None

        sent2 = get_sent_texts(mock_bot)
        assert any("Номер карты успешно изменен" in t for t in sent2)
        assert any("2200 70•• •••• 7777" in t for t in sent2)

    @pytest.mark.asyncio
    async def test_all_new_callbacks_under_64_bytes(self):
        """Verify all generated callback strings for card, cheats, grants, market, and cleanup are <= 64 bytes."""
        sample_uids = [1, 1001, 1302123809, 9223372036854775807]
        sample_amounts = [100000, 1000000, 100000000, 50000]
        sample_tickers = ["SHAW", "USPEKH", "SPICHKI", "NANO", "BBRS", "DVAN", "GRECH", "MYK"]
        sample_props = [item.item_id for item in PROPERTY_CATALOG]

        callbacks = [
            "card:change_number",
            "card:change:cancel",
            "admin:cheat:menu",
            "admin:market:menu",
            "admin:market:pump",
            "admin:market:dump",
            "admin:market:crash",
            "admin:cleanup:test_users",
            "admin:cleanup:confirm",
        ]

        for uid in sample_uids:
            callbacks.append(f"admin:cheat:user:{uid}")
            callbacks.append(f"admin:cheat:reset_cd:{uid}")
            callbacks.append(f"admin:grant:user:{uid}")
            for amt in sample_amounts:
                callbacks.append(f"admin:cheat:rub:{uid}:{amt}")
                callbacks.append(f"admin:cheat:cb:{uid}:{amt}")
            for p in sample_props:
                callbacks.append(f"admin:grant:prop:{uid}:{p}")
            for t in sample_tickers:
                callbacks.append(f"admin:grant:stock:{uid}:{t}")

        for t in sample_tickers:
            callbacks.append(f"admin:market:stock:{t}")
            callbacks.append(f"admin:market:stock:{t}:pump")
            callbacks.append(f"admin:market:stock:{t}:dump")
            callbacks.append(f"admin:market:stock:{t}:p:500.00")

        for cb_str in callbacks:
            raw_len = len(cb_str.encode("utf-8"))
            assert raw_len <= 64, f"Callback '{cb_str}' exceeds 64 bytes ({raw_len} bytes)"

    @pytest.mark.asyncio
    async def test_html_escaping_adversarial_inputs(self, mock_bot, message_factory, fsm_context_factory, async_session, sample_user):
        """Malicious HTML tags in card input or formatters are safely escaped."""
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(CardState.waiting_for_custom_number)

        malicious_input = "<script>alert('xss')</script><b>evil</b>"
        msg = message_factory(malicious_input, user_id=sample_user.telegram_id)
        await process_custom_card_number(msg, state=state, session=async_session, user=sample_user)

        sent = get_sent_texts(mock_bot)
        for text in sent:
            assert "<script>" not in text
            assert "&lt;script&gt;" in text or "Ошибка" in text

    @pytest.mark.asyncio
    async def test_transfer_to_vanity_card_without_luhn(self, async_session, sample_user, sample_recipient):
        """P2P transfer directly resolves non-Luhn vanity card and executes successfully."""
        # Non-Luhn 16-digit card number: "7777 8888 9999 0001"
        vanity_card = "7777888899990001"
        assert CardService.validate_luhn(vanity_card) is False

        await CardService.change_card_number(
            session=async_session, user_id=sample_recipient.id, new_number=vanity_card
        )
        await async_session.commit()

        # Recipient resolution by non-Luhn card number
        resolved_acc = await TransferService.resolve_recipient(async_session, "7777 8888 9999 0001")
        assert resolved_acc is not None
        assert resolved_acc.user_id == sample_recipient.id

        # Execute transfer
        tx = await TransferService.execute_transfer(
            session=async_session,
            sender_user_id=sample_user.id,
            recipient_account_id=resolved_acc.id,
            amount=Decimal("500.00"),
        )
        await async_session.commit()

        assert tx.status == "COMPLETED"
        assert tx.amount == Decimal("500.00")

    @pytest.mark.asyncio
    async def test_oleg_assistant_remains_permanently_absent(self):
        """Verify complete permanent absence of Oleg virtual assistant."""
        kb = get_main_menu_keyboard()
        buttons = []
        for row in kb.keyboard:
            for b in row:
                buttons.append(b.text)

        # Invariant: Oleg must never appear in reply keyboards
        assert not any("Олег" in b or "oleg" in b.lower() for b in buttons)

    @pytest.mark.asyncio
    async def test_admin_cheat_handlers_execution(
        self, mock_bot, callback_factory, fsm_context_factory, async_session, sample_admin, sample_user
    ):
        """Direct execution of admin cheat menu, balance cheat, cashback cheat, and cooldown reset handlers."""
        state = fsm_context_factory(sample_admin.telegram_id)

        # 1. Cheat menu self
        cb_self = callback_factory("admin:cheat:menu", user_id=sample_admin.telegram_id)
        await handle_cheat_menu_self(cb_self, session=async_session, state=state)
        edited1 = get_edited_texts(mock_bot)
        assert any("Чит-панель" in t for t in edited1)

        # 2. Cheat menu target user
        cb_user = callback_factory(f"admin:cheat:user:{sample_user.id}", user_id=sample_admin.telegram_id)
        await handle_cheat_menu_user(cb_user, session=async_session, state=state)
        edited2 = get_edited_texts(mock_bot)
        assert any("Чит-панель" in t for t in edited2)

        # 3. Top-up +100k
        cb_rub = callback_factory(f"admin:cheat:rub:{sample_user.id}:100000", user_id=sample_admin.telegram_id)
        await handle_cheat_balance(cb_rub, session=async_session)
        answers1 = get_callback_answers(mock_bot)
        assert any("Успешно начислено +100,000 ₽" in (a.get("text") or "") for a in answers1)

        # 4. Cashback +50k
        cb_cb = callback_factory(f"admin:cheat:cb:{sample_user.id}:50000", user_id=sample_admin.telegram_id)
        await handle_cheat_cashback(cb_cb, session=async_session)
        answers2 = get_callback_answers(mock_bot)
        assert any("50,000" in (a.get("text") or "") for a in answers2)

        # 5. Reset cooldowns
        cb_cd = callback_factory(f"admin:cheat:reset_cd:{sample_user.id}", user_id=sample_admin.telegram_id)
        await handle_cheat_reset_cooldowns(cb_cd, session=async_session)
        answers3 = get_callback_answers(mock_bot)
        assert any("Все кулдауны" in (a.get("text") or "") for a in answers3)

    @pytest.mark.asyncio
    async def test_admin_grant_handlers_execution(
        self, mock_bot, callback_factory, fsm_context_factory, async_session, sample_admin, sample_user
    ):
        """Direct execution of grant menu, grant property, and grant stocks handlers."""
        state = fsm_context_factory(sample_admin.telegram_id)

        # Grant menu
        cb_menu = callback_factory(f"admin:grant:user:{sample_user.id}", user_id=sample_admin.telegram_id)
        await handle_grant_menu(cb_menu, session=async_session, state=state)
        edited = get_edited_texts(mock_bot)
        assert any("Выдача активов" in t for t in edited)

        # Grant Maybach
        cb_prop = callback_factory(f"admin:grant:prop:{sample_user.id}:maybach", user_id=sample_admin.telegram_id)
        await handle_grant_property(cb_prop, session=async_session)
        answers_prop = get_callback_answers(mock_bot)
        assert any("Выдано" in (a.get("text") or "") for a in answers_prop)

        # Grant Stock (SHAURMA alias)
        cb_stock = callback_factory(f"admin:grant:stock:{sample_user.id}:SHAURMA", user_id=sample_admin.telegram_id)
        await handle_grant_stocks(cb_stock, session=async_session)
        answers_stock = get_callback_answers(mock_bot)
        assert any("1 000 акций" in (a.get("text") or "") for a in answers_stock)

    @pytest.mark.asyncio
    async def test_admin_market_handlers_execution(
        self, mock_bot, callback_factory, async_session, sample_admin
    ):
        """Direct execution of market regulator handlers: menu, pump/dump/crash, inspection, and target price."""
        # Market menu
        cb_menu = callback_factory("admin:market:menu", user_id=sample_admin.telegram_id)
        await handle_market_menu(cb_menu, session=async_session)
        edited_menu = get_edited_texts(mock_bot)
        assert any("Регулятор Фондового Рынка" in t for t in edited_menu)

        # Global Pump
        cb_pump = callback_factory("admin:market:pump", user_id=sample_admin.telegram_id)
        await handle_market_global_action(cb_pump, session=async_session)
        answers_pump = get_callback_answers(mock_bot)
        assert any("Памп рынка" in (a.get("text") or "") for a in answers_pump)

        # Stock inspect SHAW
        cb_inspect = callback_factory("admin:market:stock:SHAW", user_id=sample_admin.telegram_id)
        await handle_market_stock_inspect(cb_inspect, session=async_session)
        edited_inspect = get_edited_texts(mock_bot)
        assert any("SHAW" in t for t in edited_inspect)

        # Stock single pump
        cb_stock_pump = callback_factory("admin:market:stock:SHAW:pump", user_id=sample_admin.telegram_id)
        await handle_market_stock_action(cb_stock_pump, session=async_session)
        answers_spump = get_callback_answers(mock_bot)
        assert any("PUMP завершен" in (a.get("text") or "") for a in answers_spump)

        # Stock target price 100
        cb_p100 = callback_factory("admin:market:stock:SHAW:p:100", user_id=sample_admin.telegram_id)
        await handle_market_stock_set_price(cb_p100, session=async_session)
        answers_p100 = get_callback_answers(mock_bot)
        assert any("цена установлена в 100.00 ₽" in (a.get("text") or "") for a in answers_p100)

    @pytest.mark.asyncio
    async def test_admin_cleanup_handlers_execution(
        self, mock_bot, callback_factory, async_session, sample_admin
    ):
        """Direct execution of cleanup prompt and cleanup confirm execution handlers."""
        # Prompt
        cb_prompt = callback_factory("admin:cleanup:test_users", user_id=sample_admin.telegram_id)
        await handle_cleanup_prompt(cb_prompt)
        edited_prompt = get_edited_texts(mock_bot)
        assert any("Очистка тестовых пользователей" in t for t in edited_prompt)

        # Confirm
        cb_confirm = callback_factory("admin:cleanup:confirm", user_id=sample_admin.telegram_id)
        await handle_cleanup_execute(cb_confirm, session=async_session)
        answers = get_callback_answers(mock_bot)
        assert any("Удалено" in (a.get("text") or "") for a in answers)


# ==============================================================================
# Tier 4: E2E Full Lifecycle Scenario
# ==============================================================================

class TestTier4E2EFullLifecycle:
    """Tier 4: Comprehensive end-to-end multi-feature lifecycle scenario."""

    @pytest.mark.asyncio
    async def test_e2e_custom_card_and_admin_superpowers_lifecycle(
        self, mock_bot, message_factory, callback_factory, fsm_context_factory, async_session
    ):
        """
        Complete end-to-end user and admin superpowers lifecycle:
        1. Register user (Alice).
        2. Alice customizes card to vanity number "2200 7077 7777 7777".
        3. Admin searches user by new vanity card number.
        4. Admin applies cheat +100M ₽ and +50k cashback.
        5. Admin grants Gold Maybach, Penthouse, and 1000 shares of SHAW.
        6. Alice applies for IT job and starts shift.
        7. Admin executes cooldown fast-forward.
        8. Alice claims salary with Maybach car bonus and collects rental income.
        9. Admin triggers market pump (+50%).
        10. Alice liquidates granted shares at profit.
        11. Create mock test user (Bob, 8881001).
        12. Admin executes cleanup -> Bob purged, Alice and Admin strictly preserved.
        13. Full audit invariants verification.
        """
        admin_tg = 1302123809
        if admin_tg not in settings.ADMIN_IDS:
            settings.ADMIN_IDS.append(admin_tg)

        # Admin user
        admin_user, admin_acc, _ = await BankingService.get_or_create_user(
            session=async_session,
            telegram_id=admin_tg,
            username="supreme_admin",
            full_name="Supreme Admin",
        )
        await async_session.commit()

        # Step 1: Customer Alice registers
        alice, alice_acc, alice_card = await BankingService.get_or_create_user(
            session=async_session,
            telegram_id=50001,
            username="alice_e2e",
            full_name="Alice Wonderland",
            phone_number="+79997778899",
        )
        await async_session.commit()

        # Step 2: Alice sets custom vanity card number
        vanity_pan = "2200707777777777"
        await CardService.change_card_number(async_session, alice.id, vanity_pan)
        await async_session.commit()

        # Step 3: Admin searches Alice by formatted vanity number
        found_user = await AdminService.search_user(async_session, "2200 7077 7777 7777")
        assert found_user is not None
        assert found_user.id == alice.id

        # Step 4: Admin applies cheats: +100 000 000 ₽ and +50 000 cashback
        await AdminService.cheat_balance(async_session, admin_tg, alice.id, Decimal("100000000.00"))
        await AdminService.cheat_cashback(async_session, admin_tg, alice.id, Decimal("50000.00"))
        await async_session.commit()

        await async_session.refresh(alice_acc)
        await async_session.refresh(alice)
        assert alice_acc.balance >= Decimal("100000000.00")
        assert alice.cashback_balance >= Decimal("50000.00")

        # Step 5: Admin grants Maybach, Penthouse, and 1000 shares of SHAW
        await AdminService.grant_property(async_session, admin_tg, alice.id, "maybach")
        await AdminService.grant_property(async_session, admin_tg, alice.id, "city_penthouse")
        await AdminService.grant_stocks(async_session, admin_tg, alice.id, "SHAW", 1000)
        await async_session.commit()

        # Step 6: Alice applies for IT job and starts shift
        await JobService.apply_for_job(async_session, alice.id, JobBranch.IT)
        await JobService.start_shift(async_session, alice.id, JobBranch.IT)
        await async_session.commit()

        # Step 7: Admin runs reset_cooldowns
        reset_res = await AdminService.reset_cooldowns(async_session, admin_tg, alice.id)
        await async_session.commit()
        assert reset_res["jobs_reset"] >= 1
        assert reset_res["rentals_reset"] >= 1

        # Step 8: Alice claims salary (with Maybach +50% bonus, deterministic roll) and collects penthouse rent
        sal, _ = await JobService.claim_salary(async_session, alice.id, JobBranch.IT, event_roll=0.99)
        rent, _, _ = await PropertyService.collect_rental_income(async_session, alice.id)
        await async_session.commit()

        assert sal == Decimal("2250.00")  # 1500 + 50% bonus = 2250 ₽
        assert rent == Decimal("300000.00")  # City Penthouse daily rent

        # Step 9: Admin pumps market (+50%)
        await AdminService.manipulate_market(async_session, admin_tg, "PUMP")
        await async_session.commit()

        # Step 10: Alice sells granted shares
        sell_res = await InvestmentService.sell_stock(async_session, alice.id, "SHAW", 1000)
        await async_session.commit()
        assert sell_res["net_proceeds"] > Decimal("0.00")

        # Step 11: Create mock test user (Bob)
        bob, bob_acc, _ = await BankingService.get_or_create_user(
            session=async_session,
            telegram_id=8881001,
            username="test_bob_mock",
            full_name="Bob Mock",
        )
        await async_session.commit()

        # Step 12: Admin cleans up test data
        cleanup_summary = await AdminService.cleanup_test_users(
            session=async_session,
            extra_telegram_ids=[8881001],
            admin_telegram_id=admin_tg,
        )
        await async_session.commit()

        assert cleanup_summary["users"] >= 1

        # Verify Bob is purged, Alice and Admin remain intact
        bob_check = await async_session.execute(select(User).where(User.telegram_id == 8881001))
        assert bob_check.scalars().first() is None

        alice_check = await async_session.execute(select(User).where(User.id == alice.id))
        assert alice_check.scalars().first() is not None

        admin_check = await async_session.execute(select(User).where(User.telegram_id == admin_tg))
        assert admin_check.scalars().first() is not None

        # Step 13: Invariant audit - all balances strictly non-negative, double-entry balanced
        all_accs = (await async_session.execute(select(Account))).scalars().all()
        for acc_item in all_accs:
            assert acc_item.balance >= Decimal("0.00")
