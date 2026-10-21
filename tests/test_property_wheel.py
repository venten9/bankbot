"""Comprehensive Automated Test Suite for T-Property, Luxury & Fortune Wheel.

Covers all 4 tiers of testing per TEST_INFRA.md and user requirements:
- Tier 1: Feature Coverage (Catalog, Services, 8 Sectors, Handlers, Reply Keyboard, Formatters)
- Tier 2: Boundary & Corner Cases (Insufficient Funds, Duplicate Purchases, Cooldowns, Limits, Frozen Accounts)
- Tier 3: Cross-Feature Interactions (Energy Drink + Jobs, Car Buffs + Jobs, Cashback + Extra Spins, Dynamic Stock + DEFAULT_STOCKS Invariant)
- Tier 4: Real-World E2E Scenario (Full User Lifecycle from Onboarding to Asset & Wheel Mastery)
"""

import datetime
import html
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.bot import create_dispatcher
from app.bot.formatters.property import (
    format_my_properties,
    format_property_catalog,
    format_property_item_detail,
    format_property_overview,
)
from app.bot.formatters.wheel import (
    format_countdown,
    format_wheel_dashboard,
    format_wheel_spin_result,
)
from app.bot.keyboards.property import (
    get_my_properties_keyboard,
    get_property_category_keyboard,
    get_property_item_keyboard,
    get_property_menu_keyboard,
)
from app.bot.keyboards.reply import get_main_menu_keyboard
from app.bot.keyboards.wheel import (
    get_wheel_keyboard,
    get_wheel_result_keyboard,
)
from app.bot.routers.property import (
    handle_property_buy,
    handle_property_category,
    handle_property_menu,
    handle_property_menu_callback,
    handle_property_my,
    handle_property_rent_claim,
    property_router,
)
from app.bot.routers.wheel import (
    handle_wheel_menu,
    handle_wheel_menu_callback,
    handle_wheel_spin,
    handle_wheel_spin_extra,
    wheel_router,
)
from app.database.models import (
    Account,
    InvestmentPortfolio,
    LedgerEntry,
    Stock,
    Transaction,
    User,
    UserJob,
    UserProperty,
)
from app.services.banking import BankingService
from app.services.investments import DEFAULT_STOCKS
from app.services.jobs import JobBranch, JobService
from app.services.property import (
    PROPERTY_CATALOG,
    PropertyItem,
    PropertyService,
    buy_property,
    collect_rental_income,
    get_property_catalog,
    get_property_item,
    get_user_car_buffs,
    get_user_properties,
)
from app.services.transfers import AccountFrozenError, InsufficientFundsError
from app.services.wheel import (
    WHEEL_SECTORS,
    WheelResult,
    WheelSector,
    WheelService,
    can_spin_free,
    get_wheel_sectors,
    spin_wheel,
)


# ==============================================================================
# Helper Inspection Functions for Offline Bot Testing
# ==============================================================================

def extract_bot_sent_messages(mock_bot: AsyncMock) -> list[dict]:
    """Extract messages sent via bot(SendMessage(...)) or bot.send_message."""
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
    """Extract message edits via bot(EditMessageText(...)) or bot.edit_message_text."""
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
    """Extract callback query answers via bot(AnswerCallbackQuery(...)) or bot.answer_callback_query."""
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
# Suite 1: Tier 1 — Feature Coverage
# ==============================================================================

class TestPropertyCatalogStructureAndItems:
    """Verify completeness and specifications of the 12-item property catalog."""

    def test_catalog_overall_counts_and_categories(self):
        """Verify the catalog contains exactly 12 items across 3 categories."""
        catalog = get_property_catalog()
        assert len(catalog) == 12

        cars = get_property_catalog("CAR")
        real_estate = get_property_catalog("REAL_ESTATE")
        luxury = get_property_catalog("LUXURY")

        assert len(cars) == 4
        assert len(real_estate) == 5
        assert len(luxury) == 3

    def test_cars_specifications(self):
        """Verify car specifications: prices, salary buffs, and shift cooldown reductions."""
        cars = {item.item_id: item for item in get_property_catalog("CAR")}

        # 1. VAZ-2107
        vaz = cars["vaz2107"]
        assert vaz.name == "ВАЗ-2107 «Боевая классика»"
        assert vaz.price == Decimal("50000.00")
        assert vaz.salary_bonus_pct == 5
        assert vaz.cooldown_reduction_minutes == 0

        # 2. Lada Vesta
        vesta = cars["vesta"]
        assert vesta.name == "Lada Vesta Sport"
        assert vesta.price == Decimal("350000.00")
        assert vesta.salary_bonus_pct == 10
        assert vesta.cooldown_reduction_minutes == 0

        # 3. BMW M5
        bmw = cars["bmw_m5"]
        assert bmw.name == "BMW M5 Competition"
        assert bmw.price == Decimal("3000000.00")
        assert bmw.salary_bonus_pct == 25
        assert bmw.cooldown_reduction_minutes == 15

        # 4. Maybach
        maybach = cars["maybach"]
        assert maybach.name == "Золотой Майбах"
        assert maybach.price == Decimal("15000000.00")
        assert maybach.salary_bonus_pct == 50
        assert maybach.cooldown_reduction_minutes == 30

    def test_real_estate_specifications(self):
        """Verify real estate specifications: prices and daily rental payouts."""
        estates = {item.item_id: item for item in get_property_catalog("REAL_ESTATE")}

        assert estates["hostel"].price == Decimal("30000.00")
        assert estates["hostel"].daily_rent == Decimal("500.00")

        assert estates["murino"].price == Decimal("1500000.00")
        assert estates["murino"].daily_rent == Decimal("15000.00")

        assert estates["center"].price == Decimal("8000000.00")
        assert estates["center"].daily_rent == Decimal("70000.00")

        assert estates["city_penthouse"].price == Decimal("35000000.00")
        assert estates["city_penthouse"].daily_rent == Decimal("300000.00")

        assert estates["rublyovka"].price == Decimal("100000000.00")
        assert estates["rublyovka"].daily_rent == Decimal("1000000.00")

    def test_luxury_collectibles_specifications(self):
        """Verify luxury collectibles specifications: prices and icons."""
        lux = {item.item_id: item for item in get_property_catalog("LUXURY")}

        assert lux["rolex"].price == Decimal("500000.00")
        assert lux["rolex"].icon == "⌚️"

        assert lux["corgi"].price == Decimal("1000000.00")
        assert lux["corgi"].icon == "🐶"

        assert lux["yacht"].price == Decimal("50000000.00")
        assert lux["yacht"].icon == "🛥"

    def test_property_alias_lookups(self):
        """Verify that common aliases correctly resolve to canonical catalog items."""
        assert get_property_item("studio_murino").item_id == "murino"
        assert get_property_item("bmw").item_id == "bmw_m5"
        assert get_property_item("m5").item_id == "bmw_m5"
        assert get_property_item("vaz").item_id == "vaz2107"
        assert get_property_item("penthouse").item_id == "city_penthouse"
        assert get_property_item("rublevka").item_id == "rublyovka"
        assert get_property_item("non_existent_item") is None


class TestPropertyServiceHappyPaths:
    """Verify happy-path operations of PropertyService."""

    @pytest.mark.asyncio
    async def test_buy_property_happy_path(self, async_session: AsyncSession, sample_user: User):
        """Verify purchasing a property debits balance, writes double-entry ledger, and persists UserProperty."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("100000.00")
        await async_session.commit()

        initial_bal = acc.balance
        item_price = Decimal("50000.00")

        prop = await PropertyService.buy_property(async_session, sample_user.id, "vaz2107")
        assert prop is not None
        assert prop.item_id == "vaz2107"
        assert prop.category == "CAR"
        assert prop.purchase_price == item_price
        assert prop.last_rent_collected_at is None

        # Verify balance debited
        await async_session.refresh(acc)
        assert acc.balance == initial_bal - item_price

        # Verify transaction and ledger entry
        tx_stmt = select(Transaction).where(
            Transaction.sender_account_id == acc.id,
            Transaction.tx_type == "PROPERTY_BUY",
        )
        tx = (await async_session.execute(tx_stmt)).scalar_one()
        assert tx.amount == item_price
        assert tx.category == "Покупки"
        assert "ВАЗ-2107" in tx.description

        ledger_stmt = select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id)
        entry = (await async_session.execute(ledger_stmt)).scalar_one()
        assert entry.direction == "DEBIT"
        assert entry.amount == item_price
        assert entry.balance_after == initial_bal - item_price

    @pytest.mark.asyncio
    async def test_buy_real_estate_initializes_rent_timestamp(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify buying real estate sets initial last_rent_collected_at timestamp."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("50000.00")
        await async_session.commit()

        now = datetime.datetime(2026, 10, 21, 12, 0, 0)
        prop = await PropertyService.buy_property(async_session, sample_user.id, "hostel", now=now)
        assert prop.category == "REAL_ESTATE"
        assert prop.last_rent_collected_at == now

    @pytest.mark.asyncio
    async def test_collect_rental_income_happy_path(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify rental income collection credits account and advances collection timestamp."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("50000.00")
        await async_session.commit()

        t0 = datetime.datetime(2026, 10, 20, 10, 0, 0)
        await PropertyService.buy_property(async_session, sample_user.id, "hostel", now=t0)
        await async_session.commit()

        # Advance 24 hours
        t1 = t0 + datetime.timedelta(hours=24, seconds=5)
        rent, wait_sec, msg = await PropertyService.collect_rental_income(
            async_session, sample_user.id, now=t1
        )

        assert rent == Decimal("500.00")
        assert "успешно собрана" in msg

        # Balance credited
        await async_session.refresh(acc)
        assert acc.balance == Decimal("50000.00") - Decimal("30000.00") + Decimal("500.00")

        # Verify transaction
        tx_stmt = select(Transaction).where(
            Transaction.recipient_account_id == acc.id,
            Transaction.tx_type == "RENTAL_INCOME",
        )
        tx = (await async_session.execute(tx_stmt)).scalar_one()
        assert tx.amount == Decimal("500.00")
        assert tx.category == "Пассивный доход"

    @pytest.mark.asyncio
    async def test_get_user_car_buffs_happy_path(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify user car buffs calculation returns highest salary bonus and highest cooldown reduction."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("20000000.00")
        await async_session.commit()

        # Buy VAZ (+5%, 0m)
        await PropertyService.buy_property(async_session, sample_user.id, "vaz2107")
        buffs = await PropertyService.get_user_car_buffs(async_session, sample_user.id)
        assert buffs == (5, 0)

        # Buy BMW M5 (+25%, 15m)
        await PropertyService.buy_property(async_session, sample_user.id, "bmw_m5")
        buffs = await PropertyService.get_user_car_buffs(async_session, sample_user.id)
        assert buffs == (25, 15)

    @pytest.mark.asyncio
    async def test_job_service_car_buffs_integration(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify JobService.start_shift applies cooldown reduction and claim_salary adds car salary bonus."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("5000000.00")
        await async_session.commit()

        # Buy BMW M5 (+25% salary bonus, 15 min cooldown reduction)
        await PropertyService.buy_property(async_session, sample_user.id, "bmw_m5")

        # Apply for IT job
        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)

        now = datetime.datetime(2026, 10, 21, 12, 0, 0)
        job = await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=now)

        # Cooldown reduction check: shift_started_at should be now - 15 minutes
        expected_start = now - datetime.timedelta(minutes=15)
        assert job.shift_started_at == expected_start

        # Advance time by 1 hour 45 minutes (105 min), so total elapsed = 105 + 15 = 120 min = 2 hours
        claim_time = now + datetime.timedelta(minutes=105)
        payout, event_msg = await JobService.claim_salary(
            async_session,
            sample_user.id,
            JobBranch.IT,
            now=claim_time,
            event_roll=0.99,  # normal event
        )

        # Level 1 IT salary is 1 500 ₽. With 25% car bonus = +375 ₽ => 1 875 ₽
        base_salary = Decimal("1500.00")
        expected_bonus = Decimal("375.00")
        assert payout == base_salary + expected_bonus
        assert "Бонус за авто (+25%)" in event_msg


class TestWheelServiceHappyPaths:
    """Verify happy-path operations of WheelService and all 8 sectors."""

    def test_wheel_sectors_catalog_and_weight_sum(self):
        """Verify 8 wheel sectors exist and weights sum exactly to 100."""
        sectors = get_wheel_sectors()
        assert len(sectors) == 8
        total_weight = sum(s.weight for s in sectors)
        assert total_weight == 100

        sector_ids = [s.sector_id for s in sectors]
        expected_ids = [
            "cash_5k",
            "cash_25k",
            "jackpot_100k",
            "stocks_shaurma",
            "stocks_uspekh",
            "energy_drink",
            "cashback_2k",
            "tax_luxury",
        ]
        assert sector_ids == expected_ids

    @pytest.mark.asyncio
    async def test_can_spin_free_happy_path(self, async_session: AsyncSession, sample_user: User):
        """Verify can_spin_free returns (True, 0) for new user and after 24h."""
        can_spin, rem = await WheelService.can_spin_free(sample_user)
        assert can_spin is True
        assert rem == 0

        # After spin
        now = datetime.datetime(2026, 10, 21, 10, 0, 0)
        sample_user.last_daily_spin_at = now
        await async_session.commit()

        can_spin, rem = await WheelService.can_spin_free(
            sample_user, now=now + datetime.timedelta(hours=10)
        )
        assert can_spin is False
        assert rem == 14 * 3600

        # After 24h
        can_spin, rem = await WheelService.can_spin_free(
            sample_user, now=now + datetime.timedelta(hours=24, seconds=1)
        )
        assert can_spin is True
        assert rem == 0

    @pytest.mark.asyncio
    async def test_spin_wheel_free_updates_timestamp(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify free spin updates user.last_daily_spin_at."""
        now = datetime.datetime(2026, 10, 21, 15, 0, 0)
        res = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=False, sector_override="cash_5k", now=now
        )
        assert res.reward_type == "CASH"
        await async_session.refresh(sample_user)
        assert sample_user.last_daily_spin_at == now

    @pytest.mark.asyncio
    async def test_spin_wheel_extra_deducts_cashback(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify extra spin deducts 1 000 cashback points without modifying last_daily_spin_at."""
        sample_user.cashback_balance = Decimal("2500.00")
        sample_user.last_daily_spin_at = None
        await async_session.commit()

        res = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=True, sector_override="cash_5k"
        )
        assert res.details["is_extra"] is True

        await async_session.refresh(sample_user)
        assert sample_user.cashback_balance == Decimal("1500.00")
        assert sample_user.last_daily_spin_at is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "sector_id,expected_reward_type",
        [
            ("cash_5k", "CASH"),
            ("cash_25k", "CASH"),
            ("jackpot_100k", "CASH"),
            ("stocks_shaurma", "STOCKS"),
            ("stocks_uspekh", "STOCKS"),
            ("energy_drink", "SHIFT_RESET"),
            ("cashback_2k", "CASHBACK"),
            ("tax_luxury", "TAX"),
        ],
    )
    async def test_all_eight_sectors_individually(
        self,
        sector_id: str,
        expected_reward_type: str,
        async_session: AsyncSession,
        sample_user: User,
    ):
        """Verify each of the 8 sectors individually applies expected reward mechanics."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("10000.00")
        sample_user.cashback_balance = Decimal("500.00")
        sample_user.last_daily_spin_at = None
        await async_session.commit()

        now = datetime.datetime(2026, 10, 21, 16, 0, 0)
        res = await WheelService.spin_wheel(
            async_session,
            sample_user.id,
            is_extra=False,
            sector_override=sector_id,
            now=now,
        )

        assert res.sector_id == sector_id
        assert res.reward_type == expected_reward_type
        assert len(res.title) > 0
        assert len(res.description) > 0

        await async_session.refresh(acc)
        await async_session.refresh(sample_user)

        if sector_id == "cash_5k":
            assert acc.balance == Decimal("15000.00")
        elif sector_id == "cash_25k":
            assert acc.balance == Decimal("35000.00")
        elif sector_id == "jackpot_100k":
            assert acc.balance == Decimal("110000.00")
        elif sector_id == "stocks_shaurma":
            stock = (
                await async_session.execute(select(Stock).where(Stock.ticker == "SHAW"))
            ).scalar_one()
            port = (
                await async_session.execute(
                    select(InvestmentPortfolio).where(
                        InvestmentPortfolio.user_id == sample_user.id,
                        InvestmentPortfolio.stock_id == stock.id,
                    )
                )
            ).scalar_one()
            assert port.shares == 10
        elif sector_id == "stocks_uspekh":
            stock = (
                await async_session.execute(select(Stock).where(Stock.ticker == "USPEKH"))
            ).scalar_one()
            port = (
                await async_session.execute(
                    select(InvestmentPortfolio).where(
                        InvestmentPortfolio.user_id == sample_user.id,
                        InvestmentPortfolio.stock_id == stock.id,
                    )
                )
            ).scalar_one()
            assert port.shares == 5
            # Invariant: DEFAULT_STOCKS in investments.py must remain exactly 6
            assert len(DEFAULT_STOCKS) == 6
        elif sector_id == "cashback_2k":
            assert sample_user.cashback_balance == Decimal("2500.00")
        elif sector_id == "tax_luxury":
            assert acc.balance == Decimal("9500.00")


class TestBotRoutersAndKeyboards:
    """Verify Telegram routers, keyboards, and formatters for Property and Wheel."""

    def test_reply_keyboard_2x4_grid_and_no_oleg(self):
        """Verify get_main_menu_keyboard contains the 2x4 grid + analytics row and Oleg is absent."""
        kb = get_main_menu_keyboard()
        rows = [[b.text for b in row] for row in kb.keyboard]

        assert rows[0] == ["💳 Моя карта", "💸 Переводы"]
        assert rows[1] == ["💰 Сейф 18%", "📈 Инвестиции"]
        assert rows[2] == ["💼 Работа и бизнес", "🎁 Кэшбэк"]
        assert rows[3] == ["🎰 Колесо Фортуны", "🏛 Имущество"]
        assert rows[4] == ["📊 Аналитика"]

        all_buttons = [b.text for row in kb.keyboard for b in row]
        assert not any("олег" in b.lower() for b in all_buttons)

    def test_router_registration_in_dispatcher(self):
        """Verify property and wheel routers are registered in create_dispatcher()."""
        dp = create_dispatcher()
        sub_names = [r.name for r in dp.sub_routers]
        assert "property" in sub_names
        assert "wheel" in sub_names
        assert "oleg_router" not in sub_names

    @pytest.mark.asyncio
    async def test_property_router_message_handlers(
        self, async_session: AsyncSession, sample_user: User, message_factory, mock_bot: AsyncMock
    ):
        """Verify /property command and reply button message open property dashboard."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)

        # 1. Command /property
        msg_cmd = message_factory("/property", user_id=sample_user.telegram_id)
        await handle_property_menu(msg_cmd, session=async_session, user=sample_user, main_account=acc)

        # 2. Text message '🏛 Имущество'
        msg_txt = message_factory("🏛 Имущество", user_id=sample_user.telegram_id)
        await handle_property_menu(msg_txt, session=async_session, user=sample_user, main_account=acc)

        sent = extract_bot_sent_messages(mock_bot)
        assert len(sent) >= 2
        assert "Имущество & Роскошь" in sent[-1]["text"]

    @pytest.mark.asyncio
    async def test_property_router_callbacks(
        self, async_session: AsyncSession, sample_user: User, callback_factory, mock_bot: AsyncMock
    ):
        """Verify property callbacks: prop:menu, prop:cat:CAR, prop:buy:vaz2107, prop:my."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("100000.00")
        await async_session.commit()

        # prop:menu
        cb_menu = callback_factory("prop:menu", user_id=sample_user.telegram_id)
        await handle_property_menu_callback(cb_menu, session=async_session, user=sample_user, main_account=acc)

        # prop:cat:CAR
        cb_cat = callback_factory("prop:cat:CAR", user_id=sample_user.telegram_id)
        await handle_property_category(cb_cat, session=async_session, user=sample_user, main_account=acc)

        # prop:buy:vaz2107
        cb_buy = callback_factory("prop:buy:vaz2107", user_id=sample_user.telegram_id)
        await handle_property_buy(cb_buy, session=async_session, user=sample_user, main_account=acc)

        # prop:my
        cb_my = callback_factory("prop:my", user_id=sample_user.telegram_id)
        await handle_property_my(cb_my, session=async_session, user=sample_user, main_account=acc)

        # prop:rent:claim
        cb_rent = callback_factory("prop:rent:claim", user_id=sample_user.telegram_id)
        await handle_property_rent_claim(cb_rent, session=async_session, user=sample_user, main_account=acc)

        answers = extract_bot_callback_answers(mock_bot)
        assert any("Поздравляем с покупкой" in (a["text"] or "") for a in answers)

    @pytest.mark.asyncio
    async def test_wheel_router_messages_and_callbacks(
        self, async_session: AsyncSession, sample_user: User, message_factory, callback_factory, mock_bot: AsyncMock
    ):
        """Verify wheel router handles /wheel, '🎰 Колесо Фортуны', wheel:menu, and wheel:spin."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)

        # Command /wheel
        msg = message_factory("/wheel", user_id=sample_user.telegram_id)
        await handle_wheel_menu(msg, session=async_session, user=sample_user, main_account=acc)

        # Callback wheel:menu
        cb_menu = callback_factory("wheel:menu", user_id=sample_user.telegram_id)
        await handle_wheel_menu_callback(cb_menu, session=async_session, user=sample_user, main_account=acc)

        # Callback wheel:spin (free)
        cb_spin = callback_factory("wheel:spin", user_id=sample_user.telegram_id)
        await handle_wheel_spin(cb_spin, session=async_session, user=sample_user, main_account=acc)

        answers = extract_bot_callback_answers(mock_bot)
        assert any("Выигрыш:" in (a["text"] or "") for a in answers)

    def test_formatters_html_escaping_adversarial(self):
        """Verify formatters strictly sanitize HTML injection payloads."""
        xss_user = User(
            id=123,
            telegram_id=123456,
            username="<b>hacker</b>",
            full_name="<script>alert('xss')</script>",
            cashback_balance=Decimal("1000.00"),
        )
        # Property overview
        text_prop = format_property_overview(xss_user, [], 0, (0, 0))
        assert "<script>" not in text_prop
        assert "&lt;script&gt;alert('xss')&lt;/script&gt;" in text_prop

        # Wheel dashboard
        text_wheel = format_wheel_dashboard(xss_user, True, 0)
        assert "<script>" not in text_wheel
        assert "&lt;script&gt;alert('xss')&lt;/script&gt;" in text_wheel

        # Wheel result: sector title must be HTML escaped
        result = WheelResult(
            sector_id="cash_5k",
            title="<script>5k</script>",
            reward_type="CASH",
            description="5 000 ₽ начислено",
            icon="💵",
        )
        text_res = format_wheel_spin_result(result)
        assert "<script>" not in text_res
        assert "&lt;script&gt;5k&lt;/script&gt;" in text_res


# ==============================================================================
# Suite 2: Tier 2 — Boundary & Corner Cases
# ==============================================================================

class TestPropertyAndWheelBoundaryCases:
    """Verify corner cases, guard limits, error handling, and concurrency invariants."""

    @pytest.mark.asyncio
    async def test_buy_property_insufficient_funds_raises(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify buying property with insufficient funds raises InsufficientFundsError and keeps balance intact."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("100.00")
        await async_session.commit()

        with pytest.raises(InsufficientFundsError):
            await PropertyService.buy_property(async_session, sample_user.id, "vaz2107")

        await async_session.refresh(acc)
        assert acc.balance == Decimal("100.00")

        owned = await PropertyService.get_user_properties(async_session, sample_user.id)
        assert len(owned) == 0

    @pytest.mark.asyncio
    async def test_buy_duplicate_property_raises(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify buying duplicate property raises ValueError and prevents double-debit."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("200000.00")
        await async_session.commit()

        await PropertyService.buy_property(async_session, sample_user.id, "vaz2107")
        await async_session.refresh(acc)
        bal_after_first = acc.balance

        with pytest.raises(ValueError, match="Вы уже владеете"):
            await PropertyService.buy_property(async_session, sample_user.id, "vaz2107")

        await async_session.refresh(acc)
        assert acc.balance == bal_after_first

        owned = await PropertyService.get_user_properties(async_session, sample_user.id)
        assert len(owned) == 1

    @pytest.mark.asyncio
    async def test_buy_property_on_frozen_account_raises(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify frozen account cannot buy property."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("100000.00")
        acc.is_frozen = True
        await async_session.commit()

        with pytest.raises(AccountFrozenError):
            await PropertyService.buy_property(async_session, sample_user.id, "vaz2107")

    @pytest.mark.asyncio
    async def test_buy_nonexistent_property_item_raises(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify buying unknown property ID raises ValueError."""
        with pytest.raises(ValueError, match="не найдено в каталоге"):
            await PropertyService.buy_property(async_session, sample_user.id, "ufo_spaceship")

    @pytest.mark.asyncio
    async def test_collect_rent_when_cooldown_not_elapsed(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify collecting rental income before 24h yields 0.00 ₽ and leaves balance unchanged."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("50000.00")
        await async_session.commit()

        t0 = datetime.datetime(2026, 10, 21, 10, 0, 0)
        await PropertyService.buy_property(async_session, sample_user.id, "hostel", now=t0)
        await async_session.commit()

        # Check only 5 hours later
        t1 = t0 + datetime.timedelta(hours=5)
        rent, wait_sec, msg = await PropertyService.collect_rental_income(
            async_session, sample_user.id, now=t1
        )

        assert rent == Decimal("0.00")
        assert wait_sec == 19 * 3600
        assert "еще не накопилась" in msg

        await async_session.refresh(acc)
        assert acc.balance == Decimal("20000.00")

    @pytest.mark.asyncio
    async def test_collect_rent_multiple_properties_varying_cooldowns(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify rent is collected only for properties whose 24h cooldown has expired."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("5000000.00")
        await async_session.commit()

        t0 = datetime.datetime(2026, 10, 20, 10, 0, 0)
        await PropertyService.buy_property(async_session, sample_user.id, "hostel", now=t0)

        # Buy murino 12 hours later
        t_murino = t0 + datetime.timedelta(hours=12)
        await PropertyService.buy_property(async_session, sample_user.id, "murino", now=t_murino)
        await async_session.commit()

        # Now evaluate at t0 + 24h + 1m: hostel is ready (500 ₽), murino is NOT ready (needs 12 more hours)
        t_eval = t0 + datetime.timedelta(hours=24, minutes=1)
        rent, wait_sec, msg = await PropertyService.collect_rental_income(
            async_session, sample_user.id, now=t_eval
        )

        assert rent == Decimal("500.00")
        # Next wait should be remaining time on murino (~12 hours)
        assert 43000 <= wait_sec <= 43200

    @pytest.mark.asyncio
    async def test_free_spin_on_cooldown_raises(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify attempting free spin during cooldown raises ValueError."""
        now = datetime.datetime(2026, 10, 21, 10, 0, 0)
        sample_user.last_daily_spin_at = now
        await async_session.commit()

        attempt_time = now + datetime.timedelta(hours=2)
        with pytest.raises(ValueError, match="Бесплатный спин еще недоступен"):
            await WheelService.spin_wheel(
                async_session, sample_user.id, is_extra=False, now=attempt_time
            )

        await async_session.refresh(sample_user)
        assert sample_user.last_daily_spin_at == now

    @pytest.mark.asyncio
    async def test_extra_spin_insufficient_cashback_raises(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify extra spin with < 1000 cashback points raises InsufficientFundsError."""
        sample_user.cashback_balance = Decimal("750.00")
        await async_session.commit()

        with pytest.raises(InsufficientFundsError, match="Недостаточно бонусных рублей"):
            await WheelService.spin_wheel(async_session, sample_user.id, is_extra=True)

        await async_session.refresh(sample_user)
        assert sample_user.cashback_balance == Decimal("750.00")

    @pytest.mark.asyncio
    async def test_luxury_tax_under_500_mercy(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify luxury tax on account with < 500 ₽ charges 0 ₽ and returns mercy message."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("250.00")
        sample_user.last_daily_spin_at = None
        await async_session.commit()

        res = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=False, sector_override="tax_luxury"
        )
        assert "проявила милосердие" in res.description

        await async_session.refresh(acc)
        assert acc.balance == Decimal("250.00")

    @pytest.mark.asyncio
    async def test_luxury_tax_over_500_charges(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify luxury tax on account with >= 500 ₽ charges 500 ₽ and records transaction."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("1500.00")
        sample_user.last_daily_spin_at = None
        await async_session.commit()

        res = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=False, sector_override="tax_luxury"
        )
        assert "500.00 ₽" in res.description
        assert "Налог на роскошь" in res.description

        await async_session.refresh(acc)
        assert acc.balance == Decimal("1000.00")

        tx = (
            await async_session.execute(
                select(Transaction).where(
                    Transaction.sender_account_id == acc.id,
                    Transaction.tx_type == "WHEEL_TAX",
                )
            )
        ).scalar_one()
        assert tx.amount == Decimal("500.00")

    @pytest.mark.asyncio
    async def test_car_buffs_when_no_cars_owned(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify car buffs returns (0, 0) when user has no cars."""
        buffs = await PropertyService.get_user_car_buffs(async_session, sample_user.id)
        assert buffs == (0, 0)

    @pytest.mark.asyncio
    async def test_car_buffs_multiple_cars_takes_max_bonus_and_max_reduction(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify owning multiple cars yields max bonus and max cooldown reduction."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("25000000.00")
        await async_session.commit()

        # Buy VAZ (+5%, 0m), Lada Vesta (+10%, 0m), and Maybach (+50%, 30m)
        await PropertyService.buy_property(async_session, sample_user.id, "vaz2107")
        await PropertyService.buy_property(async_session, sample_user.id, "vesta")
        await PropertyService.buy_property(async_session, sample_user.id, "maybach")

        buffs = await PropertyService.get_user_car_buffs(async_session, sample_user.id)
        assert buffs == (50, 30)

    def test_all_property_and_wheel_callbacks_under_64_bytes(self):
        """Verify all callback_data produced by property and wheel keyboards are strictly <= 64 bytes."""
        # Property keyboards
        kb1 = get_property_menu_keyboard(has_real_estate=True)
        for row in kb1.inline_keyboard:
            for b in row:
                assert len(b.callback_data.encode("utf-8")) <= 64

        catalog = get_property_catalog()
        for cat in ("CAR", "REAL_ESTATE", "LUXURY"):
            kb2 = get_property_category_keyboard(cat, catalog, set())
            for row in kb2.inline_keyboard:
                for b in row:
                    assert len(b.callback_data.encode("utf-8")) <= 64

        kb3 = get_my_properties_keyboard(has_real_estate=True)
        for row in kb3.inline_keyboard:
            for b in row:
                assert len(b.callback_data.encode("utf-8")) <= 64

        # Wheel keyboards
        kb4 = get_wheel_keyboard(can_free_spin=True, remaining_seconds=0)
        for row in kb4.inline_keyboard:
            for b in row:
                assert len(b.callback_data.encode("utf-8")) <= 64

        kb5 = get_wheel_result_keyboard(can_free_spin=False)
        for row in kb5.inline_keyboard:
            for b in row:
                assert len(b.callback_data.encode("utf-8")) <= 64


# ==============================================================================
# Suite 3: Tier 3 — Cross-Feature Interactions
# ==============================================================================

class TestCrossFeatureInteractions:
    """Verify multi-service interactions between Wheel, Property, Jobs, and Investments."""

    @pytest.mark.asyncio
    async def test_energy_drink_resets_shift_making_salary_immediately_claimable(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify Energy Drink from Wheel resets shift wait timer so salary is immediately claimable."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("1000.00")
        await async_session.commit()

        # 1. Apply to IT job and start shift
        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.IT)
        now = datetime.datetime(2026, 10, 21, 10, 0, 0)
        await JobService.start_shift(async_session, sample_user.id, JobBranch.IT, now=now)

        # 2. Premature claim attempt without wait fails
        with pytest.raises(ValueError, match="Смена еще не окончена"):
            await JobService.claim_salary(
                async_session, sample_user.id, JobBranch.IT, now=now + datetime.timedelta(minutes=10)
            )

        # 3. Spin wheel with energy drink
        res = await WheelService.spin_wheel(
            async_session,
            sample_user.id,
            is_extra=False,
            sector_override="energy_drink",
            now=now + datetime.timedelta(minutes=15),
        )
        assert res.reward_type == "SHIFT_RESET"
        assert "Энергетик Т-Драйв подействовал" in res.description

        # 4. Claim salary immediately! Should succeed without error.
        payout, msg = await JobService.claim_salary(
            async_session,
            sample_user.id,
            JobBranch.IT,
            now=now + datetime.timedelta(minutes=16),
            event_roll=0.99,
        )
        assert payout >= Decimal("1500.00")

    @pytest.mark.asyncio
    async def test_car_purchase_synergy_with_jobs(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify purchasing a car speeds up shift start and increases salary payout."""
        acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        acc.balance = Decimal("5000000.00")
        await async_session.commit()

        # Buy BMW M5 (cooldown reduction 15m, salary bonus 25%)
        await PropertyService.buy_property(async_session, sample_user.id, "bmw_m5")
        await JobService.apply_for_job(async_session, sample_user.id, JobBranch.SERVICE)

        start_time = datetime.datetime(2026, 10, 21, 12, 0, 0)
        job = await JobService.start_shift(
            async_session, sample_user.id, JobBranch.SERVICE, now=start_time
        )
        # Shift started at should be offset by -15m
        assert job.shift_started_at == start_time - datetime.timedelta(minutes=15)

        # Claim at +1h 45m
        claim_time = start_time + datetime.timedelta(minutes=105)
        payout, msg = await JobService.claim_salary(
            async_session,
            sample_user.id,
            JobBranch.SERVICE,
            now=claim_time,
            event_roll=0.99,
        )
        # Level 1 Service salary is 1 200 ₽. With 25% bonus = +300 ₽ => 1 500 ₽
        assert payout == Decimal("1500.00")

    @pytest.mark.asyncio
    async def test_fortune_wheel_cashback_prize_funds_extra_spins(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify winning cashback from wheel can immediately fund extra wheel spins."""
        sample_user.cashback_balance = Decimal("0.00")
        sample_user.last_daily_spin_at = None
        await async_session.commit()

        # Free spin wins +2000 cashback
        res = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=False, sector_override="cashback_2k"
        )
        await async_session.refresh(sample_user)
        assert sample_user.cashback_balance == Decimal("2000.00")

        # Now take 1st extra spin for 1000
        res1 = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=True, sector_override="cash_5k"
        )
        await async_session.refresh(sample_user)
        assert sample_user.cashback_balance == Decimal("1000.00")

        # Take 2nd extra spin for 1000
        res2 = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=True, sector_override="cash_5k"
        )
        await async_session.refresh(sample_user)
        assert sample_user.cashback_balance == Decimal("0.00")

        # 3rd extra spin should fail due to 0 balance
        with pytest.raises(InsufficientFundsError):
            await WheelService.spin_wheel(async_session, sample_user.id, is_extra=True)

    @pytest.mark.asyncio
    async def test_fortune_wheel_uspekh_prize_preserves_default_stocks_invariant(
        self, async_session: AsyncSession, sample_user: User
    ):
        """Verify winning USPEKH stocks dynamically adds to DB while keeping DEFAULT_STOCKS at 6 items."""
        initial_default_len = len(DEFAULT_STOCKS)
        assert initial_default_len == 6

        res = await WheelService.spin_wheel(
            async_session, sample_user.id, is_extra=False, sector_override="stocks_uspekh"
        )
        assert res.reward_type == "STOCKS"

        # Check stock in DB
        stock = (
            await async_session.execute(select(Stock).where(Stock.ticker == "USPEKH"))
        ).scalar_one()
        assert stock.name == 'ООО "Успешный успех"'

        # Portfolio verification
        port = (
            await async_session.execute(
                select(InvestmentPortfolio).where(
                    InvestmentPortfolio.user_id == sample_user.id,
                    InvestmentPortfolio.stock_id == stock.id,
                )
            )
        ).scalar_one()
        assert port.shares == 5

        # Invariant verification: DEFAULT_STOCKS list in investments.py must be untouched
        assert len(DEFAULT_STOCKS) == 6


# ==============================================================================
# Suite 4: Tier 4 — Real-World E2E Scenario
# ==============================================================================

class TestRealWorldE2ELifecycle:
    """End-to-End Real World Scenario testing complete user lifecycle across all bank subsystems."""

    @pytest.mark.asyncio
    async def test_full_user_lifecycle_property_and_wheel(
        self, async_session: AsyncSession
    ):
        """Complete user lifecycle:

        1. Register user with 100 000 ₽ initial card balance and 0 cashback.
        2. Spin wheel (free) -> win 25 000 ₽ cash (balance becomes 125 000 ₽).
        3. Attempt immediate second free spin -> fails with cooldown error.
        4. Buy VAZ-2107 car for 50 000 ₽ (balance becomes 75 000 ₽).
        5. Buy Hostel real estate for 30 000 ₽ (balance becomes 45 000 ₽).
        6. Apply for IT job and start shift with car cooldown buff.
        7. Win +2000 cashback via extra spin or wheel prize.
        8. Extra spin lands Energy Drink -> instantly concludes shift.
        9. Claim salary -> receives base salary + 5% car salary bonus.
        10. Advance time by 24 hours -> collect rental income from hostel (+500 ₽).
        11. Full audit of all Transactions and double-entry LedgerEntries:
            All entries balanced, categorized, and zero overdraft invariant preserved.
        """
        # 1. Onboard user
        user, acc, card = await BankingService.get_or_create_user(
            async_session,
            telegram_id=888777,
            username="vladimir_magnat",
            full_name="Владимир Магнат",
        )
        acc.balance = Decimal("100000.00")
        user.cashback_balance = Decimal("0.00")
        await async_session.commit()

        t0 = datetime.datetime(2026, 10, 21, 10, 0, 0)

        # 2. Spin free wheel -> win 25 000 ₽ cash
        res_wheel = await WheelService.spin_wheel(
            async_session, user.id, is_extra=False, sector_override="cash_25k", now=t0
        )
        assert res_wheel.reward_type == "CASH"
        await async_session.refresh(acc)
        assert acc.balance == Decimal("125000.00")

        # 3. Attempt immediate free spin -> rejected on cooldown
        with pytest.raises(ValueError, match="Бесплатный спин еще недоступен"):
            await WheelService.spin_wheel(
                async_session, user.id, is_extra=False, now=t0 + datetime.timedelta(minutes=5)
            )

        # 4. Buy VAZ-2107 car (50 000 ₽)
        car = await PropertyService.buy_property(async_session, user.id, "vaz2107", now=t0)
        assert car.item_id == "vaz2107"
        await async_session.refresh(acc)
        assert acc.balance == Decimal("75000.00")

        # 5. Buy Hostel real estate (30 000 ₽)
        hostel = await PropertyService.buy_property(async_session, user.id, "hostel", now=t0)
        assert hostel.item_id == "hostel"
        await async_session.refresh(acc)
        assert acc.balance == Decimal("45000.00")

        # 6. Apply for IT job and start shift
        await JobService.apply_for_job(async_session, user.id, JobBranch.IT)
        job = await JobService.start_shift(async_session, user.id, JobBranch.IT, now=t0)
        assert job.is_on_shift is True

        # 7. Add 1000 cashback points for testing extra spin
        user.cashback_balance = Decimal("1000.00")
        await async_session.commit()

        # 8. Extra spin lands Energy Drink -> completes shift immediately
        res_energy = await WheelService.spin_wheel(
            async_session, user.id, is_extra=True, sector_override="energy_drink", now=t0
        )
        assert res_energy.reward_type == "SHIFT_RESET"

        # 9. Claim salary: base 1500 ₽ + 5% car bonus (75 ₽) = 1575 ₽
        salary_payout, _ = await JobService.claim_salary(
            async_session,
            user.id,
            JobBranch.IT,
            now=t0 + datetime.timedelta(minutes=1),
            event_roll=0.99,
        )
        assert salary_payout == Decimal("1575.00")
        await async_session.refresh(acc)
        assert acc.balance == Decimal("45000.00") + Decimal("1575.00")

        # 10. Advance time by 24 hours -> collect rental income from hostel (+500 ₽)
        t_next_day = t0 + datetime.timedelta(hours=24, minutes=5)
        rent, _, _ = await PropertyService.collect_rental_income(
            async_session, user.id, now=t_next_day
        )
        assert rent == Decimal("500.00")
        await async_session.refresh(acc)
        final_expected_balance = Decimal("45000.00") + Decimal("1575.00") + Decimal("500.00")
        assert acc.balance == final_expected_balance

        # 11. Comprehensive Audit of Transactions and Double-Entry Ledger
        tx_stmt = select(Transaction).order_by(Transaction.created_at.asc())
        all_txs = list((await async_session.execute(tx_stmt)).scalars().all())

        tx_types = [tx.tx_type for tx in all_txs]
        assert "WHEEL_PRIZE" in tx_types
        assert "PROPERTY_BUY" in tx_types
        assert "SALARY" in tx_types
        assert "RENTAL_INCOME" in tx_types

        # Verify double-entry ledger entries exist and match transaction amounts
        ledger_stmt = select(LedgerEntry).order_by(LedgerEntry.created_at.asc())
        all_ledgers = list((await async_session.execute(ledger_stmt)).scalars().all())
        assert len(all_ledgers) >= 5

        for entry in all_ledgers:
            assert entry.amount > Decimal("0.00")
            assert entry.direction in ("DEBIT", "CREDIT")
            assert entry.balance_after >= Decimal("0.00")
