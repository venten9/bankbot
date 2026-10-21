"""Adversarial Concurrency Stress Tests for Property and Fortune Wheel.

Challenges:
1. Concurrency stress on `buy_property`: Attempt concurrent duplicate purchases of the
   same property item with sufficient and borderline funds. Ensure exactly one succeeds,
   balance is decremented exactly once, and zero overdraft occurs.
2. Concurrency stress on `collect_rental_income`: Attempt concurrent simultaneous rent claims.
   Ensure rent is awarded exactly once per 24 hours.
3. Concurrency stress on `spin_wheel`: Attempt concurrent free spins across multiple
   simultaneous tasks. Ensure exactly one free spin succeeds within 24 hours.
4. Concurrency stress on extra wheel spins: Borderline cashback balance (e.g. 1500 points).
   Attempt concurrent extra spins; verify only 1 succeeds and 500 remains.
"""

import asyncio
import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import Account, LedgerEntry, Transaction, User, UserProperty
from app.services.banking import BankingService
from app.services.property import PropertyService
from app.services.transfers import InsufficientFundsError
from app.services.wheel import WheelService


class TestPropertyAndWheelConcurrencyStress:
    """Adversarial stress testing of atomicity, double-claim prevention, and balance invariants."""

    @pytest.mark.asyncio
    async def test_concurrent_buy_property_duplicate_item_sufficient_funds(self, test_engine):
        """
        Scenario:
        - User has 100,000.00 ₽ (sufficient to buy 'vaz2107' which costs 50,000.00 ₽ twice if not guarded).
        - 10 concurrent tasks attempt to buy 'vaz2107' simultaneously.
        Invariants:
        - Exactly 1 purchase succeeds.
        - User owns exactly 1 'vaz2107'.
        - Account balance is debited exactly once (50,000.00 ₽ remaining).
        - Zero overdraft occurs.
        - Exactly 1 PROPERTY_BUY transaction and 1 DEBIT ledger entry created.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=910001, username="prop_buyer_stress", full_name="Buyer Stress"
            )
            acc.balance = Decimal("100000.00")
            await session.commit()
            user_id = user.id
            acc_id = acc.id

        async def buy_task():
            async with session_factory() as session:
                try:
                    await PropertyService.buy_property(session, user_id=user_id, item_id="vaz2107")
                    await session.commit()
                    return "SUCCESS"
                except (ValueError, InsufficientFundsError) as e:
                    await session.rollback()
                    return f"HANDLED_ERROR: {type(e).__name__}"
                except IntegrityError as e:
                    await session.rollback()
                    return f"INTEGRITY_ERROR: {type(e).__name__}"
                except OperationalError as e:
                    await session.rollback()
                    return f"LOCK_ERROR: {type(e).__name__}"
                except Exception as e:
                    await session.rollback()
                    return f"UNEXPECTED: {type(e).__name__}"

        # Execute 10 concurrent buy attempts
        tasks = [buy_task() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        success_count = results.count("SUCCESS")
        assert success_count == 1, f"Expected exactly 1 purchase to succeed, got {success_count}. Results: {results}"

        async with session_factory() as session:
            final_acc = (await session.execute(select(Account).where(Account.id == acc_id))).scalar_one()
            assert final_acc.balance == Decimal("50000.00"), f"Expected 50000.00 ₽, got {final_acc.balance}"

            user_props = (await session.execute(select(UserProperty).where(UserProperty.user_id == user_id))).scalars().all()
            assert len(user_props) == 1
            assert user_props[0].item_id == "vaz2107"

            txs = (await session.execute(
                select(Transaction).where(Transaction.sender_account_id == acc_id, Transaction.tx_type == "PROPERTY_BUY")
            )).scalars().all()
            assert len(txs) == 1

            ledgers = (await session.execute(
                select(LedgerEntry).where(LedgerEntry.account_id == acc_id, LedgerEntry.direction == "DEBIT")
            )).scalars().all()
            assert len(ledgers) == 1
            assert ledgers[0].balance_after == Decimal("50000.00")

    @pytest.mark.asyncio
    async def test_concurrent_buy_property_borderline_funds(self, test_engine):
        """
        Scenario:
        - User has exactly 50,000.00 ₽ (exact cost of 'vaz2107').
        - 10 concurrent tasks attempt to buy 'vaz2107' simultaneously.
        Invariants:
        - Exactly 1 purchase succeeds.
        - Balance is exactly 0.00 ₽ (never negative).
        - Exactly 1 'vaz2107' owned.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=910002, username="prop_border_stress", full_name="Borderline Buyer"
            )
            acc.balance = Decimal("50000.00")
            await session.commit()
            user_id = user.id
            acc_id = acc.id

        async def buy_task():
            async with session_factory() as session:
                try:
                    await PropertyService.buy_property(session, user_id=user_id, item_id="vaz2107")
                    await session.commit()
                    return "SUCCESS"
                except (ValueError, InsufficientFundsError):
                    await session.rollback()
                    return "INSUFFICIENT_OR_ALREADY_OWNED"
                except (IntegrityError, OperationalError):
                    await session.rollback()
                    return "DB_REJECTED"

        tasks = [buy_task() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        success_count = results.count("SUCCESS")
        assert success_count == 1, f"Expected exactly 1 success, got {success_count}. Results: {results}"

        async with session_factory() as session:
            final_acc = (await session.execute(select(Account).where(Account.id == acc_id))).scalar_one()
            assert final_acc.balance == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_concurrent_buy_different_properties_competing_funds(self, test_engine):
        """
        Scenario:
        - User has 60,000.00 ₽.
        - Tries to buy 'vaz2107' (50,000 ₽) and 'hostel' (30,000 ₽) concurrently.
        - Total requested = 80,000 ₽ > 60,000 ₽ balance.
        Invariants:
        - Exactly 1 purchase succeeds.
        - Exactly 1 purchase fails with InsufficientFundsError.
        - Zero overdraft occurs. Final balance >= 0.00 ₽.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=910006, username="prop_compete_buyer", full_name="Competing Buyer"
            )
            acc.balance = Decimal("60000.00")
            await session.commit()
            user_id = user.id
            acc_id = acc.id

        async def buy(item_id):
            async with session_factory() as session:
                try:
                    await PropertyService.buy_property(session, user_id=user_id, item_id=item_id)
                    await session.commit()
                    return f"SUCCESS_{item_id}"
                except InsufficientFundsError:
                    await session.rollback()
                    return f"INSUFFICIENT_{item_id}"
                except Exception as e:
                    await session.rollback()
                    return f"ERR_{type(e).__name__}"

        tasks = [buy("vaz2107"), buy("hostel")]
        results = await asyncio.gather(*tasks)

        successes = [r for r in results if r.startswith("SUCCESS")]
        failures = [r for r in results if r.startswith("INSUFFICIENT")]

        assert len(successes) == 1, f"Expected 1 success, got {results}"
        assert len(failures) == 1, f"Expected 1 insufficient funds failure, got {results}"

        async with session_factory() as session:
            final_acc = (await session.execute(select(Account).where(Account.id == acc_id))).scalar_one()
            assert final_acc.balance >= Decimal("0.00")
            if "SUCCESS_vaz2107" in successes:
                assert final_acc.balance == Decimal("10000.00")
            else:
                assert final_acc.balance == Decimal("30000.00")

    @pytest.mark.asyncio
    async def test_concurrent_collect_rental_income_double_claim_prevention(self, test_engine):
        """
        Scenario:
        - User owns a 'hostel' (daily rent: 500.00 ₽).
        - Rent was last collected 25 hours ago (eligible for 1 collection).
        - 10 concurrent tasks attempt to collect rental income simultaneously.
        Invariants:
        - Rent is credited exactly once (+500.00 ₽).
        - Balance increases by exactly 500.00 ₽.
        - Exactly 1 RENTAL_INCOME transaction is recorded.
        - Exactly 1 CREDIT ledger entry is recorded.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
        base_time = datetime.datetime.now() - datetime.timedelta(hours=25)
        now_time = datetime.datetime.now()

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=910003, username="landlord_stress", full_name="Landlord Stress"
            )
            acc.balance = Decimal("1000.00")
            prop = UserProperty(
                user_id=user.id,
                category="REAL_ESTATE",
                item_id="hostel",
                purchase_price=Decimal("30000.00"),
                purchased_at=base_time,
                last_rent_collected_at=base_time,
            )
            session.add(prop)
            await session.commit()
            user_id = user.id
            acc_id = acc.id

        async def collect_task():
            async with session_factory() as session:
                try:
                    rent_amt, wait_sec, msg = await PropertyService.collect_rental_income(
                        session, user_id=user_id, now=now_time
                    )
                    await session.commit()
                    if rent_amt > Decimal("0.00"):
                        return f"RENT_{rent_amt}"
                    return "NO_RENT_READY"
                except OperationalError:
                    await session.rollback()
                    return "LOCK_ERROR"
                except Exception as e:
                    await session.rollback()
                    return f"ERROR: {type(e).__name__}"

        tasks = [collect_task() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        rent_awarded = [r for r in results if r == "RENT_500.00"]
        assert len(rent_awarded) == 1, (
            f"Rent must be awarded exactly once! Got {len(rent_awarded)} awards: {results}"
        )

        async with session_factory() as session:
            final_acc = (await session.execute(select(Account).where(Account.id == acc_id))).scalar_one()
            assert final_acc.balance == Decimal("1500.00"), (
                f"Expected final balance 1500.00 ₽, got {final_acc.balance}"
            )

            tx_count = (await session.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.recipient_account_id == acc_id,
                    Transaction.tx_type == "RENTAL_INCOME",
                )
            )).scalar_one()
            assert tx_count == 1, f"Expected 1 RENTAL_INCOME transaction, got {tx_count}"

            ledger_count = (await session.execute(
                select(func.count(LedgerEntry.id)).where(
                    LedgerEntry.account_id == acc_id,
                    LedgerEntry.direction == "CREDIT",
                )
            )).scalar_one()
            assert ledger_count == 1, f"Expected 1 CREDIT ledger entry, got {ledger_count}"

    @pytest.mark.asyncio
    async def test_concurrent_free_wheel_spins_double_claim_prevention(self, test_engine):
        """
        Scenario:
        - User is eligible for free daily spin (last_daily_spin_at is None).
        - 10 concurrent tasks attempt free spin simultaneously.
        Invariants:
        - Exactly 1 free spin succeeds.
        - Exactly 9 tasks fail with ValueError (spin on cooldown).
        - `last_daily_spin_at` is set.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
        now_time = datetime.datetime.now()

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=910004, username="free_spinner_stress", full_name="Free Spinner"
            )
            acc.balance = Decimal("0.00")
            user.last_daily_spin_at = None
            await session.commit()
            user_id = user.id

        async def free_spin_task():
            async with session_factory() as session:
                try:
                    res = await WheelService.spin_wheel(
                        session, user_id=user_id, is_extra=False, sector_override="cash_5k", now=now_time
                    )
                    await session.commit()
                    return "SUCCESS"
                except ValueError as e:
                    await session.rollback()
                    return "ON_COOLDOWN"
                except OperationalError:
                    await session.rollback()
                    return "LOCK_ERROR"
                except Exception as e:
                    await session.rollback()
                    return f"ERROR: {type(e).__name__}"

        tasks = [free_spin_task() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        success_count = results.count("SUCCESS")
        assert success_count == 1, f"Expected exactly 1 free spin success, got {success_count}. Results: {results}"
        assert results.count("ON_COOLDOWN") == 9

        async with session_factory() as session:
            db_user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
            assert db_user.last_daily_spin_at is not None

    @pytest.mark.asyncio
    async def test_concurrent_extra_wheel_spins_borderline_cashback(self, test_engine):
        """
        Scenario:
        - User has 1,500.00 cashback rubles (cost per extra spin is 1,000.00).
        - 10 concurrent tasks attempt extra spins simultaneously.
        Invariants:
        - Exactly 1 extra spin succeeds (costs 1,000.00).
        - Remaining cashback balance is exactly 500.00 (not negative).
        - Exactly 9 tasks fail with InsufficientFundsError.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
        now_time = datetime.datetime.now()

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=910005, username="extra_spinner_stress", full_name="Extra Spinner"
            )
            user.cashback_balance = Decimal("1500.00")
            await session.commit()
            user_id = user.id

        async def extra_spin_task():
            async with session_factory() as session:
                try:
                    res = await WheelService.spin_wheel(
                        session, user_id=user_id, is_extra=True, sector_override="cash_5k", now=now_time
                    )
                    await session.commit()
                    return "SUCCESS"
                except InsufficientFundsError:
                    await session.rollback()
                    return "INSUFFICIENT_CASHBACK"
                except OperationalError:
                    await session.rollback()
                    return "LOCK_ERROR"
                except Exception as e:
                    await session.rollback()
                    return f"ERROR: {type(e).__name__}"

        tasks = [extra_spin_task() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        success_count = results.count("SUCCESS")
        assert success_count == 1, f"Expected exactly 1 success, got {success_count}. Results: {results}"
        assert results.count("INSUFFICIENT_CASHBACK") == 9

        async with session_factory() as session:
            db_user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
            assert db_user.cashback_balance == Decimal("500.00"), (
                f"Expected 500.00 cashback remaining, got {db_user.cashback_balance}"
            )

    @pytest.mark.asyncio
    async def test_concurrent_extra_wheel_spins_two_allowed(self, test_engine):
        """
        Scenario:
        - User has 2,500.00 cashback rubles (cost per extra spin is 1,000.00).
        - 10 concurrent tasks attempt extra spins simultaneously.
        Invariants:
        - Exactly 2 extra spins succeed (total cost 2,000.00).
        - Remaining cashback balance is exactly 500.00.
        - Exactly 8 tasks fail with InsufficientFundsError.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
        now_time = datetime.datetime.now()

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=910007, username="two_spin_stress", full_name="Two Spin Stress"
            )
            user.cashback_balance = Decimal("2500.00")
            await session.commit()
            user_id = user.id

        async def extra_spin_task():
            async with session_factory() as session:
                try:
                    res = await WheelService.spin_wheel(
                        session, user_id=user_id, is_extra=True, sector_override="cash_5k", now=now_time
                    )
                    await session.commit()
                    return "SUCCESS"
                except InsufficientFundsError:
                    await session.rollback()
                    return "INSUFFICIENT_CASHBACK"
                except OperationalError:
                    await session.rollback()
                    return "LOCK_ERROR"
                except Exception as e:
                    await session.rollback()
                    return f"ERROR: {type(e).__name__}"

        tasks = [extra_spin_task() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        success_count = results.count("SUCCESS")
        assert success_count == 2, f"Expected exactly 2 successes, got {success_count}. Results: {results}"
        assert results.count("INSUFFICIENT_CASHBACK") == 8

        async with session_factory() as session:
            db_user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
            assert db_user.cashback_balance == Decimal("500.00")

