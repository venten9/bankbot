"""SQLAlchemy Declarative Base and Database Models for T-Bank Neobank."""

import datetime
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative Base class for all ORM models."""
    pass


class User(Base):
    """User account model representing Telegram clients."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), default="Т-Банк Клиент", nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, nullable=True)
    cashback_balance: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=Decimal("0.00"), nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    accounts: Mapped[list["Account"]] = relationship(
        "Account", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    cards: Mapped[list["Card"]] = relationship(
        "Card", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    savings_account: Mapped["SavingsAccount | None"] = relationship(
        "SavingsAccount", back_populates="user", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )
    portfolio: Mapped[list["InvestmentPortfolio"]] = relationship(
        "InvestmentPortfolio", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    cashback_selections: Mapped[list["UserCashbackSelection"]] = relationship(
        "UserCashbackSelection", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    jobs: Mapped[list["UserJob"]] = relationship(
        "UserJob", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    properties: Mapped[list["UserProperty"]] = relationship(
        "UserProperty", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    last_hustle_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_daily_spin_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)


class Account(Base):
    """Relational account entity enforcing non-negative balance constraints."""

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("balance >= 0.00", name="chk_positive_balance"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    account_type: Mapped[str] = mapped_column(String(32), default="MAIN", nullable=False)  # MAIN, SAVINGS, CASHBACK, INVEST
    currency: Mapped[str] = mapped_column(String(3), default="RUB", nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=Decimal("0.00"), nullable=False)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="accounts")
    cards: Mapped[list["Card"]] = relationship("Card", back_populates="account", lazy="selectin")


class Card(Base):
    """Virtual debit card entity with 16-digit Luhn MIR card number and CVV."""

    __tablename__ = "cards"
    __table_args__ = (
        UniqueConstraint("card_number", name="uq_cards_card_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounts.id"), nullable=False)
    card_number: Mapped[str] = mapped_column(String(19), unique=True, index=True, nullable=False)
    cardholder_name: Mapped[str] = mapped_column(String(128), default="TBANK CLIENT", nullable=False)
    cvv: Mapped[str] = mapped_column(String(3), nullable=False)
    expiry_date: Mapped[str] = mapped_column(String(5), nullable=False)  # MM/YY
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)  # ACTIVE, FROZEN, BLOCKED
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="cards")
    account: Mapped["Account"] = relationship("Account", back_populates="cards")


class Transaction(Base):
    """Financial transaction entity supporting double-entry accounting and receipts."""

    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    tx_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=Decimal("0.00"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="COMPLETED", nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="Переводы", nullable=False)
    sender_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("accounts.id"), nullable=True)
    recipient_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("accounts.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    fiscal_code: Mapped[str] = mapped_column(String(64), nullable=False)
    auth_code: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(
        "LedgerEntry", back_populates="transaction", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def fiscal_sign(self) -> str:
        return self.fiscal_code

    @fiscal_sign.setter
    def fiscal_sign(self, value: str) -> None:
        self.fiscal_code = value


class LedgerEntry(Base):
    """Double-entry balanced ledger posting (Debit / Credit)."""

    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(String(36), ForeignKey("transactions.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(String(36), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # DEBIT or CREDIT
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    transaction: Mapped["Transaction"] = relationship("Transaction", back_populates="ledger_entries")

    @property
    def entry_type(self) -> str:
        return self.direction

    @entry_type.setter
    def entry_type(self, value: str) -> None:
        self.direction = value


class SavingsAccount(Base):
    """18% APR savings account entity with interest accumulation tracking."""

    __tablename__ = "savings_accounts"
    __table_args__ = (
        CheckConstraint("balance >= 0.00", name="chk_positive_savings_balance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("accounts.id"), nullable=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=Decimal("0.00"), nullable=False)
    annual_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.1800"), nullable=False)
    accrued_interest: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0.000000"), nullable=False)
    last_interest_calc: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="savings_account")

    @property
    def last_interest_accrual(self) -> datetime.datetime:
        return self.last_interest_calc

    @last_interest_accrual.setter
    def last_interest_accrual(self, value: datetime.datetime) -> None:
        self.last_interest_calc = value

    @property
    def accumulated_interest(self) -> Decimal:
        return self.accrued_interest

    @accumulated_interest.setter
    def accumulated_interest(self, value: Decimal) -> None:
        self.accrued_interest = value


class Stock(Base):
    """Parody stock asset for T-Investments market simulation."""

    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    volatility: Mapped[float] = mapped_column(Float, default=0.05, nullable=False)
    mean_reversion_rate: Mapped[float] = mapped_column(Float, default=0.05, nullable=False)
    sector: Mapped[str] = mapped_column(String(64), default="Разное", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )


class InvestmentPortfolio(Base):
    """User holdings in parody stocks with volume and cost basis."""

    __tablename__ = "investment_portfolios"
    __table_args__ = (
        UniqueConstraint("user_id", "stock_id", name="uq_user_stock"),
        CheckConstraint("shares >= 0", name="chk_positive_shares"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("stocks.id"), nullable=False)
    shares: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    average_buy_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=Decimal("0.00"), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="portfolio")
    stock: Mapped["Stock"] = relationship("Stock", lazy="selectin")

    @property
    def shares_count(self) -> int:
        return self.shares

    @shares_count.setter
    def shares_count(self, value: int) -> None:
        self.shares = value


class CashbackCategory(Base):
    """Humorous monthly cashback category entity."""

    __tablename__ = "cashback_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    percent: Mapped[int] = mapped_column(Integer, nullable=False)
    icon: Mapped[str] = mapped_column(String(16), default="💛", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    @property
    def rate(self) -> Decimal:
        return Decimal(str(self.percent)) / Decimal("100")


class UserCashbackSelection(Base):
    """User selected monthly cashback categories."""

    __tablename__ = "user_cashback_selections"
    __table_args__ = (
        UniqueConstraint("user_id", "category_id", "month_year", name="uq_user_cat_month"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    category_id: Mapped[int] = mapped_column(Integer, ForeignKey("cashback_categories.id"), nullable=False)
    month_year: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="cashback_selections")
    category: Mapped["CashbackCategory"] = relationship("CashbackCategory", lazy="selectin")

    @property
    def month(self) -> str:
        return self.month_year

    @month.setter
    def month(self, value: str) -> None:
        self.month_year = value


class AdminLog(Base):
    """Persistent audit log of administrative operations."""

    __tablename__ = "admin_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    target_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    @property
    def details(self) -> str:
        return self.reason

    @details.setter
    def details(self, value: str) -> None:
        self.reason = value


class UserJob(Base):
    """User employment model: active profession branch, level, shifts, and shift timers."""

    __tablename__ = "user_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "branch", name="uq_user_job_branch"),
        CheckConstraint("level >= 1 AND level <= 5", name="chk_user_job_level"),
        CheckConstraint("shifts_completed >= 0", name="chk_user_job_shifts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    branch: Mapped[str] = mapped_column(String(32), nullable=False)  # IT, SERVICE, FINANCE, SHADOW
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    shifts_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_on_shift: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    shift_started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_shift_completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="jobs")


class UserProperty(Base):
    """User property model: cars with buffs, rental real estate, and luxury collectibles."""

    __tablename__ = "user_properties"
    __table_args__ = (
        UniqueConstraint("user_id", "item_id", name="uq_user_property"),
        CheckConstraint("purchase_price >= 0.00", name="chk_user_property_price"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(32), index=True, nullable=False)  # CAR, REAL_ESTATE, LUXURY
    item_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    purchase_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    purchased_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    last_rent_collected_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="properties")

