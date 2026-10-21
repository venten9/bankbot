"""Savings Account Service: 18% APR Interest Accrual Math and Atomic Safe Management."""

import datetime
import random
import uuid
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Account, LedgerEntry, SavingsAccount, Transaction
from app.services.transfers import AccountFrozenError, InsufficientFundsError


class SavingsService:
    """Service providing 18% APR savings safe management, interest calculation, and payouts."""

    @classmethod
    async def get_or_create_savings(cls, session: AsyncSession, user_id: int) -> SavingsAccount:
        """Fetch or initialize a 18% APR savings safe for the user."""
        res = await session.execute(
            select(SavingsAccount).where(SavingsAccount.user_id == user_id)
        )
        savings = res.scalars().first()
        if not savings:
            savings = SavingsAccount(
                user_id=user_id,
                balance=Decimal("0.00"),
                annual_rate=Decimal(str(settings.SAVINGS_APR)),
                accrued_interest=Decimal("0.000000"),
                last_interest_calc=datetime.datetime.now(),
            )
            session.add(savings)
            await session.flush()
        return savings

    @classmethod
    async def get_savings(cls, session: AsyncSession, user_id: int) -> SavingsAccount:
        """Alias to get_or_create_savings."""
        return await cls.get_or_create_savings(session, user_id)

    @staticmethod
    def calculate_interest(
        balance: Decimal,
        annual_rate: Decimal,
        elapsed_seconds: float,
    ) -> Decimal:
        """Calculate accrued interest with high precision: Balance * (APR / 365) * (delta_t / 86400)."""
        if balance <= Decimal("0.00") or elapsed_seconds <= 0:
            return Decimal("0.000000")

        daily_rate = annual_rate / Decimal("365")
        time_fraction = Decimal(str(elapsed_seconds)) / Decimal("86400")
        accrued = balance * daily_rate * time_fraction
        return accrued.quantize(Decimal("0.000001"))

    @classmethod
    def calculate_accrued_interest(
        cls, savings: SavingsAccount, as_of: datetime.datetime | None = None
    ) -> Decimal:
        """Calculate interest accrued since last_interest_calc without mutating database."""
        now = as_of or datetime.datetime.now()
        elapsed = (now - savings.last_interest_calc).total_seconds()
        return cls.calculate_interest(savings.balance, savings.annual_rate, elapsed)

    @classmethod
    async def accrue_interest(
        cls, session: AsyncSession, user_id: int, as_of: datetime.datetime | None = None
    ) -> Decimal:
        """Accrue interest into savings.accrued_interest and update last_interest_calc."""
        savings = await cls.get_or_create_savings(session, user_id)
        now = as_of or datetime.datetime.now()
        delta = cls.calculate_accrued_interest(savings, as_of=now)
        if delta > Decimal("0.000000"):
            # Update atomically in SQL to prevent dirty in-memory overwrites during concurrent operations
            await session.execute(
                update(SavingsAccount)
                .where(SavingsAccount.id == savings.id)
                .values(
                    accrued_interest=SavingsAccount.accrued_interest + delta,
                    last_interest_calc=now,
                )
            )
            await session.refresh(savings)
        return savings.accrued_interest

    @classmethod
    async def deposit_to_savings(
        cls, session: AsyncSession, user_id: int, amount: Decimal | float | int
    ) -> Transaction:
        """Atomically transfer funds from Main card account to Savings safe."""
        dec_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        if dec_amount <= Decimal("0.00"):
            raise ValueError(f"Сумма пополнения должна быть больше 0: {dec_amount}")

        # 1. Accrue pending interest up to this moment
        now = datetime.datetime.now()
        await cls.accrue_interest(session, user_id, as_of=now)

        # 2. Fetch main account and savings
        main_res = await session.execute(
            select(Account)
            .where(Account.user_id == user_id)
            .where(Account.account_type == "MAIN")
        )
        main_acc = main_res.scalars().first()
        if not main_acc:
            raise ValueError(f"Main account for user {user_id} not found")

        if main_acc.is_frozen:
            raise AccountFrozenError("Счет заморожен. Пополнение сейфа недоступно.")

        # 3. Conditional debit from main account
        debit_stmt = (
            update(Account)
            .where(Account.id == main_acc.id)
            .where(Account.balance >= dec_amount)
            .where(Account.is_frozen == False)  # noqa: E712
            .values(balance=Account.balance - dec_amount)
        )
        debit_res = await session.execute(debit_stmt)
        if debit_res.rowcount == 0:
            raise InsufficientFundsError(
                f"Недостаточно средств на карте. Доступно: {main_acc.balance} ₽, требуется: {dec_amount} ₽."
            )

        # 4. Credit savings safe
        savings = await cls.get_or_create_savings(session, user_id)
        savings.balance += dec_amount
        await session.flush()

        # 5. Create transaction record
        tx_id = str(uuid.uuid4())
        tx = Transaction(
            id=tx_id,
            tx_type="SAVINGS_DEPOSIT",
            amount=dec_amount,
            fee=Decimal("0.00"),
            status="COMPLETED",
            category="Накопления",
            sender_account_id=main_acc.id,
            description="Перевод на накопительный счет",
            fiscal_code=f"ФП-{random.randint(1000000000, 9999999999)}",
            auth_code=f"AUTH-{random.randint(100000, 999999)}",
        )
        session.add(tx)
        await session.flush()
        return tx

    @classmethod
    async def withdraw_from_savings(
        cls, session: AsyncSession, user_id: int, amount: Decimal | float | int
    ) -> Transaction:
        """Atomically withdraw funds from Savings safe back to Main card account."""
        dec_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        if dec_amount <= Decimal("0.00"):
            raise ValueError(f"Сумма вывода должна быть больше 0: {dec_amount}")

        # 1. Accrue pending interest up to now
        now = datetime.datetime.now()
        await cls.accrue_interest(session, user_id, as_of=now)

        # 2. Fetch savings and main account
        savings = await cls.get_or_create_savings(session, user_id)
        if savings.balance < dec_amount:
            raise InsufficientFundsError(
                f"Недостаточно средств на накопительном счете. Доступно: {savings.balance} ₽, требуется: {dec_amount} ₽."
            )

        main_res = await session.execute(
            select(Account)
            .where(Account.user_id == user_id)
            .where(Account.account_type == "MAIN")
        )
        main_acc = main_res.scalars().first()
        if not main_acc:
            raise ValueError(f"Main account for user {user_id} not found")

        # 3. Atomic deduction from savings and addition to main
        savings.balance -= dec_amount
        await session.execute(
            update(Account)
            .where(Account.id == main_acc.id)
            .values(balance=Account.balance + dec_amount)
        )
        await session.flush()

        # 4. Create transaction record
        tx_id = str(uuid.uuid4())
        tx = Transaction(
            id=tx_id,
            tx_type="SAVINGS_WITHDRAWAL",
            amount=dec_amount,
            fee=Decimal("0.00"),
            status="COMPLETED",
            category="Накопления",
            recipient_account_id=main_acc.id,
            description="Вывод с накопительного счета",
            fiscal_code=f"ФП-{random.randint(1000000000, 9999999999)}",
            auth_code=f"AUTH-{random.randint(100000, 999999)}",
        )
        session.add(tx)
        await session.flush()
        return tx

    @classmethod
    async def payout_interest(
        cls, session: AsyncSession, user_id: int, as_of: datetime.datetime | None = None
    ) -> Decimal:
        """Payout accrued interest to the savings account balance in an atomic transaction."""
        now = as_of or datetime.datetime.now()
        await cls.accrue_interest(session, user_id, as_of=now)
        savings = await cls.get_or_create_savings(session, user_id)
        await session.refresh(savings)

        # Round accrued to kopecks using HALF_UP
        payout_amount = savings.accrued_interest.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if payout_amount < Decimal("0.01"):
            return Decimal("0.00")

        # Atomic conditional deduction: only succeed if accrued_interest has not been claimed
        # Since payout_amount is rounded with HALF_UP, accrued_interest is >= payout_amount - 0.005
        min_accrued = payout_amount - Decimal("0.005")
        payout_stmt = (
            update(SavingsAccount)
            .where(SavingsAccount.id == savings.id)
            .where(SavingsAccount.accrued_interest >= min_accrued)
            .values(
                accrued_interest=Decimal("0.000000"),
                balance=SavingsAccount.balance + payout_amount,
                last_interest_calc=now,
            )
        )
        payout_res = await session.execute(payout_stmt)
        if payout_res.rowcount == 0:
            # Another concurrent transaction already claimed the accrued interest
            return Decimal("0.00")

        # Refresh in-memory ORM instance so identity map reflects new balance and accumulator
        await session.refresh(savings)

        # Record payout transaction
        tx_id = str(uuid.uuid4())
        tx = Transaction(
            id=tx_id,
            tx_type="INTEREST_PAYOUT",
            amount=payout_amount,
            fee=Decimal("0.00"),
            status="COMPLETED",
            category="Накопления",
            description="Выплата процентов по накопительному счету (18% годовых)",
            fiscal_code=f"ФП-{random.randint(1000000000, 9999999999)}",
            auth_code=f"AUTH-{random.randint(100000, 999999)}",
        )
        session.add(tx)
        await session.flush()

        return payout_amount
