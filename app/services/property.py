"""T-Property & Luxury Domain Service: Cars with job buffs, rental real estate, and luxury collectibles."""

import datetime
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Account, LedgerEntry, Transaction, UserProperty
from app.services.banking import BankingService
from app.services.receipts import ReceiptService
from app.services.transfers import AccountFrozenError, InsufficientFundsError


@dataclass(frozen=True)
class PropertyItem:
    """Catalog item definition for property, vehicles, and collectibles."""

    item_id: str
    category: str  # "CAR", "REAL_ESTATE", "LUXURY"
    name: str
    price: Decimal
    icon: str
    description: str
    salary_bonus_pct: int = 0
    cooldown_reduction_minutes: int = 0
    daily_rent: Decimal = Decimal("0.00")


# Complete Authoritative Property Catalog
PROPERTY_CATALOG: list[PropertyItem] = [
    # Cars (Buffs: salary bonus and shift cooldown reduction)
    PropertyItem(
        item_id="vaz2107",
        category="CAR",
        name="ВАЗ-2107 «Боевая классика»",
        price=Decimal("50000.00"),
        icon="🚗",
        description="Легенда дрифта у ТЦ. Двери скрипят, но на работу довезет с ветерком.",
        salary_bonus_pct=5,
        cooldown_reduction_minutes=0,
    ),
    PropertyItem(
        item_id="vesta",
        category="CAR",
        name="Lada Vesta Sport",
        price=Decimal("350000.00"),
        icon="🏎",
        description="Спортивный обвес и отечественный характер. Коллеги по офису завидуют.",
        salary_bonus_pct=10,
        cooldown_reduction_minutes=0,
    ),
    PropertyItem(
        item_id="bmw_m5",
        category="CAR",
        name="BMW M5 Competition",
        price=Decimal("3000000.00"),
        icon="⚡️",
        description="Шашки по МКАДу, выхлоп будит район. Сокращает смену на 15 минут!",
        salary_bonus_pct=25,
        cooldown_reduction_minutes=15,
    ),
    PropertyItem(
        item_id="maybach",
        category="CAR",
        name="Золотой Майбах",
        price=Decimal("15000000.00"),
        icon="👑",
        description="Личный водитель и массаж сидений. Смена короче на 30 минут!",
        salary_bonus_pct=50,
        cooldown_reduction_minutes=30,
    ),
    # Real Estate (Passive daily rental income with 24h timer)
    PropertyItem(
        item_id="hostel",
        category="REAL_ESTATE",
        name="Койка в хостеле",
        price=Decimal("30000.00"),
        icon="🛏",
        description="Двухъярусная кровать у вокзала. Приносит 500 ₽ в сутки.",
        daily_rent=Decimal("500.00"),
    ),
    PropertyItem(
        item_id="murino",
        category="REAL_ESTATE",
        name="Однушка в Мурино",
        price=Decimal("1500000.00"),
        icon="🏢",
        description="25-й этаж человейника. Сдается айтишникам за 15 000 ₽ в день.",
        daily_rent=Decimal("15000.00"),
    ),
    PropertyItem(
        item_id="center",
        category="REAL_ESTATE",
        name="Трешка в центре",
        price=Decimal("8000000.00"),
        icon="🏛",
        description="Исторический фонд. Приносит 70 000 ₽ ежедневно.",
        daily_rent=Decimal("70000.00"),
    ),
    PropertyItem(
        item_id="city_penthouse",
        category="REAL_ESTATE",
        name="Пентхаус в Сити",
        price=Decimal("35000000.00"),
        icon="🏙",
        description="Панорамные окна с видом на смог. 300 000 ₽ пассивного дохода в сутки!",
        daily_rent=Decimal("300000.00"),
    ),
    PropertyItem(
        item_id="rublyovka",
        category="REAL_ESTATE",
        name="Особняк на Рублевке",
        price=Decimal("100000000.00"),
        icon="🏰",
        description="Личный вертодром. 1 000 000 ₽ в день на полном пассиве!",
        daily_rent=Decimal("1000000.00"),
    ),
    # Luxury Collectibles (Status & prestige)
    PropertyItem(
        item_id="rolex",
        category="LUXURY",
        name="Золотые часы Rolex Т-Bank Edition",
        price=Decimal("500000.00"),
        icon="⌚️",
        description="Тикают с точностью до миллисекунды. Статус успешного вкладчика.",
    ),
    PropertyItem(
        item_id="corgi",
        category="LUXURY",
        name="Породистый корги Инвестор",
        price=Decimal("1000000.00"),
        icon="🐶",
        description="Гавкает при падении котировок и радует глаз короткими лапками.",
    ),
    PropertyItem(
        item_id="yacht",
        category="LUXURY",
        name="Яхта «Непотопляемый кэшбэк»",
        price=Decimal("50000000.00"),
        icon="🛥",
        description="Белоснежная суперъяхта главного крипто-магната Т-Банка.",
    ),
]

_PROPERTY_INDEX: dict[str, PropertyItem] = {item.item_id: item for item in PROPERTY_CATALOG}

# Aliases for catalog lookup flexibility
_PROPERTY_ALIASES: dict[str, str] = {
    "studio_murino": "murino",
    "bmw": "bmw_m5",
    "m5": "bmw_m5",
    "vaz": "vaz2107",
    "penthouse": "city_penthouse",
    "rublevka": "rublyovka",
}


class PropertyService:
    """Core domain service for property purchases, rental accrual, and car buff calculations."""

    RENT_COOLDOWN_SECONDS: int = 86400  # 24 hours

    @classmethod
    def get_property_catalog(cls, category: str | None = None) -> list[PropertyItem]:
        """Return the complete catalog or filter by category (CAR, REAL_ESTATE, LUXURY)."""
        if not category:
            return list(PROPERTY_CATALOG)
        cat_upper = category.upper()
        return [item for item in PROPERTY_CATALOG if item.category == cat_upper]

    @classmethod
    def get_property_item(cls, item_id: str) -> PropertyItem | None:
        """Find a property item by exact item_id or recognized alias."""
        clean_id = item_id.strip().lower()
        resolved_id = _PROPERTY_ALIASES.get(clean_id, clean_id)
        return _PROPERTY_INDEX.get(resolved_id)

    @classmethod
    async def get_user_properties(cls, session: AsyncSession, user_id: int) -> list[UserProperty]:
        """Query all property items owned by a user."""
        stmt = (
            select(UserProperty)
            .where(UserProperty.user_id == user_id)
            .order_by(UserProperty.purchased_at.asc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def buy_property(
        cls,
        session: AsyncSession,
        user_id: int,
        item_id: str,
        now: datetime.datetime | None = None,
    ) -> UserProperty:
        """Atomically purchase property item, debiting main account with double-entry ledger."""
        item = cls.get_property_item(item_id)
        if not item:
            raise ValueError(f"Имущество с ID '{item_id}' не найдено в каталоге")

        # 1. Enforce unique ownership per item per user
        existing_stmt = select(UserProperty).where(
            UserProperty.user_id == user_id, UserProperty.item_id == item.item_id
        )
        existing_res = await session.execute(existing_stmt)
        if existing_res.scalar_one_or_none():
            raise ValueError(f"Вы уже владеете «{item.name}»! Повторная покупка невозможна.")

        # 2. Get user's main bank account
        main_acc = await BankingService.get_user_main_account(session, user_id)
        if not main_acc:
            raise ValueError(f"Основной банковский счет пользователя {user_id} не найден")

        if main_acc.is_frozen:
            raise AccountFrozenError("Счет заморожен. Покупка имущества невозможна.")

        # 3. Atomic debit with balance >= price constraint guard
        debit_stmt = (
            update(Account)
            .where(Account.id == main_acc.id)
            .where(Account.balance >= item.price)
            .where(Account.is_frozen == False)  # noqa: E712
            .values(balance=Account.balance - item.price)
        )
        debit_res = await session.execute(debit_stmt)
        if debit_res.rowcount == 0:
            raise InsufficientFundsError(
                f"Недостаточно средств для покупки «{item.name}». Требуется: {item.price:,.2f} ₽, на счете: {main_acc.balance:,.2f} ₽"
            )

        # 4. Authoritative post-debit balance query
        new_balance = (
            await session.execute(select(Account.balance).where(Account.id == main_acc.id))
        ).scalar_one()

        current_time = now or datetime.datetime.now()

        # 5. Create Transaction and double-entry LedgerEntry
        tx_id = f"prop-{uuid.uuid4().hex[:12]}"
        tx = Transaction(
            id=tx_id,
            sender_account_id=main_acc.id,
            recipient_account_id=None,
            amount=item.price,
            tx_type="PROPERTY_BUY",
            category="Покупки",
            description=f"Покупка: {item.name}",
            status="COMPLETED",
            fiscal_code=ReceiptService.generate_fiscal_code(),
            auth_code=ReceiptService.generate_auth_code(),
            created_at=current_time,
        )
        ledger_entry = LedgerEntry(
            transaction_id=tx_id,
            account_id=main_acc.id,
            direction="DEBIT",
            amount=item.price,
            balance_after=new_balance,
            created_at=current_time,
        )
        session.add(tx)
        session.add(ledger_entry)

        # 6. Insert UserProperty entity
        prop = UserProperty(
            user_id=user_id,
            category=item.category,
            item_id=item.item_id,
            purchase_price=item.price,
            purchased_at=current_time,
            last_rent_collected_at=current_time if item.category == "REAL_ESTATE" else None,
        )
        session.add(prop)
        await session.flush()
        return prop

    @classmethod
    async def collect_rental_income(
        cls,
        session: AsyncSession,
        user_id: int,
        now: datetime.datetime | None = None,
    ) -> tuple[Decimal, int, str]:
        """Collect daily rental income across all owned real estate with 24-hour timer verification."""
        current_time = now or datetime.datetime.now()

        stmt = (
            select(UserProperty)
            .where(UserProperty.user_id == user_id)
            .where(UserProperty.category == "REAL_ESTATE")
        )
        res = await session.execute(stmt)
        real_estate_props = list(res.scalars().all())

        if not real_estate_props:
            return (Decimal("0.00"), 0, "У вас нет недвижимости для сбора арендной платы.")

        total_rent = Decimal("0.00")
        min_seconds_remaining: int | None = None
        collected_count = 0

        cutoff_time = current_time - datetime.timedelta(seconds=cls.RENT_COOLDOWN_SECONDS)

        for prop in real_estate_props:
            base_time = prop.last_rent_collected_at or prop.purchased_at
            elapsed = (current_time - base_time).total_seconds()

            if elapsed >= cls.RENT_COOLDOWN_SECONDS or prop.last_rent_collected_at is None:
                item = cls.get_property_item(prop.item_id)
                if item and item.daily_rent > Decimal("0.00"):
                    # Atomic CAS guard against concurrent double-collection
                    cas_condition = or_(
                        UserProperty.last_rent_collected_at <= cutoff_time,
                        UserProperty.last_rent_collected_at.is_(None),
                    )
                    cas_stmt = (
                        update(UserProperty)
                        .where(UserProperty.id == prop.id)
                        .where(cas_condition)
                        .values(last_rent_collected_at=current_time)
                    )
                    cas_res = await session.execute(cas_stmt)
                    if cas_res.rowcount > 0:
                        total_rent += item.daily_rent
                        prop.last_rent_collected_at = current_time
                        collected_count += 1
                    else:
                        if min_seconds_remaining is None or cls.RENT_COOLDOWN_SECONDS < min_seconds_remaining:
                            min_seconds_remaining = cls.RENT_COOLDOWN_SECONDS
            else:
                rem = max(1, int(cls.RENT_COOLDOWN_SECONDS - elapsed))
                if min_seconds_remaining is None or rem < min_seconds_remaining:
                    min_seconds_remaining = rem

        if total_rent > Decimal("0.00"):
            main_acc = await BankingService.get_user_main_account(session, user_id)
            if not main_acc:
                raise ValueError(f"Основной счет пользователя {user_id} не найден")

            # Atomic credit to main account
            await session.execute(
                update(Account)
                .where(Account.id == main_acc.id)
                .values(balance=Account.balance + total_rent)
            )

            new_balance = (
                await session.execute(select(Account.balance).where(Account.id == main_acc.id))
            ).scalar_one()

            # Record Transaction and LedgerEntry
            tx_id = f"rent-{uuid.uuid4().hex[:12]}"
            tx = Transaction(
                id=tx_id,
                sender_account_id=None,
                recipient_account_id=main_acc.id,
                amount=total_rent,
                tx_type="RENTAL_INCOME",
                category="Пассивный доход",
                description="Арендная плата за недвижимость",
                status="COMPLETED",
                fiscal_code=ReceiptService.generate_fiscal_code(),
                auth_code=ReceiptService.generate_auth_code(),
                created_at=current_time,
            )
            ledger_entry = LedgerEntry(
                transaction_id=tx_id,
                account_id=main_acc.id,
                direction="CREDIT",
                amount=total_rent,
                balance_after=new_balance,
                created_at=current_time,
            )
            session.add(tx)
            session.add(ledger_entry)
            await session.flush()

            next_wait = min_seconds_remaining if min_seconds_remaining is not None else cls.RENT_COOLDOWN_SECONDS
            msg = (
                f"🏛 <b>Арендная плата успешно собрана!</b>\n\n"
                f"Начислено: <b>+{total_rent:,.2f} ₽</b> (объектов: {collected_count})\n"
                f"Следующий сбор через 24 ч."
            )
            return (total_rent, next_wait, msg)

        # No rent ready yet
        wait_sec = min_seconds_remaining if min_seconds_remaining is not None else cls.RENT_COOLDOWN_SECONDS
        hours = wait_sec // 3600
        mins = (wait_sec % 3600) // 60
        msg = f"⏳ Арендная плата еще не накопилась. До следующего сбора: {hours} ч {mins} мин."
        return (Decimal("0.00"), wait_sec, msg)

    @classmethod
    async def get_user_car_buffs(cls, session: AsyncSession, user_id: int) -> tuple[int, int]:
        """Calculate active car buffs: (max_salary_bonus_pct, max_cooldown_reduction_minutes)."""
        stmt = (
            select(UserProperty)
            .where(UserProperty.user_id == user_id)
            .where(UserProperty.category == "CAR")
        )
        res = await session.execute(stmt)
        cars = list(res.scalars().all())

        if not cars:
            return (0, 0)

        max_bonus = 0
        max_reduction = 0
        for car in cars:
            item = cls.get_property_item(car.item_id)
            if item:
                if item.salary_bonus_pct > max_bonus:
                    max_bonus = item.salary_bonus_pct
                if item.cooldown_reduction_minutes > max_reduction:
                    max_reduction = item.cooldown_reduction_minutes

        return (max_bonus, max_reduction)


# Module-level convenience functions mirroring PropertyService classmethods
get_property_catalog = PropertyService.get_property_catalog
get_property_item = PropertyService.get_property_item
get_user_properties = PropertyService.get_user_properties
buy_property = PropertyService.buy_property
collect_rental_income = PropertyService.collect_rental_income
get_user_car_buffs = PropertyService.get_user_car_buffs
