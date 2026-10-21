"""Pytest shared test infrastructure, in-memory database, and offline bot fixtures."""

import datetime
import random
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User as TgUser
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.database.models import (
    Account,
    AdminLog,
    Base,
    Card,
    CashbackCategory,
    InvestmentPortfolio,
    LedgerEntry,
    SavingsAccount,
    Stock,
    Transaction,
    User,
    UserCashbackSelection,
)
from app.database.session import setup_sqlite_pragmas
from app.services.banking import BankingService


@pytest_asyncio.fixture(scope="function")
async def test_engine(tmp_path) -> AsyncEngine:
    """Provide a fresh, isolated SQLite database engine with WAL mode for each test."""
    db_file = tmp_path / f"test_{random.randint(1000, 999999)}.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    setup_sqlite_pragmas(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def async_session(test_engine: AsyncEngine):
    """Provide an isolated AsyncSession bound to the test engine."""
    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(scope="function")
async def db_session(async_session: AsyncSession):
    """Alias for async_session fixture for compatibility."""
    return async_session


@pytest.fixture
def mock_bot() -> AsyncMock:
    """Create an offline mock aiogram Bot instance conforming to Bot specifications."""
    bot = AsyncMock(spec=Bot)
    bot.id = 123456789

    # Dummy message returned by send_message and edit_message
    dummy_chat = Chat(id=123456789, type=ChatType.PRIVATE)
    dummy_user = TgUser(id=123456789, is_bot=True, first_name="T-Bank Bot", username="tbank_bot")
    dummy_message = MagicMock(spec=Message)
    dummy_message.message_id = 999
    dummy_message.date = datetime.datetime.now()
    dummy_message.chat = dummy_chat
    dummy_message.from_user = dummy_user
    dummy_message.text = "OK"

    # Make bot(...) directly return dummy_message for method.emit(bot)
    bot.return_value = dummy_message
    bot.send_message = AsyncMock(return_value=dummy_message)
    bot.edit_message_text = AsyncMock(return_value=dummy_message)
    bot.answer_callback_query = AsyncMock(return_value=True)

    return bot


@pytest.fixture
def fsm_storage() -> MemoryStorage:
    """In-memory FSM storage for state testing."""
    return MemoryStorage()


@pytest.fixture
def fsm_context_factory(mock_bot: AsyncMock, fsm_storage: MemoryStorage):
    """Factory creating FSMContext instances for testing state machines."""
    def _create(user_id: int, chat_id: int | None = None) -> FSMContext:
        key = StorageKey(
            bot_id=mock_bot.id,
            chat_id=chat_id or user_id,
            user_id=user_id,
        )
        return FSMContext(storage=fsm_storage, key=key)

    return _create


@pytest.fixture
def message_factory(mock_bot: AsyncMock):
    """Factory generating Message instances bound to mock_bot via .as_(mock_bot)."""
    def _create(
        text: str,
        user_id: int = 1001,
        username: str = "testuser",
        full_name: str = "Test User",
        chat_id: int | None = None,
    ) -> Message:
        actual_chat_id = chat_id or user_id
        chat = Chat(id=actual_chat_id, type=ChatType.PRIVATE)
        tg_user = TgUser(id=user_id, is_bot=False, first_name=full_name, username=username)
        msg = Message(
            message_id=random.randint(1000, 999999),
            date=datetime.datetime.now(),
            chat=chat,
            from_user=tg_user,
            text=text,
        )
        return msg.as_(mock_bot)

    return _create


@pytest.fixture
def callback_factory(mock_bot: AsyncMock, message_factory):
    """Factory generating CallbackQuery instances bound to mock_bot via .as_(mock_bot)."""
    def _create(
        data: str,
        user_id: int = 1001,
        username: str = "testuser",
        full_name: str = "Test User",
    ) -> CallbackQuery:
        msg = message_factory("Menu", user_id=user_id, username=username, full_name=full_name)
        tg_user = TgUser(id=user_id, is_bot=False, first_name=full_name, username=username)
        cb = CallbackQuery(
            id=f"cb_{random.randint(1000, 999999)}",
            from_user=tg_user,
            chat_instance="test_chat_instance",
            message=msg,
            data=data,
        )
        return cb.as_(mock_bot)

    return _create


@pytest_asyncio.fixture
async def sample_user(async_session: AsyncSession) -> User:
    """Create a standard registered customer with 10,000.00 ₽ balance."""
    user, acc, card = await BankingService.get_or_create_user(
        async_session,
        telegram_id=100001,
        username="alice",
        full_name="Alice Client",
        phone_number="+79991112233",
    )
    acc.balance = Decimal("10000.00")
    await async_session.commit()
    await async_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def sample_recipient(async_session: AsyncSession) -> User:
    """Create a second registered customer with 1,000.00 ₽ balance."""
    user, acc, card = await BankingService.get_or_create_user(
        async_session,
        telegram_id=100002,
        username="bob",
        full_name="Bob Recipient",
        phone_number="+79994445566",
    )
    acc.balance = Decimal("1000.00")
    await async_session.commit()
    await async_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def sample_admin(async_session: AsyncSession) -> User:
    """Create an administrator registered in ADMIN_IDS with 50,000.00 ₽ balance."""
    admin_id = 99999999
    if admin_id not in settings.ADMIN_IDS:
        settings.ADMIN_IDS.append(admin_id)

    user, acc, card = await BankingService.get_or_create_user(
        async_session,
        telegram_id=admin_id,
        username="admin_boss",
        full_name="Chief Admin",
    )
    acc.balance = Decimal("50000.00")
    await async_session.commit()
    await async_session.refresh(user)
    return user
