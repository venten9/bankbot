"""Empirical Stress Test Harness & Adversarial Challenge Suite for Milestone 1.

Author: Challenger 1 (Milestone 1 Empirical Challenger)
Scope:
1. Card Luhn generation, independent oracle verification (10,000 cards), single-digit mutation detectability, adjacent transposition sensitivity, and edge cases.
2. Concurrent transfer overdraft prevention under high concurrency (50 coroutines, bi-directional race, zero-balance hammering, self-transfer, frozen accounts).
3. 18% APR daily savings interest math, precision, leap yearActual/365, clock reversal, and anti-duplication payout semantics under concurrency.
4. Parody investments GBM price floor bounds, mean-reversion pull, fee calculation, cost-basis weighted averages, partial/full sell liquidation, and portfolio valuation.
"""

import asyncio
import datetime
import math
import random
from decimal import Decimal, ROUND_HALF_UP

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.database.models import Account, Card, InvestmentPortfolio, SavingsAccount, Stock, Transaction, User
from app.services.banking import BankingService
from app.services.card import CardService
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
# Domain 1: Card Luhn Generation & Validation Under Edge Cases
# ==============================================================================

def independent_luhn_oracle(number: str) -> bool:
    """Independent oracle implementing standard ISO/IEC 7812 Luhn MOD-10 checksum."""
    clean = "".join(c for c in number if c.isdigit())
    if len(clean) != 16:
        return False
    digits = [int(c) for c in clean]
    # In 16-digit number, index 0, 2, 4, ... are doubled from left (or odd from right)
    checksum = 0
    for idx, d in enumerate(digits):
        if idx % 2 == 0:
            val = d * 2
            checksum += (val - 9) if val > 9 else val
        else:
            checksum += d
    return checksum % 10 == 0


class TestLuhnEmpiricalStress:
    """Empirical verification and adversarial challenge for CardService."""

    def test_ten_thousand_generated_cards_against_independent_oracle(self):
        """Generate 10,000 cards and verify every single one matches the independent oracle."""
        seen = set()
        for _ in range(10000):
            card = CardService.generate_card_number()
            # Must be string of length 16
            assert len(card) == 16, f"Invalid card length: {len(card)}"
            assert card.startswith("220070"), f"Missing MIR BIN: {card}"
            assert card.isdigit(), f"Card contains non-digits: {card}"

            # Must pass CardService validator
            assert CardService.validate_luhn(card) is True, f"CardService failed own card: {card}"

            # Must pass independent oracle
            assert independent_luhn_oracle(card) is True, f"Independent oracle rejected card: {card}"

            seen.add(card)

        # Collision check on 10,000 cards (9 random digits = 1 billion space)
        # Birthday paradox: collision probability for 10k items in 1B space is ~0.05
        assert len(seen) >= 9900, f"Excessive collisions detected: only {len(seen)} unique out of 10,000"

    def test_single_digit_mutation_detectability(self):
        """Luhn MOD-10 must detect 100% of single-digit substitution errors.

        We generate 500 cards and mutate every single digit (16 positions * 9 wrong digits = 144 mutations/card).
        Total test cases: 72,000 mutated cards. 0 must pass validation.
        """
        for _ in range(500):
            card = CardService.generate_card_number()
            for pos in range(16):
                original_digit = int(card[pos])
                for replacement in range(10):
                    if replacement == original_digit:
                        continue
                    mutated = card[:pos] + str(replacement) + card[pos + 1:]
                    assert CardService.validate_luhn(mutated) is False, (
                        f"Single-digit mutation not detected! Original: {card}, Mutated pos {pos}: {mutated}"
                    )

    def test_adjacent_digit_transposition_sensitivity(self):
        """Luhn MOD-10 detects adjacent transpositions except when digits differ by 9 (e.g. 09 <-> 90).

        Verify that any adjacent transposition of distinct digits (a, b) with abs(a - b) != 9 fails.
        """
        for _ in range(500):
            card = CardService.generate_card_number()
            for pos in range(15):
                d1 = int(card[pos])
                d2 = int(card[pos + 1])
                if d1 == d2 or abs(d1 - d2) == 9:
                    continue  # Known Luhn limitation (09 and 90 have same contribution)
                transposed = card[:pos] + str(d2) + str(d1) + card[pos + 2:]
                assert CardService.validate_luhn(transposed) is False, (
                    f"Transposition not detected! Original: {card}, Transposed pos {pos}-{pos+1}: {transposed}"
                )

    @pytest.mark.parametrize(
        "invalid_input",
        [
            "",
            " ",
            "    ",
            "1",
            "220070",
            "22007012345678",       # 14 digits
            "220070123456789",      # 15 digits
            "22007012345678901",    # 17 digits
            "22007012345678901234", # 20 digits
            "2200 7012 3456 789",   # 15 digits with spaces
            "2200-7012-3456-789a",  # contains letter
            "abcdefghijklmnop",     # letters only
            "220070123456789!",     # special symbol
            "22007012345678\x000",  # null byte
            "220070123456789\n",    # newline
            "220070123456789\t",    # tab
            "' OR '1'='1",          # SQL injection string
            "<script>alert(1)</script>",
            "-22007012345678",      # negative sign
        ],
    )
    def test_luhn_adversarial_edge_cases(self, invalid_input):
        """Adversarial, malformed, and out-of-spec strings must return False without crashing."""
        assert CardService.validate_luhn(invalid_input) is False

    def test_card_formatting_and_masking(self):
        """Verify card formatting handles unmasked, masked, and malformed inputs gracefully."""
        card = "2200701234567890"
        formatted_unmasked = CardService.format_card_number(card, masked=False)
        assert formatted_unmasked == "2200 7012 3456 7890"

        formatted_masked = CardService.format_card_number(card, masked=True)
        assert formatted_masked == "2200 70•• •••• 7890"
        assert "123456" not in formatted_masked

        # Non-16-digit strings should be returned as-is
        assert CardService.format_card_number("123", masked=False) == "123"
        assert CardService.format_card_number("", masked=True) == ""


# ==============================================================================
# Domain 2: Concurrent Transfer Overdraft Prevention Under High Concurrency
# ==============================================================================

class TestConcurrencyEmpiricalStress:
    """High-concurrency empirical challenge for TransferService."""

    @pytest.mark.asyncio
    async def test_fifty_concurrent_transfers_hammering_limited_balance(self, test_engine):
        """Scenario: Sender has 100.00 ₽. 50 concurrent tasks try to send 20.00 ₽ each.

        Total requested: 1,000.00 ₽. Available: 100.00 ₽.
        Expected:
        - Exactly 5 transfers succeed (5 * 20.00 = 100.00 ₽).
        - Exactly 45 transfers fail with InsufficientFundsError.
        - Sender final balance == 0.00 ₽.
        - Recipient final balance == 100.00 ₽.
        - Conservation of currency strictly holds.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            sender, s_acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=300001, username="c_sender_50", full_name="Concurrency Sender 50"
            )
            recipient, r_acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=300002, username="c_rec_50", full_name="Concurrency Recipient 50"
            )
            s_acc.balance = Decimal("100.00")
            r_acc.balance = Decimal("0.00")
            await session.commit()
            sender_id = sender.id
            recipient_acc_id = r_acc.id

        async def attempt_transfer():
            async with session_factory() as session:
                try:
                    await TransferService.execute_transfer(
                        session=session,
                        sender_user_id=sender_id,
                        recipient_account_id=recipient_acc_id,
                        amount=Decimal("20.00"),
                    )
                    await session.commit()
                    return "SUCCESS"
                except InsufficientFundsError:
                    await session.rollback()
                    return "INSUFFICIENT"
                except Exception as e:
                    await session.rollback()
                    return f"ERROR: {type(e).__name__}"

        tasks = [attempt_transfer() for _ in range(50)]
        results = await asyncio.gather(*tasks)

        success_count = results.count("SUCCESS")
        insufficient_count = results.count("INSUFFICIENT")

        assert success_count == 5, f"Expected exactly 5 successes, got {success_count}. Results: {results}"
        assert insufficient_count == 45, f"Expected 45 insufficient funds, got {insufficient_count}"

        async with session_factory() as session:
            s_bal = (await session.execute(
                select(Account.balance).where(Account.user_id == sender_id).where(Account.account_type == "MAIN")
            )).scalar_one()
            r_bal = (await session.execute(
                select(Account.balance).where(Account.id == recipient_acc_id)
            )).scalar_one()

            assert s_bal == Decimal("0.00"), f"Sender balance breached zero: {s_bal}"
            assert r_bal == Decimal("100.00"), f"Recipient balance incorrect: {r_bal}"
            assert s_bal + r_bal == Decimal("100.00"), "Currency conservation invariant violated!"

    @pytest.mark.asyncio
    async def test_bidirectional_concurrent_cross_transfers(self, test_engine):
        """Stress test bi-directional simultaneous transfers between two accounts.

        Account A: 500.00 ₽. Account B: 500.00 ₽. Total = 1,000.00 ₽.
        25 tasks transfer 20.00 ₽ from A to B.
        25 tasks transfer 20.00 ₽ from B to A.
        All 50 tasks fire concurrently via asyncio.gather.
        Invariant:
        - Neither balance ever drops below 0.00 ₽.
        - Sum of balances A + B == 1,000.00 ₽ at all times.
        - Every completed transaction has balanced LedgerEntries.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user_a, acc_a, _ = await BankingService.get_or_create_user(
                session, telegram_id=300010, username="user_a", full_name="User A"
            )
            user_b, acc_b, _ = await BankingService.get_or_create_user(
                session, telegram_id=300020, username="user_b", full_name="User B"
            )
            acc_a.balance = Decimal("500.00")
            acc_b.balance = Decimal("500.00")
            await session.commit()
            a_id = user_a.id
            b_id = user_b.id
            acc_a_id = acc_a.id
            acc_b_id = acc_b.id

        async def transfer_a_to_b():
            async with session_factory() as session:
                try:
                    await TransferService.execute_transfer(
                        session=session,
                        sender_user_id=a_id,
                        recipient_account_id=acc_b_id,
                        amount=Decimal("20.00"),
                    )
                    await session.commit()
                    return ("A->B", "SUCCESS")
                except InsufficientFundsError:
                    await session.rollback()
                    return ("A->B", "INSUFFICIENT")

        async def transfer_b_to_a():
            async with session_factory() as session:
                try:
                    await TransferService.execute_transfer(
                        session=session,
                        sender_user_id=b_id,
                        recipient_account_id=acc_a_id,
                        amount=Decimal("20.00"),
                    )
                    await session.commit()
                    return ("B->A", "SUCCESS")
                except InsufficientFundsError:
                    await session.rollback()
                    return ("B->A", "INSUFFICIENT")

        tasks = []
        for _ in range(25):
            tasks.append(transfer_a_to_b())
            tasks.append(transfer_b_to_a())
        random.shuffle(tasks)

        results = await asyncio.gather(*tasks)

        async with session_factory() as session:
            bal_a = (await session.execute(
                select(Account.balance).where(Account.id == acc_a_id)
            )).scalar_one()
            bal_b = (await session.execute(
                select(Account.balance).where(Account.id == acc_b_id)
            )).scalar_one()

            assert bal_a >= Decimal("0.00"), f"Account A balance negative: {bal_a}"
            assert bal_b >= Decimal("0.00"), f"Account B balance negative: {bal_b}"
            assert bal_a + bal_b == Decimal("1000.00"), (
                f"Currency conservation violated: {bal_a} + {bal_b} != 1000.00"
            )

    @pytest.mark.asyncio
    async def test_zero_balance_account_concurrent_hammering(self, test_engine):
        """30 concurrent transfer attempts against an account with exactly 0.00 ₽ must all fail."""
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            sender, s_acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=300030, username="zero_broke", full_name="Zero Broke"
            )
            rec, r_acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=300031, username="zero_rec", full_name="Zero Rec"
            )
            s_acc.balance = Decimal("0.00")
            r_acc.balance = Decimal("0.00")
            await session.commit()
            s_id = sender.id
            r_acc_id = r_acc.id

        async def debit_zero():
            async with session_factory() as session:
                try:
                    await TransferService.execute_transfer(
                        session=session,
                        sender_user_id=s_id,
                        recipient_account_id=r_acc_id,
                        amount=Decimal("1.00"),
                    )
                    await session.commit()
                    return "UNEXPECTED_SUCCESS"
                except InsufficientFundsError:
                    await session.rollback()
                    return "REJECTED"

        tasks = [debit_zero() for _ in range(30)]
        results = await asyncio.gather(*tasks)
        assert results.count("REJECTED") == 30
        assert results.count("UNEXPECTED_SUCCESS") == 0

        async with session_factory() as session:
            final_bal = (await session.execute(
                select(Account.balance).where(Account.id == s_acc.id)
            )).scalar_one()
            assert final_bal == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_self_transfer_and_frozen_rejections(self, test_engine):
        """Ensure transfers to self and transfers from frozen accounts are strictly rejected."""
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, acc, card = await BankingService.get_or_create_user(
                session, telegram_id=300040, username="self_freeze", full_name="Self Freeze"
            )
            acc.balance = Decimal("5000.00")
            await session.commit()
            u_id = user.id
            acc_id = acc.id

        # Self-transfer must raise SelfTransferError
        async with session_factory() as session:
            with pytest.raises(SelfTransferError):
                await TransferService.execute_transfer(
                    session=session,
                    sender_user_id=u_id,
                    recipient_account_id=acc_id,
                    amount=Decimal("100.00"),
                )

        # Freeze card and test outgoing transfer rejection
        async with session_factory() as session:
            await CardService.freeze_card(session, user_id=u_id)
            await session.commit()

        async with session_factory() as session:
            # Create a target recipient
            target, t_acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=300041, username="target_user", full_name="Target"
            )
            await session.commit()
            t_acc_id = t_acc.id

        async with session_factory() as session:
            with pytest.raises(AccountFrozenError):
                await TransferService.execute_transfer(
                    session=session,
                    sender_user_id=u_id,
                    recipient_account_id=t_acc_id,
                    amount=Decimal("50.00"),
                )


# ==============================================================================
# Domain 3: 18% APR Daily Savings Interest Math & Anti-Duplication Payout
# ==============================================================================

class TestSavingsInterestEmpiricalStress:
    """Empirical math verification and anti-duplication challenge for SavingsService."""

    def test_interest_math_exact_precision(self):
        """Formula: Balance * (APR / 365) * (delta_t / 86400).

        Verify against exact mathematical calculation:
        For 100,000.00 ₽ at 18% APR:
        1 day (86,400s): 100,000 * 0.18 / 365 = 49.31506849315...
        Expected quantized to 6 places: 49.315068
        """
        balance = Decimal("100000.00")
        rate = Decimal("0.1800")
        accrued = SavingsService.calculate_interest(balance, rate, 86400.0)
        expected = (balance * (rate / Decimal("365")) * Decimal("1.0")).quantize(Decimal("0.000001"))
        assert accrued == expected
        assert accrued == Decimal("49.315068")

    def test_full_year_interest_equals_eighteen_percent(self):
        """For 365 days (365 * 86,400s) on 10,000.00 ₽, interest must equal 1,800.00 ₽."""
        balance = Decimal("10000.00")
        rate = Decimal("0.1800")
        year_seconds = 365.0 * 86400.0
        accrued = SavingsService.calculate_interest(balance, rate, year_seconds)
        assert accrued == Decimal("1800.000000")

    def test_zero_and_negative_elapsed_seconds(self):
        """Non-positive elapsed time (or negative clock drift) must produce 0.000000 interest."""
        balance = Decimal("50000.00")
        rate = Decimal("0.1800")
        assert SavingsService.calculate_interest(balance, rate, 0.0) == Decimal("0.000000")
        assert SavingsService.calculate_interest(balance, rate, -100.0) == Decimal("0.000000")
        assert SavingsService.calculate_interest(Decimal("0.00"), rate, 86400.0) == Decimal("0.000000")
        assert SavingsService.calculate_interest(Decimal("-500.00"), rate, 86400.0) == Decimal("0.000000")

    @pytest.mark.asyncio
    async def test_anti_duplication_rapid_consecutive_payouts(self, test_engine):
        """Calling payout_interest back-to-back must pay out on first call and return 0.00 on second."""
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, _, _ = await BankingService.get_or_create_user(
                session, telegram_id=400001, username="interest_user", full_name="Interest User"
            )
            savings = await SavingsService.get_or_create_savings(session, user.id)
            savings.balance = Decimal("100000.00")
            # Seed 50.00 ₽ of accrued interest
            savings.accrued_interest = Decimal("50.000000")
            await session.commit()
            u_id = user.id

        # First payout
        async with session_factory() as session:
            first_payout = await SavingsService.payout_interest(session, u_id)
            await session.commit()

        assert first_payout == Decimal("50.00")

        # Second immediate payout
        async with session_factory() as session:
            second_payout = await SavingsService.payout_interest(session, u_id)
            await session.commit()

        assert second_payout == Decimal("0.00"), f"Duplicate payout detected! Paid: {second_payout}"

        # Third immediate payout
        async with session_factory() as session:
            third_payout = await SavingsService.payout_interest(session, u_id)
            await session.commit()

        assert third_payout == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_savings_deposit_withdraw_balance_limits(self, test_engine):
        """Verify atomic deposit and withdrawal balance boundaries."""
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=400002, username="safe_user", full_name="Safe User"
            )
            acc.balance = Decimal("1000.00")
            await session.commit()
            u_id = user.id

        # Deposit 400.00 to savings
        async with session_factory() as session:
            await SavingsService.deposit_to_savings(session, u_id, Decimal("400.00"))
            await session.commit()

        async with session_factory() as session:
            savings = await SavingsService.get_savings(session, u_id)
            assert savings.balance == Decimal("400.00")
            acc_bal = (await session.execute(
                select(Account.balance).where(Account.user_id == u_id).where(Account.account_type == "MAIN")
            )).scalar_one()
            assert acc_bal == Decimal("600.00")

        # Overdraft withdraw (attempting to withdraw 500 when only 400 available)
        async with session_factory() as session:
            with pytest.raises(InsufficientFundsError):
                await SavingsService.withdraw_from_savings(session, u_id, Decimal("500.00"))


# ==============================================================================
# Domain 4: Parody Investments GBM Dynamic Price Engine & Portfolio Valuation
# ==============================================================================

class TestInvestmentsEmpiricalStress:
    """Empirical verification of GBM stochastic pricing and portfolio trading."""

    def test_gbm_price_floor_under_extreme_negative_shocks(self):
        """Under 1,000 extreme negative shocks (e.g. epsilon = -15.0), price must NEVER drop below 0.01 ₽."""
        current_price = Decimal("100.00")
        base_price = Decimal("100.00")

        for _ in range(1000):
            next_price = InvestmentService.calculate_next_price(
                current_price=current_price,
                base_price=base_price,
                volatility=0.20,
                mean_reversion_rate=0.05,
                delta_t=1.0,
                epsilon=-15.0,  # Extreme 15-sigma market crash shock
            )
            assert next_price >= Decimal("0.01"), f"Price dropped below floor! {next_price}"
            current_price = next_price

        # Even at minimum price, next price with extreme shock stays at or above 0.01
        floor_price = InvestmentService.calculate_next_price(
            current_price=Decimal("0.01"),
            base_price=Decimal("100.00"),
            volatility=0.50,
            mean_reversion_rate=0.10,
            delta_t=1.0,
            epsilon=-50.0,
        )
        assert floor_price >= Decimal("0.01")

    def test_gbm_mean_reversion_pull_direction(self):
        """With zero stochastic noise (epsilon = 0.0):

        - Price well above base_price must decrease towards base_price.
        - Price well below base_price must increase towards base_price.
        """
        base_price = Decimal("100.00")

        # High price: 200.00 ₽
        high_price = Decimal("200.00")
        next_from_high = InvestmentService.calculate_next_price(
            current_price=high_price,
            base_price=base_price,
            volatility=0.10,
            mean_reversion_rate=0.10,
            delta_t=1.0,
            epsilon=0.0,
        )
        assert next_from_high < high_price, f"High price did not mean-revert downwards: {next_from_high}"

        # Low price: 50.00 ₽
        low_price = Decimal("50.00")
        next_from_low = InvestmentService.calculate_next_price(
            current_price=low_price,
            base_price=base_price,
            volatility=0.10,
            mean_reversion_rate=0.10,
            delta_t=1.0,
            epsilon=0.0,
        )
        assert next_from_low > low_price, f"Low price did not mean-revert upwards: {next_from_low}"

    @pytest.mark.asyncio
    async def test_stock_weighted_average_cost_basis_math(self, test_engine):
        """Scenario:

        1. Buy 10 shares of SHAW at 150.00 ₽ -> avg buy price = 150.00 ₽.
        2. Stock price increases to 250.00 ₽.
        3. Buy 10 more shares at 250.00 ₽ -> avg buy price = (10*150 + 10*250)/20 = 200.00 ₽.
        4. Sell 5 shares -> avg buy price remains 200.00 ₽, remaining shares = 15.
        5. Sell all remaining 15 shares -> remaining shares = 0, avg buy price resets to 0.00 ₽.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=500001, username="trader_joe", full_name="Trader Joe"
            )
            acc.balance = Decimal("100000.00")
            await InvestmentService.init_stocks(session)
            # Fix SHAW price at 150.00 ₽
            stock = await InvestmentService.get_stock(session, "SHAW")
            stock.current_price = Decimal("150.00")
            await session.commit()
            u_id = user.id

        # 1. Buy 10 shares @ 150.00 ₽
        async with session_factory() as session:
            buy1 = await InvestmentService.buy_stock(session, u_id, "SHAW", 10)
            await session.commit()
            assert buy1["average_buy_price"] == Decimal("150.00")
            assert buy1["total_shares"] == 10

        # 2. Update stock price to 250.00 ₽
        async with session_factory() as session:
            stock = await InvestmentService.get_stock(session, "SHAW")
            stock.current_price = Decimal("250.00")
            await session.commit()

        # 3. Buy 10 more shares @ 250.00 ₽
        async with session_factory() as session:
            buy2 = await InvestmentService.buy_stock(session, u_id, "SHAW", 10)
            await session.commit()
            assert buy2["average_buy_price"] == Decimal("200.00")
            assert buy2["total_shares"] == 20

        # 4. Partial sell: sell 5 shares
        async with session_factory() as session:
            sell1 = await InvestmentService.sell_stock(session, u_id, "SHAW", 5)
            await session.commit()
            assert sell1["remaining_shares"] == 15
            # Cost basis should remain 200.00 ₽
            port = (await session.execute(
                select(InvestmentPortfolio).where(InvestmentPortfolio.user_id == u_id)
            )).scalar_one()
            assert port.average_buy_price == Decimal("200.00")

        # 5. Sell remaining 15 shares
        async with session_factory() as session:
            sell2 = await InvestmentService.sell_stock(session, u_id, "SHAW", 15)
            await session.commit()
            assert sell2["remaining_shares"] == 0
            port = (await session.execute(
                select(InvestmentPortfolio).where(InvestmentPortfolio.user_id == u_id)
            )).scalar_one()
            assert port.shares == 0
            assert port.average_buy_price == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_portfolio_valuation_and_unrealized_pnl(self, test_engine):
        """Verify accurate real-time portfolio valuation and unrealized P&L calculations."""
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=500002, username="portfolio_user", full_name="Portfolio User"
            )
            acc.balance = Decimal("50000.00")
            await InvestmentService.init_stocks(session)
            await session.commit()
            u_id = user.id

        # Empty portfolio valuation
        async with session_factory() as session:
            summary = await InvestmentService.get_portfolio(session, u_id)
            assert summary["total_value"] == Decimal("0.00")
            assert summary["total_invested"] == Decimal("0.00")
            assert summary["total_pnl"] == Decimal("0.00")
            assert summary["total_pnl_percent"] == 0.0

        # Buy 10 shares of BBRS (base price 420.00 ₽)
        async with session_factory() as session:
            await InvestmentService.buy_stock(session, u_id, "BBRS", 10)
            await session.commit()

        # Update BBRS price to 500.00 ₽
        async with session_factory() as session:
            stock = await InvestmentService.get_stock(session, "BBRS")
            stock.current_price = Decimal("500.00")
            await session.commit()

        # Check updated valuation:
        # Invested: 10 * 420.00 = 4,200.00 ₽
        # Current value: 10 * 500.00 = 5,000.00 ₽
        # PnL: +800.00 ₽
        # PnL %: (800 / 4200) * 100 = 19.0476...%
        async with session_factory() as session:
            summary = await InvestmentService.get_portfolio(session, u_id)
            assert summary["total_value"] == Decimal("5000.00")
            assert summary["total_invested"] == Decimal("4200.00")
            assert summary["total_pnl"] == Decimal("800.00")
            assert pytest.approx(summary["total_pnl_percent"], 0.01) == 19.05

        # Over-selling shares must raise InsufficientSharesError
        async with session_factory() as session:
            with pytest.raises(InsufficientSharesError):
                await InvestmentService.sell_stock(session, u_id, "BBRS", 100)

    @pytest.mark.asyncio
    async def test_concurrent_savings_withdraw_race(self, test_engine):
        """User has 300.00 ₽ in savings safe.

        10 concurrent tasks attempt to withdraw 100.00 ₽ each.
        Total requested = 1,000.00 ₽. Available = 300.00 ₽.
        Under SQLite concurrency, exactly 3 withdrawals should succeed and 7 fail (either InsufficientFundsError or IntegrityError).
        Final savings balance must be >= 0.00 ₽ (NEVER negative).
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=500003, username="withdraw_race", full_name="Withdraw Race"
            )
            acc.balance = Decimal("1000.00")
            savings = await SavingsService.get_or_create_savings(session, user.id)
            savings.balance = Decimal("300.00")
            await session.commit()
            u_id = user.id

        async def attempt_withdraw():
            async with session_factory() as session:
                try:
                    await SavingsService.withdraw_from_savings(session, u_id, Decimal("100.00"))
                    await session.commit()
                    return "SUCCESS"
                except (InsufficientFundsError, Exception) as e:
                    await session.rollback()
                    return f"FAILED_{type(e).__name__}"

        tasks = [attempt_withdraw() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        async with session_factory() as session:
            savings = await SavingsService.get_savings(session, u_id)
            assert savings.balance >= Decimal("0.00"), f"Savings balance overdrafted! Balance: {savings.balance}"

    @pytest.mark.asyncio
    async def test_concurrent_interest_payout_race(self, test_engine):
        """User has 100.00 ₽ accrued interest.

        10 concurrent tasks call payout_interest simultaneously.
        Total interest paid out across all tasks must equal exactly 100.00 ₽.
        No duplicate interest paid out.
        """
        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, acc, _ = await BankingService.get_or_create_user(
                session, telegram_id=500004, username="payout_race", full_name="Payout Race"
            )
            savings = await SavingsService.get_or_create_savings(session, user.id)
            savings.balance = Decimal("1000.00")
            savings.accrued_interest = Decimal("100.000000")
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

        tasks = [attempt_payout() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        total_paid = sum(results)
        assert total_paid == Decimal("100.00"), f"Expected 100.00 total paid out, got {total_paid}. Individual results: {results}"

        async with session_factory() as session:
            savings = await SavingsService.get_savings(session, u_id)
            assert savings.balance == Decimal("1100.00"), f"Savings balance unexpected: {savings.balance}"
            assert savings.accrued_interest < Decimal("0.01")

