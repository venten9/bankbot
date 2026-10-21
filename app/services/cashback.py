"""Monthly Cashback Program Service: Category Selection, Spending Accrual, and Payout."""

import datetime
import random
import uuid
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    Account,
    CashbackCategory,
    Transaction,
    User,
    UserCashbackSelection,
)

DEFAULT_CASHBACK_CATEGORIES = [
    {
        "code": "FOOD_FASTFOOD",
        "name": "🌯 Шаурма и ночной дожор",
        "percent": 10,
        "icon": "🌯",
        "description": "Скидка на шаурму, беляши и ночные углеводные калории.",
    },
    {
        "code": "PETS_HEALTH",
        "name": "🐱 Котики и успокоительные",
        "percent": 15,
        "icon": "🐱",
        "description": "Премиум корм для пушистых и валерьянка для их владельцев.",
    },
    {
        "code": "COFFEE_CAFE",
        "name": "☕ Кофе и бессонница",
        "percent": 7,
        "icon": "☕",
        "description": "Топливо для выживания на бесконечных утренних созвонах.",
    },
    {
        "code": "TAXI_TRANSPORT",
        "name": "🚕 Такси 'не выходи из комнаты'",
        "percent": 5,
        "icon": "🚕",
        "description": "Комфортные поездки по делам и побег от реальности.",
    },
    {
        "code": "GAMING_DIGITAL",
        "name": "🎮 Игры и побег от реальности",
        "percent": 8,
        "icon": "🎮",
        "description": "Донаты, скины и виртуальные боевые достижения.",
    },
    {
        "code": "BOOKS_LEARNING",
        "name": "📚 Книги, которые никогда не прочтешь",
        "percent": 12,
        "icon": "📚",
        "description": "Красивые корешки для создания солидного фона в Zoom.",
    },
    {
        "code": "GROCERIES",
        "name": "🛒 Супермаркеты 'только за хлебушком'",
        "percent": 5,
        "icon": "🛒",
        "description": "Зашел за батоном хлеба, оставил в кассе 5000 рублей.",
    },
    {
        "code": "ALL_GENERAL",
        "name": "🍿 На всё вообще",
        "percent": 1,
        "icon": "🍿",
        "description": "Честный один процент на любые ваши повседневные траты.",
    },
]


class CashbackService:
    """Service providing monthly 4-of-8 category selection, accrual math, and 1-click payouts."""

    @classmethod
    async def init_cashback_categories(cls, session: AsyncSession) -> None:
        """Seed default 8 humorous categories if table is empty."""
        res = await session.execute(select(CashbackCategory))
        if res.scalars().first() is None:
            for cat_data in DEFAULT_CASHBACK_CATEGORIES:
                cat = CashbackCategory(**cat_data)
                session.add(cat)
            await session.flush()

    @classmethod
    async def get_available_categories(cls, session: AsyncSession) -> list[CashbackCategory]:
        """Fetch all available cashback categories."""
        await cls.init_cashback_categories(session)
        res = await session.execute(select(CashbackCategory).order_by(CashbackCategory.id))
        return list(res.scalars().all())

    @classmethod
    async def get_user_categories(
        cls, session: AsyncSession, user_id: int, month_year: str | None = None
    ) -> list[CashbackCategory]:
        """Get the active cashback categories selected by the user for the given month."""
        await cls.init_cashback_categories(session)
        cur_month = month_year or datetime.datetime.now().strftime("%Y-%m")

        query = (
            select(CashbackCategory)
            .join(
                UserCashbackSelection,
                UserCashbackSelection.category_id == CashbackCategory.id,
            )
            .where(UserCashbackSelection.user_id == user_id)
            .where(UserCashbackSelection.month_year == cur_month)
        )
        res = await session.execute(query)
        return list(res.scalars().all())

    @classmethod
    async def select_categories(
        cls,
        session: AsyncSession,
        user_id: int,
        category_ids: list[int],
        month_year: str | None = None,
    ) -> None:
        """Lock in up to 4 categories for the user for the current month."""
        await cls.init_cashback_categories(session)
        cur_month = month_year or datetime.datetime.now().strftime("%Y-%m")

        if len(category_ids) > 4:
            raise ValueError("Можно выбрать максимум 4 категории кэшбэка.")

        if len(set(category_ids)) != len(category_ids):
            raise ValueError("Категории не должны повторяться.")

        # Check if already locked for this month
        existing = await cls.get_user_categories(session, user_id, cur_month)
        if existing:
            raise ValueError(f"Категории на месяц {cur_month} уже зафиксированы и не могут быть изменены.")

        # Validate that all category IDs exist
        avail = await cls.get_available_categories(session)
        valid_ids = {c.id for c in avail}
        for cid in category_ids:
            if cid not in valid_ids:
                raise ValueError(f"Категория с ID {cid} не существует.")

        for cid in category_ids:
            selection = UserCashbackSelection(
                user_id=user_id,
                category_id=cid,
                month_year=cur_month,
            )
            session.add(selection)
        await session.flush()

    @classmethod
    async def calculate_cashback(
        cls,
        session: AsyncSession,
        user_id: int,
        amount: Decimal | float | int,
        category_code: str,
        month_year: str | None = None,
    ) -> Decimal:
        """Calculate cashback earned for a purchase in category_code."""
        dec_amount = Decimal(str(amount))
        if dec_amount <= Decimal("0.00"):
            return Decimal("0.00")

        active_cats = await cls.get_user_categories(session, user_id, month_year)
        active_by_code = {c.code: c for c in active_cats}

        # Matched category
        if category_code in active_by_code:
            percent = Decimal(str(active_by_code[category_code].percent))
            return (dec_amount * (percent / Decimal("100"))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        # Fallback to general category if active
        if "ALL_GENERAL" in active_by_code:
            return (dec_amount * Decimal("0.01")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        return Decimal("0.00")

    @classmethod
    async def accrue_cashback(
        cls,
        session: AsyncSession,
        user_id: int,
        amount: Decimal | float | int,
        category_code: str,
        month_year: str | None = None,
    ) -> Decimal:
        """Calculate cashback and credit it to user.cashback_balance."""
        earned = await cls.calculate_cashback(
            session, user_id, amount, category_code, month_year
        )
        if earned > Decimal("0.00"):
            user_res = await session.execute(select(User).where(User.id == user_id))
            user = user_res.scalars().first()
            if user:
                user.cashback_balance += earned
                await session.flush()
        return earned

    @classmethod
    async def payout_cashback(cls, session: AsyncSession, user_id: int) -> Decimal:
        """1-click payout of accumulated bonus rubles to user's Main card account."""
        await session.flush()
        user_res = await session.execute(select(User).where(User.id == user_id))
        user = user_res.scalars().first()
        if not user:
            raise ValueError(f"User {user_id} not found")

        bonus_amount = user.cashback_balance
        if bonus_amount < Decimal("1.00"):
            raise ValueError("У вас пока нет накопленного кэшбэка для вывода (минимум 1.00 ₽).")

        # 1. Atomic conditional deduction: CAS on user.cashback_balance with rowcount check
        cb_stmt = (
            update(User)
            .where(User.id == user.id)
            .where(User.cashback_balance == bonus_amount)
            .where(User.cashback_balance >= Decimal("1.00"))
            .values(cashback_balance=Decimal("0.00"))
        )
        cb_res = await session.execute(cb_stmt)
        if cb_res.rowcount == 0:
            raise ValueError("У вас пока нет накопленного кэшбэка для вывода (минимум 1.00 ₽).")

        # 2. Fetch main account
        main_res = await session.execute(
            select(Account)
            .where(Account.user_id == user_id)
            .where(Account.account_type == "MAIN")
        )
        main_acc = main_res.scalars().first()
        if not main_acc:
            raise ValueError(f"Main account for user {user_id} not found")

        # 3. Credit main account
        await session.execute(
            update(Account)
            .where(Account.id == main_acc.id)
            .values(balance=Account.balance + bonus_amount)
        )
        await session.refresh(user)

        # 3. Record transaction
        tx_id = str(uuid.uuid4())
        tx = Transaction(
            id=tx_id,
            tx_type="CASHBACK_PAYOUT",
            amount=bonus_amount,
            fee=Decimal("0.00"),
            status="COMPLETED",
            category="Кэшбэк",
            recipient_account_id=main_acc.id,
            description="Вывод кэшбэка на карту Т-Банк Black",
            fiscal_code=f"ФП-{random.randint(1000000000, 9999999999)}",
            auth_code=f"AUTH-{random.randint(100000, 999999)}",
        )
        session.add(tx)
        await session.flush()

        return bonus_amount
