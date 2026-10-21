"""T-Bank Daily Fortune Wheel Domain Service: 24h daily spin, cashback extra spins, and 8 prize sectors."""

import datetime
import random
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Account,
    InvestmentPortfolio,
    LedgerEntry,
    Stock,
    Transaction,
    User,
    UserJob,
)
from app.services.banking import BankingService
from app.services.receipts import ReceiptService
from app.services.transfers import InsufficientFundsError


@dataclass(frozen=True)
class WheelSector:
    """Definition of a sector on the Fortune Wheel."""

    sector_id: str
    title: str
    icon: str
    reward_type: str  # CASH, STOCKS, SHIFT_RESET, CASHBACK, TAX
    weight: int
    description: str


@dataclass
class WheelResult:
    """Result of spinning the Fortune Wheel."""

    sector_id: str
    title: str
    reward_type: str
    description: str
    icon: str
    balance: Decimal = Decimal("0.00")
    cashback_balance: Decimal = Decimal("0.00")
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def sector_name(self) -> str:
        return self.title

    @property
    def reward_description(self) -> str:
        return self.description


# 8 Wheel Sectors (Sum of weights = 100)
WHEEL_SECTORS: list[WheelSector] = [
    WheelSector(
        sector_id="cash_5k",
        title="💵 5 000 ₽",
        icon="💵",
        reward_type="CASH",
        weight=25,
        description="Выигрыш 5 000 ₽ зачислен на ваш счет!",
    ),
    WheelSector(
        sector_id="cash_25k",
        title="💰 25 000 ₽",
        icon="💰",
        reward_type="CASH",
        weight=15,
        description="Крупный выигрыш 25 000 ₽ зачислен на ваш счет!",
    ),
    WheelSector(
        sector_id="jackpot_100k",
        title="💎 Джекпот 100 000 ₽",
        icon="💎",
        reward_type="CASH",
        weight=5,
        description="ДЖЕКПОТ! 100 000 ₽ зачислены на ваш счет!",
    ),
    WheelSector(
        sector_id="stocks_shaurma",
        title="🌯 10 акций Шаурмы",
        icon="🌯",
        reward_type="STOCKS",
        weight=15,
        description="10 акций ПАО «Шаурма на вокзале» (SHAW) добавлены в ваш портфель!",
    ),
    WheelSector(
        sector_id="stocks_uspekh",
        title="📈 5 акций Успеха",
        icon="📈",
        reward_type="STOCKS",
        weight=15,
        description="5 акций ООО «Успешный успех» (USPEKH) добавлены в ваш портфель!",
    ),
    WheelSector(
        sector_id="energy_drink",
        title="⚡️ Энергетик Т-Драйв",
        icon="⚡️",
        reward_type="SHIFT_RESET",
        weight=10,
        description="Энергетик Т-Драйв сбросил таймер смены! Заберите зарплату немедленно!",
    ),
    WheelSector(
        sector_id="cashback_2k",
        title="🎁 +2 000 кэшбэка",
        icon="🎁",
        reward_type="CASHBACK",
        weight=10,
        description="+2 000 бонусных рублей начислены на ваш баланс кэшбэка!",
    ),
    WheelSector(
        sector_id="tax_luxury",
        title="🏦 Налог на роскошь",
        icon="🏦",
        reward_type="TAX",
        weight=5,
        description="Налог на роскошь от Т-Банка на развитие цифровой экосистемы.",
    ),
]

_SECTOR_MAP: dict[str, WheelSector] = {s.sector_id: s for s in WHEEL_SECTORS}
EXTRA_SPIN_CASHBACK_COST: Decimal = Decimal("1000.00")
FREE_SPIN_COOLDOWN_SECONDS: int = 86400  # 24 hours


class WheelService:
    """Fortune wheel domain service handling daily spins, extra spins, and probabilistic prize delivery."""

    EXTRA_SPIN_COST: Decimal = EXTRA_SPIN_CASHBACK_COST
    FREE_SPIN_COOLDOWN_SECONDS: int = FREE_SPIN_COOLDOWN_SECONDS

    @classmethod
    def get_wheel_sectors(cls) -> list[WheelSector]:
        """Return the list of all available wheel sectors."""
        return list(WHEEL_SECTORS)

    @classmethod
    async def can_spin_free(
        cls,
        user_or_session: Any,
        user_id: int | None = None,
        now: datetime.datetime | None = None,
    ) -> tuple[bool, int]:
        """Check if free spin is available. Returns (can_spin, seconds_remaining)."""
        current_time = now or datetime.datetime.now()

        if isinstance(user_or_session, User):
            user = user_or_session
        else:
            if user_id is None:
                raise ValueError("user_id required when passing session")
            session: AsyncSession = user_or_session
            res = await session.execute(select(User).where(User.id == user_id))
            user = res.scalar_one_or_none()

        if not user:
            return (False, cls.FREE_SPIN_COOLDOWN_SECONDS)

        if user.last_daily_spin_at is None:
            return (True, 0)

        elapsed = (current_time - user.last_daily_spin_at).total_seconds()
        if elapsed >= cls.FREE_SPIN_COOLDOWN_SECONDS:
            return (True, 0)

        remaining = max(1, int(cls.FREE_SPIN_COOLDOWN_SECONDS - elapsed))
        return (False, remaining)

    @classmethod
    async def spin_wheel(
        cls,
        session: AsyncSession,
        user_id: int,
        is_extra: bool = False,
        sector_override: str | None = None,
        now: datetime.datetime | None = None,
    ) -> WheelResult:
        """Spin the fortune wheel, deducting cost / verifying cooldown and atomically granting prizes."""
        current_time = now or datetime.datetime.now()

        # 1. Fetch user
        user_res = await session.execute(select(User).where(User.id == user_id))
        user = user_res.scalar_one_or_none()
        if not user:
            raise ValueError(f"Пользователь {user_id} не найден")

        # 2. Enforce spin conditions
        if not is_extra:
            # Free spin: 24h atomic CAS on last_daily_spin_at
            cutoff = current_time - datetime.timedelta(seconds=cls.FREE_SPIN_COOLDOWN_SECONDS)
            free_spin_stmt = (
                update(User)
                .where(User.id == user.id)
                .where(
                    (User.last_daily_spin_at.is_(None)) | (User.last_daily_spin_at <= cutoff)
                )
                .values(last_daily_spin_at=current_time)
            )
            free_res = await session.execute(free_spin_stmt)
            if free_res.rowcount == 0:
                elapsed = (
                    (current_time - user.last_daily_spin_at).total_seconds()
                    if user.last_daily_spin_at
                    else 0
                )
                rem = max(1, int(cls.FREE_SPIN_COOLDOWN_SECONDS - elapsed))
                hours = rem // 3600
                mins = (rem % 3600) // 60
                raise ValueError(
                    f"Бесплатный спин еще недоступен! До следующего спина: {hours} ч {mins} мин."
                )
            await session.refresh(user)
        else:
            # Extra spin: atomic CAS deduction of 1 000 cashback rubles
            extra_spin_stmt = (
                update(User)
                .where(User.id == user.id)
                .where(User.cashback_balance >= cls.EXTRA_SPIN_COST)
                .values(cashback_balance=User.cashback_balance - cls.EXTRA_SPIN_COST)
            )
            extra_res = await session.execute(extra_spin_stmt)
            if extra_res.rowcount == 0:
                raise InsufficientFundsError(
                    f"Недостаточно бонусных рублей кэшбэка для покупки спина. "
                    f"Требуется: {cls.EXTRA_SPIN_COST:,.2f} ₽, на балансе: {user.cashback_balance:,.2f} ₽"
                )
            await session.refresh(user)

        # 3. Pick sector
        if sector_override:
            clean_sec = sector_override.strip().lower()
            if clean_sec not in _SECTOR_MAP:
                raise ValueError(f"Неизвестный сектор колеса: '{sector_override}'")
            sector = _SECTOR_MAP[clean_sec]
        else:
            weights = [s.weight for s in WHEEL_SECTORS]
            chosen_sectors = random.choices(WHEEL_SECTORS, weights=weights, k=1)
            sector = chosen_sectors[0]

        # 4. Deliver sector reward
        main_acc = await BankingService.get_user_main_account(session, user_id)
        if not main_acc:
            raise ValueError(f"Основной банковский счет пользователя {user_id} не найден")

        result_desc = sector.description

        if sector.reward_type == "CASH":
            cash_amounts = {
                "cash_5k": Decimal("5000.00"),
                "cash_25k": Decimal("25000.00"),
                "jackpot_100k": Decimal("100000.00"),
            }
            amount = cash_amounts.get(sector.sector_id, Decimal("5000.00"))

            # Atomic credit to main account
            await session.execute(
                update(Account)
                .where(Account.id == main_acc.id)
                .values(balance=Account.balance + amount)
            )
            new_bal = (
                await session.execute(select(Account.balance).where(Account.id == main_acc.id))
            ).scalar_one()

            tx_id = f"whl-{uuid.uuid4().hex[:12]}"
            tx = Transaction(
                id=tx_id,
                sender_account_id=None,
                recipient_account_id=main_acc.id,
                amount=amount,
                tx_type="WHEEL_PRIZE",
                category="Призы и подарки",
                description=f"Выигрыш в Колесе Фортуны: {amount:,.2f} ₽",
                status="COMPLETED",
                fiscal_code=ReceiptService.generate_fiscal_code(),
                auth_code=ReceiptService.generate_auth_code(),
                created_at=current_time,
            )
            ledger_entry = LedgerEntry(
                transaction_id=tx_id,
                account_id=main_acc.id,
                direction="CREDIT",
                amount=amount,
                balance_after=new_bal,
                created_at=current_time,
            )
            session.add(tx)
            session.add(ledger_entry)
            result_desc = f"🎉 <b>Вы выиграли {amount:,.2f} ₽!</b> Средства зачислены на ваш счет."

        elif sector.reward_type == "STOCKS":
            if sector.sector_id == "stocks_shaurma":
                ticker = "SHAW"
                shares_count = 10
            else:
                ticker = "USPEKH"
                shares_count = 5

            # Find or insert stock in SQLite DB (without mutating DEFAULT_STOCKS in investments.py)
            stock_res = await session.execute(select(Stock).where(Stock.ticker == ticker))
            stock = stock_res.scalar_one_or_none()
            if not stock:
                if ticker == "SHAW":
                    stock = Stock(
                        ticker="SHAW",
                        name='ПАО "Шаурма на вокзале"',
                        base_price=Decimal("150.00"),
                        current_price=Decimal("150.00"),
                        volatility=0.08,
                        mean_reversion_rate=0.05,
                        sector="Потребительский",
                        description="Крупнейшая сеть вокзального питания.",
                    )
                else:
                    stock = Stock(
                        ticker="USPEKH",
                        name='ООО "Успешный успех"',
                        base_price=Decimal("100.00"),
                        current_price=Decimal("100.00"),
                        volatility=0.10,
                        mean_reversion_rate=0.05,
                        sector="Инфобизнес",
                        description="Акции курсов успешного успеха. Продают воздух с гарантией 100%.",
                    )
                session.add(stock)
                await session.flush()

            # Find or insert portfolio entry
            port_res = await session.execute(
                select(InvestmentPortfolio).where(
                    InvestmentPortfolio.user_id == user_id,
                    InvestmentPortfolio.stock_id == stock.id,
                )
            )
            port = port_res.scalar_one_or_none()
            if port:
                port.shares += shares_count
            else:
                port = InvestmentPortfolio(
                    user_id=user_id,
                    stock_id=stock.id,
                    shares=shares_count,
                    average_buy_price=stock.current_price,
                )
                session.add(port)

            tx_id = f"whl-{uuid.uuid4().hex[:12]}"
            tx = Transaction(
                id=tx_id,
                sender_account_id=None,
                recipient_account_id=None,
                amount=stock.current_price * shares_count,
                tx_type="WHEEL_PRIZE",
                category="Призы и подарки",
                description=f"Выигрыш в Колесе Фортуны: {shares_count} акций {stock.ticker}",
                status="COMPLETED",
                fiscal_code=ReceiptService.generate_fiscal_code(),
                auth_code=ReceiptService.generate_auth_code(),
                created_at=current_time,
            )
            session.add(tx)
            result_desc = (
                f"📈 <b>Подарочные акции!</b> Вам начислено <b>{shares_count} акций</b> "
                f"«{stock.name}» ({stock.ticker}) в ваш инвестиционный портфель!"
            )

        elif sector.reward_type == "SHIFT_RESET":
            # Energy drink: reset shift wait time for active jobs currently on shift
            job_stmt = select(UserJob).where(
                UserJob.user_id == user_id,
                UserJob.is_active == True,  # noqa: E712
                UserJob.is_on_shift == True,  # noqa: E712
            )
            jobs_res = await session.execute(job_stmt)
            active_jobs = list(jobs_res.scalars().all())

            for j in active_jobs:
                # Pre-advance shift_started_at by 2 hours into past, enabling instant salary claim
                j.shift_started_at = current_time - datetime.timedelta(seconds=7200)

            if active_jobs:
                result_desc = (
                    f"⚡️ <b>Энергетик Т-Драйв подействовал!</b> Смена ({len(active_jobs)} раб.) "
                    f"завершена досрочно. Можете забрать зарплату прямо сейчас!"
                )
            else:
                result_desc = (
                    "⚡️ <b>Энергетик Т-Драйв выпит!</b> Вы полны сил и бодрости. "
                    "Устройтесь на работу или начните новую смену!"
                )

        elif sector.reward_type == "CASHBACK":
            # Bonus cashback
            bonus_amount = Decimal("2000.00")
            await session.execute(
                update(User)
                .where(User.id == user.id)
                .values(cashback_balance=User.cashback_balance + bonus_amount)
            )
            await session.refresh(user)
            result_desc = (
                f"🎁 <b>Кэшбэк-бонус!</b> На ваш бонусный баланс начислено <b>+{bonus_amount:,.2f} ₽</b>."
            )

        elif sector.reward_type == "TAX":
            # Comedic luxury tax
            tax_amount = Decimal("500.00")
            tax_stmt = (
                update(Account)
                .where(Account.id == main_acc.id)
                .where(Account.balance >= tax_amount)
                .where(Account.is_frozen == False)  # noqa: E712
                .values(balance=Account.balance - tax_amount)
            )
            tax_res = await session.execute(tax_stmt)
            if tax_res.rowcount > 0:
                new_bal = (
                    await session.execute(select(Account.balance).where(Account.id == main_acc.id))
                ).scalar_one()

                tx_id = f"whl-{uuid.uuid4().hex[:12]}"
                tx = Transaction(
                    id=tx_id,
                    sender_account_id=main_acc.id,
                    recipient_account_id=None,
                    amount=tax_amount,
                    tx_type="WHEEL_TAX",
                    category="Комиссии и налоги",
                    description="Налог на роскошь от Т-Банка (Колесо Фортуны)",
                    status="COMPLETED",
                    fiscal_code=ReceiptService.generate_fiscal_code(),
                    auth_code=ReceiptService.generate_auth_code(),
                    created_at=current_time,
                )
                ledger_entry = LedgerEntry(
                    transaction_id=tx_id,
                    account_id=main_acc.id,
                    direction="DEBIT",
                    amount=tax_amount,
                    balance_after=new_bal,
                    created_at=current_time,
                )
                session.add(tx)
                session.add(ledger_entry)
                result_desc = (
                    "🏦 <b>Налог на роскошь!</b> С вашего счета удержано <b>500.00 ₽</b> "
                    "в пользу фонда озеленения дата-центров Т-Банка."
                )
            else:
                result_desc = (
                    "🏦 <b>Налоговая Т-Банка проявила милосердие!</b> "
                    "На вашем счете менее 500 ₽, поэтому налог на роскошь списан в размере <b>0.00 ₽</b>."
                )

        # 5. Refresh authoritative balances and commit/flush
        await session.flush()
        refreshed_bal = (
            await session.execute(select(Account.balance).where(Account.id == main_acc.id))
        ).scalar_one()
        await session.refresh(user)

        return WheelResult(
            sector_id=sector.sector_id,
            title=sector.title,
            reward_type=sector.reward_type,
            description=result_desc,
            icon=sector.icon,
            balance=refreshed_bal,
            cashback_balance=user.cashback_balance,
            details={
                "is_extra": is_extra,
                "reward_type": sector.reward_type,
                "sector_id": sector.sector_id,
            },
        )


# Module-level convenience functions mirroring WheelService classmethods
get_wheel_sectors = WheelService.get_wheel_sectors
can_spin_free = WheelService.can_spin_free
spin_wheel = WheelService.spin_wheel
