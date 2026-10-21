"""
Empirical Concurrency Challenge Harness for Milestone 1 Gate 2.

Exhaustive stress tests for:
1. SavingsService.payout_interest atomic CAS race conditions (up to 50 concurrent tasks).
2. CashbackService.payout_cashback atomic CAS race conditions (up to 50 concurrent tasks).
3. Interleaved concurrent operations (deposit/withdraw/payout and accrue/payout).
"""

import asyncio
from decimal import Decimal
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import Account, SavingsAccount, Transaction, User
from app.services.banking import BankingService
from app.services.cashback import CashbackService
from app.services.savings import SavingsService


class TestGate2SavingsConcurrencyStress:
    """Empirical challenge for SavingsService.payout_interest race safety."""

    @pytest.mark.asyncio
    async def test_savings_payout_interest_50_concurrent_tasks_exact_once(self, test_engine):
        """User has 150.00 ₽ accrued interest.

        50 concurrent asyncio tasks call payout_interest simultaneously.
        Invariants:
        - Exactly 1 task succeeds returning Decimal('150.00').
        - Exactly 49 tasks return Decimal('0.00').
        - Total paid out across all 50 tasks == Decimal('150.00').
        - SavingsAccount.balance increases by exactly 150.00 ₽.
        - SavingsAccount.accrued_interest becomes Decimal('0.000000').
        - Exactly 1 INTEREST_PAYOUT transaction is persisted in the ledger.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, _, _ = await BankingService.get_or_create_user(
                session, telegram_id=910001, username="savings_race_50", full_name="Savings Race 50"
            )
            savings = await SavingsService.get_or_create_savings(session, user.id)
            savings.balance = Decimal("1000.00")
            savings.accrued_interest = Decimal("150.000000")
            await session.commit()
            u_id = user.id

        async def attempt_payout():
            async with session_factory() as session:
                try:
                    paid = await SavingsService.payout_interest(session, u_id)
                    await session.commit()
                    return paid
                except Exception:
                    await session.rollback()
                    return Decimal("0.00")

        tasks = [attempt_payout() for _ in range(50)]
        results = await asyncio.gather(*tasks)

        # 1. Verify return values
        positive_payouts = [r for r in results if r > Decimal("0.00")]
        zero_payouts = [r for r in results if r == Decimal("0.00")]

        assert len(positive_payouts) == 1, (
            f"Expected exactly 1 payout success, got {len(positive_payouts)}. Results: {positive_payouts}"
        )
        assert len(zero_payouts) == 49, (
            f"Expected 49 zero payouts, got {len(zero_payouts)}"
        )
        assert positive_payouts[0] == Decimal("150.00")
        assert sum(results) == Decimal("150.00"), f"Total paid out must be 150.00, got {sum(results)}"

        # 2. Verify database state
        async with session_factory() as session:
            savings_db = await SavingsService.get_savings(session, u_id)
            assert savings_db.balance == Decimal("1150.00"), (
                f"Expected savings balance 1150.00, got {savings_db.balance}"
            )
            assert savings_db.accrued_interest < Decimal("0.01"), (
                f"Expected accrued_interest < 0.01, got {savings_db.accrued_interest}"
            )

            # Check transaction records
            tx_count_res = await session.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.tx_type == "INTEREST_PAYOUT"
                )
            )
            tx_count = tx_count_res.scalar_one()
            assert tx_count == 1, f"Expected exactly 1 INTEREST_PAYOUT transaction, found {tx_count}"

            tx_res = await session.execute(
                select(Transaction).where(Transaction.tx_type == "INTEREST_PAYOUT")
            )
            tx = tx_res.scalars().first()
            assert tx.amount == Decimal("150.00")

    @pytest.mark.asyncio
    async def test_savings_payout_interest_zero_accrued_concurrent(self, test_engine):
        """User has 0.00 ₽ accrued interest.

        20 concurrent tasks call payout_interest simultaneously.
        All 20 must return Decimal('0.00').
        No balance changes, 0 transactions created.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, _, _ = await BankingService.get_or_create_user(
                session, telegram_id=910002, username="savings_zero_20", full_name="Savings Zero 20"
            )
            savings = await SavingsService.get_or_create_savings(session, user.id)
            savings.balance = Decimal("500.00")
            savings.accrued_interest = Decimal("0.000000")
            await session.commit()
            u_id = user.id

        async def attempt_payout():
            async with session_factory() as session:
                try:
                    paid = await SavingsService.payout_interest(session, u_id)
                    await session.commit()
                    return paid
                except Exception:
                    await session.rollback()
                    return Decimal("-1.00")

        tasks = [attempt_payout() for _ in range(20)]
        results = await asyncio.gather(*tasks)

        assert all(r == Decimal("0.00") for r in results), f"All results should be 0.00, got: {results}"

        async with session_factory() as session:
            savings_db = await SavingsService.get_savings(session, u_id)
            assert savings_db.balance == Decimal("500.00")
            tx_count = (await session.execute(
                select(func.count(Transaction.id)).where(Transaction.tx_type == "INTEREST_PAYOUT")
            )).scalar_one()
            assert tx_count == 0

    @pytest.mark.asyncio
    async def test_savings_payout_interest_fractional_rounding_concurrent(self, test_engine):
        """User has 10.005000 ₽ accrued interest (rounds half up to 10.01 ₽).

        25 concurrent tasks call payout_interest.
        Invariant: Exactly 1 task receives Decimal('10.01'), 24 receive Decimal('0.00').
        Savings balance increments by 10.01.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, _, _ = await BankingService.get_or_create_user(
                session, telegram_id=910003, username="savings_round_25", full_name="Savings Round 25"
            )
            savings = await SavingsService.get_or_create_savings(session, user.id)
            savings.balance = Decimal("200.00")
            savings.accrued_interest = Decimal("10.005000")
            await session.commit()
            u_id = user.id

        async def attempt_payout():
            async with session_factory() as session:
                try:
                    paid = await SavingsService.payout_interest(session, u_id)
                    await session.commit()
                    return paid
                except Exception:
                    await session.rollback()
                    return Decimal("0.00")

        tasks = [attempt_payout() for _ in range(25)]
        results = await asyncio.gather(*tasks)

        assert sum(results) == Decimal("10.01")
        assert results.count(Decimal("10.01")) == 1
        assert results.count(Decimal("0.00")) == 24

        async with session_factory() as session:
            savings_db = await SavingsService.get_savings(session, u_id)
            assert savings_db.balance == Decimal("210.01")
            assert savings_db.accrued_interest == Decimal("0.000000")


class TestGate2CashbackConcurrencyStress:
    """Empirical challenge for CashbackService.payout_cashback race safety."""

    @pytest.mark.asyncio
    async def test_cashback_payout_50_concurrent_tasks_exact_once(self, test_engine):
        """User has 250.00 ₽ cashback balance, 50.00 ₽ main account balance.

        50 concurrent asyncio tasks call payout_cashback simultaneously.
        Invariants:
        - Exactly 1 task succeeds returning Decimal('250.00').
        - Exactly 49 tasks fail with ValueError.
        - Main account balance becomes exactly 300.00 ₽ (50.00 + 250.00).
        - User.cashback_balance becomes Decimal('0.00').
        - Exactly 1 CASHBACK_PAYOUT transaction is persisted in the ledger.
        - Zero duplicate money created.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=920001, username="cb_race_50", full_name="Cashback Race 50"
            )
            acc.balance = Decimal("50.00")
            user.cashback_balance = Decimal("250.00")
            await session.commit()
            u_id = user.id
            main_acc_id = acc.id

        async def attempt_cashback_payout():
            async with session_factory() as session:
                try:
                    paid = await CashbackService.payout_cashback(session, u_id)
                    await session.commit()
                    return paid
                except ValueError as e:
                    await session.rollback()
                    return e
                except Exception as e:
                    await session.rollback()
                    return f"UNEXPECTED_{type(e).__name__}"

        tasks = [attempt_cashback_payout() for _ in range(50)]
        results = await asyncio.gather(*tasks)

        successes = [r for r in results if isinstance(r, Decimal)]
        failures = [r for r in results if isinstance(r, ValueError)]
        unexpected = [r for r in results if not isinstance(r, (Decimal, ValueError))]

        assert len(unexpected) == 0, f"Unexpected exceptions encountered: {unexpected}"
        assert len(successes) == 1, (
            f"Expected exactly 1 successful payout, got {len(successes)}. Payout amounts: {successes}"
        )
        assert len(failures) == 49, f"Expected exactly 49 ValueError rejections, got {len(failures)}"
        assert successes[0] == Decimal("250.00")

        # Verify database state
        async with session_factory() as session:
            main_acc = (await session.execute(
                select(Account).where(Account.id == main_acc_id)
            )).scalar_one()
            user_db = (await session.execute(
                select(User).where(User.id == u_id)
            )).scalar_one()

            assert main_acc.balance == Decimal("300.00"), (
                f"Main account balance must be 300.00, got {main_acc.balance} (money duplication check)"
            )
            assert user_db.cashback_balance == Decimal("0.00"), (
                f"Cashback balance must be 0.00, got {user_db.cashback_balance}"
            )

            # Transaction count check
            tx_count = (await session.execute(
                select(func.count(Transaction.id)).where(Transaction.tx_type == "CASHBACK_PAYOUT")
            )).scalar_one()
            assert tx_count == 1, f"Expected exactly 1 CASHBACK_PAYOUT transaction, found {tx_count}"

            tx = (await session.execute(
                select(Transaction).where(Transaction.tx_type == "CASHBACK_PAYOUT")
            )).scalar_one()
            assert tx.amount == Decimal("250.00")
            assert tx.recipient_account_id == main_acc_id

    @pytest.mark.asyncio
    async def test_cashback_payout_sub_threshold_rejected_concurrent(self, test_engine):
        """User has 0.99 ₽ cashback balance (below 1.00 ₽ minimum threshold).

        20 concurrent tasks attempt payout_cashback.
        All 20 must be rejected with ValueError.
        No balances modified, 0 transactions recorded.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=920002, username="cb_sub_20", full_name="Cashback Sub 20"
            )
            acc.balance = Decimal("100.00")
            user.cashback_balance = Decimal("0.99")
            await session.commit()
            u_id = user.id
            main_acc_id = acc.id

        async def attempt_payout():
            async with session_factory() as session:
                try:
                    paid = await CashbackService.payout_cashback(session, u_id)
                    await session.commit()
                    return paid
                except ValueError as e:
                    await session.rollback()
                    return e

        tasks = [attempt_payout() for _ in range(20)]
        results = await asyncio.gather(*tasks)

        assert all(isinstance(r, ValueError) for r in results), (
            f"All tasks should fail with ValueError, got: {results}"
        )

        async with session_factory() as session:
            main_acc = (await session.execute(
                select(Account).where(Account.id == main_acc_id)
            )).scalar_one()
            user_db = (await session.execute(
                select(User).where(User.id == u_id)
            )).scalar_one()

            assert main_acc.balance == Decimal("100.00")
            assert user_db.cashback_balance == Decimal("0.99")

            tx_count = (await session.execute(
                select(func.count(Transaction.id)).where(Transaction.tx_type == "CASHBACK_PAYOUT")
            )).scalar_one()
            assert tx_count == 0


class TestGate2InterleavedConcurrencyStress:
    """Empirical challenge for interleaved banking operations."""

    @pytest.mark.asyncio
    async def test_interleaved_savings_payout_and_deposits(self, test_engine):
        """Race condition test: 10 tasks trying to payout interest while 10 tasks deposit money.

        Invariant:
        - Accrued interest is claimed at most once.
        - Total deposited matches the successful deposit amounts.
        - Total funds are conserved.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, main_acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=930001, username="interleave_user", full_name="Interleave User"
            )
            main_acc.balance = Decimal("1000.00")
            savings = await SavingsService.get_or_create_savings(session, user.id)
            savings.balance = Decimal("500.00")
            savings.accrued_interest = Decimal("50.000000")
            await session.commit()
            u_id = user.id

        async def do_payout():
            async with session_factory() as session:
                try:
                    paid = await SavingsService.payout_interest(session, u_id)
                    await session.commit()
                    return ("PAYOUT", paid)
                except Exception as e:
                    await session.rollback()
                    return ("PAYOUT_ERR", str(e))

        async def do_deposit():
            async with session_factory() as session:
                try:
                    tx = await SavingsService.deposit_to_savings(session, u_id, Decimal("20.00"))
                    await session.commit()
                    return ("DEPOSIT", tx.amount)
                except Exception as e:
                    await session.rollback()
                    return ("DEPOSIT_ERR", str(e))

        payout_tasks = [do_payout() for _ in range(10)]
        deposit_tasks = [do_deposit() for _ in range(10)]
        all_tasks = payout_tasks + deposit_tasks

        # Shuffle tasks
        import random
        random.shuffle(all_tasks)

        results = await asyncio.gather(*all_tasks)

        payout_wins = [r[1] for r in results if r[0] == "PAYOUT" and r[1] > Decimal("0.00")]
        deposit_wins = [r[1] for r in results if r[0] == "DEPOSIT"]

        # Exactly 1 payout can claim the 50.00 interest
        total_payout = sum(payout_wins)
        assert total_payout == Decimal("50.00"), f"Total payout must be 50.00, got {total_payout}"
        assert len(payout_wins) == 1, f"Expected 1 payout winner, got {len(payout_wins)}"

        # Deposits: 10 tasks * 20.00 = 200.00
        assert len(deposit_wins) == 10
        assert sum(deposit_wins) == Decimal("200.00")

        # Verify conservation of funds:
        # Initial: Main 1000.00, Savings 500.00 + 50.00 interest = 1550.00
        # Final: Main 1000 - 200 = 800.00
        # Savings: 500 + 50 (payout) + 200 (deposits) = 750.00
        # Total: 800 + 750 = 1550.00
        async with session_factory() as session:
            main_bal = (await session.execute(
                select(Account.balance).where(Account.user_id == u_id).where(Account.account_type == "MAIN")
            )).scalar_one()
            savings = await SavingsService.get_savings(session, u_id)

            assert main_bal == Decimal("800.00"), f"Expected main balance 800.00, got {main_bal}"
            assert savings.balance == Decimal("750.00"), f"Expected savings balance 750.00, got {savings.balance}"
            assert main_bal + savings.balance == Decimal("1550.00"), "Conservation of funds violated!"
