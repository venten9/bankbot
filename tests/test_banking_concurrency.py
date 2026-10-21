"""Tier 2: Boundary & Concurrency Tests.

Covers:
- Concurrency stress tests: 10 concurrent transfers attempting to overdraft an account via asyncio.gather.
- 20 concurrent transfers competing for limited funds, strict zero overdraft verification.
- Concurrent mutual transfers between accounts with total money supply conservation.
- Self-transfer rejection (SelfTransferError).
- Frozen account / frozen card rejection (AccountFrozenError) on outgoing transfers, acceptance on incoming.
- Negative amount / zero amount transfer validation.
- Recipient multi-route resolution (@username, phone, card, user ID).
- Double-entry ledger verification (Transaction + 2 LedgerEntries DEBIT/CREDIT).
- Savings safe deposit, withdrawal, boundary overdraft, and duplicate/concurrent interest payout prevention.
- Stock trading boundary overdraft, integer share validations, and InsufficientSharesError.
- Cashback monthly category selection constraints (max 4, locking) and 1-click payout boundary.
"""

import asyncio
import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import (
    Account,
    Card,
    LedgerEntry,
    SavingsAccount,
    Stock,
    Transaction,
    User,
)
from app.services.banking import BankingService
from app.services.card import CardService
from app.services.cashback import CashbackService
from app.services.investments import (
    DEFAULT_STOCKS,
    InsufficientSharesError,
    InvestmentService,
)
from app.services.savings import SavingsService
from app.services.transfers import (
    AccountFrozenError,
    InsufficientFundsError,
    RecipientNotFoundError,
    SelfTransferError,
    TransferService,
)


# ==============================================================================
# 1. Concurrency Stress & Overdraft Prevention Tests
# ==============================================================================

class TestConcurrencyAndOverdraftStress:
    """Stress testing atomic double-entry updates under asynchronous concurrency."""

    @pytest.mark.asyncio
    async def test_ten_concurrent_transfers_overdraft_stress(self, test_engine):
        """
        Scenario:
        - Sender has 500.00 ₽ balance.
        - Recipient has 0.00 ₽ balance.
        - 10 concurrent transfer tasks attempt to send 200.00 ₽ each via asyncio.gather.
        - Total requested = 2,000.00 ₽. Available = 500.00 ₽.
        Expected:
        - Exactly 2 transfers succeed (2 * 200.00 = 400.00 ₽).
        - Exactly 8 transfers fail with InsufficientFundsError.
        - Sender final balance = 100.00 ₽.
        - Recipient final balance = 400.00 ₽.
        - Total money is conserved (500.00 ₽).
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        # Setup accounts
        async with session_factory() as session:
            sender, s_acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=200001, username="sender_stress", full_name="Sender Stress"
            )
            recipient, r_acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=200002, username="rec_stress", full_name="Recipient Stress"
            )
            s_acc.balance = Decimal("500.00")
            r_acc.balance = Decimal("0.00")
            await session.commit()
            sender_id = sender.id
            recipient_acc_id = r_acc.id

        # Worker coroutine per transfer
        async def transfer_task():
            async with session_factory() as session:
                try:
                    await TransferService.execute_transfer(
                        session=session,
                        sender_user_id=sender_id,
                        recipient_account_id=recipient_acc_id,
                        amount=Decimal("200.00"),
                    )
                    await session.commit()
                    return "SUCCESS"
                except InsufficientFundsError:
                    await session.rollback()
                    return "INSUFFICIENT_FUNDS"
                except Exception as e:
                    await session.rollback()
                    return f"ERROR: {type(e).__name__}"

        # Run 10 concurrent transfers
        tasks = [transfer_task() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        success_count = results.count("SUCCESS")
        insufficient_count = results.count("INSUFFICIENT_FUNDS")

        assert success_count == 2
        assert insufficient_count == 8

        # Verify final balances in DB
        async with session_factory() as session:
            s_res = await session.execute(
                select(Account.balance).where(Account.user_id == sender_id).where(Account.account_type == "MAIN")
            )
            r_res = await session.execute(
                select(Account.balance).where(Account.id == recipient_acc_id)
            )
            sender_final = s_res.scalar_one()
            recipient_final = r_res.scalar_one()

            assert sender_final == Decimal("100.00")
            assert recipient_final == Decimal("400.00")
            assert sender_final + recipient_final == Decimal("500.00")

    @pytest.mark.asyncio
    async def test_twenty_concurrent_transfers_competing_to_zero(self, test_engine):
        """
        Scenario:
        - Sender has 1,000.00 ₽ balance.
        - 20 concurrent transfer tasks attempt to send 100.00 ₽ each.
        - Total requested = 2,000.00 ₽. Available = 1,000.00 ₽.
        Expected:
        - Exactly 10 transfers succeed.
        - Exactly 10 transfers fail.
        - Final sender balance = exactly 0.00 ₽.
        - Sender balance NEVER becomes negative.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            sender, s_acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=200003, username="sender_20", full_name="Sender 20"
            )
            recipient, r_acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=200004, username="rec_20", full_name="Recipient 20"
            )
            s_acc.balance = Decimal("1000.00")
            r_acc.balance = Decimal("0.00")
            await session.commit()
            sender_id = sender.id
            recipient_acc_id = r_acc.id

        async def worker():
            async with session_factory() as session:
                try:
                    await TransferService.execute_transfer(
                        session=session,
                        sender_user_id=sender_id,
                        recipient_account_id=recipient_acc_id,
                        amount=Decimal("100.00"),
                    )
                    await session.commit()
                    return True
                except InsufficientFundsError:
                    await session.rollback()
                    return False

        results = await asyncio.gather(*(worker() for _ in range(20)))

        assert results.count(True) == 10
        assert results.count(False) == 10

        async with session_factory() as session:
            s_bal = (
                await session.execute(
                    select(Account.balance).where(Account.user_id == sender_id).where(Account.account_type == "MAIN")
                )
            ).scalar_one()
            r_bal = (
                await session.execute(select(Account.balance).where(Account.id == recipient_acc_id))
            ).scalar_one()

            assert s_bal == Decimal("0.00")
            assert r_bal == Decimal("1000.00")

    @pytest.mark.asyncio
    async def test_concurrent_mutual_cross_transfers_currency_conservation(self, test_engine):
        """
        Scenario:
        - Account A has 1,000.00 ₽.
        - Account B has 1,000.00 ₽.
        - Concurrently launch 10 tasks transferring 100 ₽ from A -> B and 10 tasks transferring 100 ₽ from B -> A.
        - Total money supply (M0 = 2,000.00 ₽) must remain strictly conserved.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user_a, acc_a, _ = await BankingService.get_or_create_user(
                session, telegram_id=200005, username="user_a", full_name="User A"
            )
            user_b, acc_b, _ = await BankingService.get_or_create_user(
                session, telegram_id=200006, username="user_b", full_name="User B"
            )
            acc_a.balance = Decimal("1000.00")
            acc_b.balance = Decimal("1000.00")
            await session.commit()
            a_id, a_acc_id = user_a.id, acc_a.id
            b_id, b_acc_id = user_b.id, acc_b.id

        async def a_to_b():
            async with session_factory() as session:
                try:
                    await TransferService.execute_transfer(
                        session=session,
                        sender_user_id=a_id,
                        recipient_account_id=b_acc_id,
                        amount=Decimal("100.00"),
                    )
                    await session.commit()
                    return "OK"
                except Exception:
                    await session.rollback()
                    return "FAIL"

        async def b_to_a():
            async with session_factory() as session:
                try:
                    await TransferService.execute_transfer(
                        session=session,
                        sender_user_id=b_id,
                        recipient_account_id=a_acc_id,
                        amount=Decimal("100.00"),
                    )
                    await session.commit()
                    return "OK"
                except Exception:
                    await session.rollback()
                    return "FAIL"

        tasks = []
        for _ in range(10):
            tasks.append(a_to_b())
            tasks.append(b_to_a())

        await asyncio.gather(*tasks)

        async with session_factory() as session:
            bal_a = (await session.execute(select(Account.balance).where(Account.id == a_acc_id))).scalar_one()
            bal_b = (await session.execute(select(Account.balance).where(Account.id == b_acc_id))).scalar_one()

            assert bal_a >= Decimal("0.00")
            assert bal_b >= Decimal("0.00")
            assert bal_a + bal_b == Decimal("2000.00")


# ==============================================================================
# 2. Transfer Boundary & Validation Tests
# ==============================================================================

class TestTransferBoundaries:
    """Boundary conditions, negative balances, and error handling for transfers."""

    @pytest.mark.asyncio
    async def test_transfer_exact_balance_succeeds(self, async_session, sample_user, sample_recipient):
        """Transferring exact available balance leaves 0.00 ₽ without overdraft."""
        sender_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        recipient_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        current_bal = sender_acc.balance

        tx = await TransferService.execute_transfer(
            async_session,
            sender_user_id=sample_user.id,
            recipient_account_id=recipient_acc.id,
            amount=current_bal,
        )
        assert tx.status == "COMPLETED"
        assert sender_acc.balance == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_transfer_overdraft_by_one_kopeck_rejected(self, async_session, sample_user, sample_recipient):
        """Transferring balance + 0.01 ₽ raises InsufficientFundsError and preserves balance."""
        sender_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        recipient_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        initial_balance = sender_acc.balance
        overdraft_amount = initial_balance + Decimal("0.01")

        with pytest.raises(InsufficientFundsError):
            await TransferService.execute_transfer(
                async_session,
                sender_user_id=sample_user.id,
                recipient_account_id=recipient_acc.id,
                amount=overdraft_amount,
            )

        assert sender_acc.balance == initial_balance

    @pytest.mark.asyncio
    @pytest.mark.parametrize("invalid_amount", [0, -1, -500.50, Decimal("0.00"), Decimal("-0.01")])
    async def test_transfer_zero_or_negative_amount_rejected(
        self, async_session, sample_user, sample_recipient, invalid_amount
    ):
        """Zero and negative transfer amounts must be rejected with ValueError."""
        recipient_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)

        with pytest.raises(ValueError):
            await TransferService.execute_transfer(
                async_session,
                sender_user_id=sample_user.id,
                recipient_account_id=recipient_acc.id,
                amount=invalid_amount,
            )

    @pytest.mark.asyncio
    async def test_self_transfer_rejected(self, async_session, sample_user):
        """Transferring funds to one's own same account must raise SelfTransferError."""
        sender_acc = await BankingService.get_user_main_account(async_session, sample_user.id)

        with pytest.raises(SelfTransferError):
            await TransferService.execute_transfer(
                async_session,
                sender_user_id=sample_user.id,
                recipient_account_id=sender_acc.id,
                amount=Decimal("100.00"),
            )

    @pytest.mark.asyncio
    async def test_frozen_sender_account_transfer_rejected(self, async_session, sample_user, sample_recipient):
        """Outgoing transfer from frozen account must raise AccountFrozenError."""
        sender_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        recipient_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)

        await CardService.freeze_card(async_session, user_id=sample_user.id)
        await async_session.refresh(sender_acc)
        assert sender_acc.is_frozen is True

        with pytest.raises(AccountFrozenError):
            await TransferService.execute_transfer(
                async_session,
                sender_user_id=sample_user.id,
                recipient_account_id=recipient_acc.id,
                amount=Decimal("500.00"),
            )

    @pytest.mark.asyncio
    async def test_frozen_recipient_account_can_receive_funds(self, async_session, sample_user, sample_recipient):
        """Incoming transfer to frozen recipient account succeeds (standard banking behavior)."""
        recipient_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        await CardService.freeze_card(async_session, user_id=sample_recipient.id)
        await async_session.refresh(recipient_acc)
        assert recipient_acc.is_frozen is True

        initial_recipient_bal = recipient_acc.balance

        tx = await TransferService.execute_transfer(
            async_session,
            sender_user_id=sample_user.id,
            recipient_account_id=recipient_acc.id,
            amount=Decimal("250.00"),
        )
        assert tx.status == "COMPLETED"
        await async_session.refresh(recipient_acc)
        assert recipient_acc.balance == initial_recipient_bal + Decimal("250.00")

    @pytest.mark.asyncio
    async def test_transfer_to_nonexistent_recipient_fails(self, async_session, sample_user):
        """Transfer to an invalid UUID recipient account raises RecipientNotFoundError."""
        with pytest.raises(RecipientNotFoundError):
            await TransferService.execute_transfer(
                async_session,
                sender_user_id=sample_user.id,
                recipient_account_id="nonexistent-account-uuid-99999",
                amount=Decimal("100.00"),
            )


# ==============================================================================
# 3. Recipient Multi-Route Resolution Tests
# ==============================================================================

class TestRecipientResolution:
    """Multi-identifier recipient lookup: @username, phone, card 16-digit, ID."""

    @pytest.mark.asyncio
    async def test_resolve_by_username_with_and_without_at(self, async_session, sample_user):
        """Resolves user by @username (case-insensitive) and plain username."""
        # With @
        acc1 = await TransferService.resolve_recipient(async_session, "@alice")
        assert acc1 is not None
        assert acc1.user_id == sample_user.id

        # Uppercase with @
        acc2 = await TransferService.resolve_recipient(async_session, "@ALICE")
        assert acc2 is not None
        assert acc2.user_id == sample_user.id

        # Without @
        acc3 = await TransferService.resolve_recipient(async_session, "alice")
        assert acc3 is not None
        assert acc3.user_id == sample_user.id

    @pytest.mark.asyncio
    async def test_resolve_by_card_number(self, async_session, sample_user):
        """Resolves recipient by 16-digit Luhn card number (with or without spaces)."""
        card_res = await async_session.execute(select(Card).where(Card.user_id == sample_user.id))
        card = card_res.scalars().first()
        assert card is not None

        # Clean card number
        acc = await TransferService.resolve_recipient(async_session, card.card_number)
        assert acc is not None
        assert acc.user_id == sample_user.id

        # Card number with spaces
        spaced = f"{card.card_number[:4]} {card.card_number[4:8]} {card.card_number[8:12]} {card.card_number[12:]}"
        acc_spaced = await TransferService.resolve_recipient(async_session, spaced)
        assert acc_spaced is not None
        assert acc_spaced.user_id == sample_user.id

    @pytest.mark.asyncio
    async def test_resolve_by_phone_number(self, async_session, sample_user):
        """Resolves recipient by phone number in various formats."""
        # Standard +7
        acc1 = await TransferService.resolve_recipient(async_session, "+79991112233")
        assert acc1 is not None
        assert acc1.user_id == sample_user.id

        # Format with 8
        acc2 = await TransferService.resolve_recipient(async_session, "89991112233")
        assert acc2 is not None
        assert acc2.user_id == sample_user.id

        # Format with dashes and spaces
        acc3 = await TransferService.resolve_recipient(async_session, "+7 (999) 111-22-33")
        assert acc3 is not None
        assert acc3.user_id == sample_user.id

    @pytest.mark.asyncio
    async def test_resolve_by_telegram_id(self, async_session, sample_user):
        """Resolves recipient by integer or numeric string Telegram ID."""
        acc_int = await TransferService.resolve_recipient(async_session, sample_user.telegram_id)
        assert acc_int is not None
        assert acc_int.user_id == sample_user.id

        acc_str = await TransferService.resolve_recipient(async_session, str(sample_user.telegram_id))
        assert acc_str is not None
        assert acc_str.user_id == sample_user.id

    @pytest.mark.asyncio
    async def test_resolve_unknown_returns_none(self, async_session):
        """Unknown username, phone, or invalid card number returns None."""
        assert await TransferService.resolve_recipient(async_session, "@nobody_known") is None
        assert await TransferService.resolve_recipient(async_session, "+70000000000") is None
        assert await TransferService.resolve_recipient(async_session, 999999999999) is None
        assert await TransferService.resolve_recipient(async_session, "2200700000000001") is None  # Invalid Luhn


# ==============================================================================
# 4. Double-Entry Accounting & Ledger Integrity Tests
# ==============================================================================

class TestDoubleEntryLedger:
    """Verifies strict double-entry ledger postings on transfers."""

    @pytest.mark.asyncio
    async def test_transfer_creates_balanced_ledger_entries(self, async_session, sample_user, sample_recipient):
        """Each transfer creates 1 Transaction and exactly 2 balanced LedgerEntry rows."""
        sender_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        recipient_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        amount = Decimal("750.00")

        tx = await TransferService.execute_transfer(
            async_session,
            sender_user_id=sample_user.id,
            recipient_account_id=recipient_acc.id,
            amount=amount,
            category="Обед",
            description="За совместный обед",
        )

        # Query ledger entries for this transaction
        ledger_res = await async_session.execute(
            select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id)
        )
        entries = ledger_res.scalars().all()
        assert len(entries) == 2

        debit_entry = next((e for e in entries if e.direction == "DEBIT"), None)
        credit_entry = next((e for e in entries if e.direction == "CREDIT"), None)

        assert debit_entry is not None
        assert credit_entry is not None
        assert debit_entry.account_id == sender_acc.id
        assert credit_entry.account_id == recipient_acc.id
        assert debit_entry.amount == amount
        assert credit_entry.amount == amount

        # Verify balance conservation
        assert debit_entry.amount == credit_entry.amount

    @pytest.mark.asyncio
    async def test_transfer_idempotency_key_prevents_duplicate(self, async_session, sample_user, sample_recipient):
        """Executing with the same idempotency key returns the cached transaction without double-debit."""
        sender_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        recipient_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        key = "idemp-key-test-12345"
        amount = Decimal("300.00")

        bal_before = sender_acc.balance

        tx1 = await TransferService.execute_transfer(
            async_session,
            sender_user_id=sample_user.id,
            recipient_account_id=recipient_acc.id,
            amount=amount,
            idempotency_key=key,
        )

        # Second attempt with same key
        tx2 = await TransferService.execute_transfer(
            async_session,
            sender_user_id=sample_user.id,
            recipient_account_id=recipient_acc.id,
            amount=amount,
            idempotency_key=key,
        )

        assert tx1.id == tx2.id
        await async_session.refresh(sender_acc)
        # Balance must only have been debited ONCE
        assert sender_acc.balance == bal_before - amount


# ==============================================================================
# 5. Savings Safe Deposit, Withdrawal & Anti-Duplication Tests
# ==============================================================================

class TestSavingsSafeOperations:
    """Deposit, withdrawal, and atomic interest payout without duplicate payouts."""

    @pytest.mark.asyncio
    async def test_savings_deposit_happy_path(self, async_session, sample_user):
        """Moving funds from Main account to Savings increases savings and decreases main."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        initial_main = main_acc.balance

        deposit_amt = Decimal("2000.00")
        tx = await SavingsService.deposit_to_savings(async_session, sample_user.id, deposit_amt)
        assert tx.status == "COMPLETED"

        savings = await SavingsService.get_savings(async_session, sample_user.id)
        assert savings.balance == deposit_amt
        assert main_acc.balance == initial_main - deposit_amt

    @pytest.mark.asyncio
    async def test_savings_deposit_insufficient_funds_rejected(self, async_session, sample_user):
        """Depositing more than main account balance raises InsufficientFundsError."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        too_much = main_acc.balance + Decimal("1000.00")

        with pytest.raises(InsufficientFundsError):
            await SavingsService.deposit_to_savings(async_session, sample_user.id, too_much)

    @pytest.mark.asyncio
    async def test_savings_withdraw_happy_path(self, async_session, sample_user):
        """Withdrawing funds from Savings back to Main increases main and decreases savings."""
        # First deposit
        await SavingsService.deposit_to_savings(async_session, sample_user.id, Decimal("3000.00"))
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        bal_before = main_acc.balance

        # Now withdraw 1,000.00 ₽
        tx = await SavingsService.withdraw_from_savings(async_session, sample_user.id, Decimal("1000.00"))
        assert tx.status == "COMPLETED"

        savings = await SavingsService.get_savings(async_session, sample_user.id)
        assert savings.balance == Decimal("2000.00")
        await async_session.refresh(main_acc)
        assert main_acc.balance == bal_before + Decimal("1000.00")

    @pytest.mark.asyncio
    async def test_savings_withdraw_insufficient_savings_rejected(self, async_session, sample_user):
        """Withdrawing more than available in Savings raises InsufficientFundsError."""
        savings = await SavingsService.get_savings(async_session, sample_user.id)
        savings.balance = Decimal("500.00")
        await async_session.flush()

        with pytest.raises(InsufficientFundsError):
            await SavingsService.withdraw_from_savings(async_session, sample_user.id, Decimal("500.01"))

    @pytest.mark.asyncio
    async def test_duplicate_interest_payout_prevention(self, async_session, sample_user):
        """
        Duplicate interest payout prevention:
        - Accrue interest.
        - First payout executes and credits savings balance.
        - Immediate second payout returns 0.00 ₽ and does not alter balance.
        """
        savings = await SavingsService.get_savings(async_session, sample_user.id)
        savings.balance = Decimal("100000.00")
        # Simulate 30 days of elapsed time
        savings.last_interest_calc = datetime.datetime.now() - datetime.timedelta(days=30)
        await async_session.flush()

        # First payout
        payout_1 = await SavingsService.payout_interest(async_session, sample_user.id)
        assert payout_1 > Decimal("1000.00")  # ~1,479.45 ₽
        assert savings.accrued_interest < Decimal("0.01")
        bal_after_first = savings.balance

        # Immediate second payout (double tap)
        payout_2 = await SavingsService.payout_interest(async_session, sample_user.id)
        assert payout_2 == Decimal("0.00")
        assert savings.balance == bal_after_first


# ==============================================================================
# 6. Stock Trading Boundary Tests
# ==============================================================================

class TestStockTradingBoundaries:
    """Boundary conditions for T-Investments: insufficient funds, share quantities, selling limits."""

    @pytest.mark.asyncio
    async def test_buy_stock_insufficient_funds_rejected(self, async_session, sample_user):
        """Buying more shares than affordable raises InsufficientFundsError."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        main_acc.balance = Decimal("100.00")
        await async_session.flush()

        # SHAW base price is 150.00 ₽ + fee
        with pytest.raises(InsufficientFundsError):
            await InvestmentService.buy_stock(async_session, sample_user.id, "SHAW", shares=10)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("invalid_shares", [0, -1, -100])
    async def test_buy_stock_invalid_share_count_rejected(self, async_session, sample_user, invalid_shares):
        """Buying 0 or negative shares raises ValueError."""
        with pytest.raises(ValueError):
            await InvestmentService.buy_stock(async_session, sample_user.id, "SHAW", shares=invalid_shares)

    @pytest.mark.asyncio
    async def test_sell_more_shares_than_owned_rejected(self, async_session, sample_user):
        """Selling more shares than owned raises InsufficientSharesError."""
        with pytest.raises(InsufficientSharesError):
            await InvestmentService.sell_stock(async_session, sample_user.id, "SHAW", shares=5)

    @pytest.mark.asyncio
    async def test_buy_and_sell_entire_position(self, async_session, sample_user):
        """Buying and selling whole position updates holdings to zero and credits proceeds."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        main_acc.balance = Decimal("10000.00")
        await async_session.flush()

        buy_res = await InvestmentService.buy_stock(async_session, sample_user.id, "SHAW", shares=5)
        assert buy_res["total_shares"] == 5

        sell_res = await InvestmentService.sell_stock(async_session, sample_user.id, "SHAW", shares=5)
        assert sell_res["remaining_shares"] == 0
        assert sell_res["net_proceeds"] > Decimal("0.00")


# ==============================================================================
# 7. Cashback Selection & Payout Boundary Tests
# ==============================================================================

class TestCashbackBoundaries:
    """Cashback category limits and 1-click payout boundary checks."""

    @pytest.mark.asyncio
    async def test_selecting_more_than_four_categories_rejected(self, async_session, sample_user):
        """Selecting more than 4 categories raises ValueError."""
        categories = await CashbackService.get_available_categories(async_session)
        five_ids = [c.id for c in categories[:5]]

        with pytest.raises(ValueError):
            await CashbackService.select_categories(
                async_session, sample_user.id, five_ids, month_year="2026-11"
            )

    @pytest.mark.asyncio
    async def test_locked_categories_cannot_be_reselected_same_month(self, async_session, sample_user):
        """Once confirmed for the month, categories cannot be re-selected."""
        categories = await CashbackService.get_available_categories(async_session)
        two_ids = [categories[0].id, categories[1].id]

        await CashbackService.select_categories(
            async_session, sample_user.id, two_ids, month_year="2026-12"
        )

        with pytest.raises(ValueError):
            await CashbackService.select_categories(
                async_session, sample_user.id, two_ids, month_year="2026-12"
            )

    @pytest.mark.asyncio
    async def test_payout_zero_cashback_rejected(self, async_session, sample_user):
        """Attempting payout when cashback balance is 0.00 ₽ raises ValueError."""
        sample_user.cashback_balance = Decimal("0.00")
        await async_session.flush()

        with pytest.raises(ValueError):
            await CashbackService.payout_cashback(async_session, sample_user.id)

    @pytest.mark.asyncio
    async def test_payout_cashback_credits_main_and_resets_balance(self, async_session, sample_user):
        """Successful payout credits main account and resets cashback balance to 0.00 ₽."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        initial_main = main_acc.balance

        sample_user.cashback_balance = Decimal("1250.00")
        await async_session.flush()

        payout = await CashbackService.payout_cashback(async_session, sample_user.id)
        assert payout == Decimal("1250.00")
        assert sample_user.cashback_balance == Decimal("0.00")
        await async_session.refresh(main_acc)
        assert main_acc.balance == initial_main + Decimal("1250.00")
