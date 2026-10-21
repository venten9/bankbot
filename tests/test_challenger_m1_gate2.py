"""
Empirical Challenger Verification Test Suite for Milestone 1 Gate 2.
Adversarially tests:
1. AdminService.search_user (15-20 digit Telegram IDs, card fallback, SQL wildcard escaping).
3. CashbackService.select_categories (duplicate category rejection with clean ValueError).
4. ReceiptService.format_receipt (fee=None handling, decimal precision).
"""

import datetime
from decimal import Decimal
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.database.models import Account, Card, Transaction, User
from app.services.admin import AdminService
from app.services.banking import BankingService
from app.services.cashback import CashbackService
from app.services.receipts import ReceiptService





class TestAdminSearchEmpirical:
    """Empirically stress-test AdminService.search_user."""

    @pytest.mark.asyncio
    async def test_search_user_15_to_19_digit_telegram_ids(self, async_session):
        """Verify AdminService.search_user correctly handles 15, 16, 17, 18, and 19 digit valid 64-bit Telegram IDs."""
        test_ids = [
            123456789012345,        # 15 digits
            1234567890123456,       # 16 digits
            12345678901234567,      # 17 digits
            123456789012345678,     # 18 digits
            9223372036854775807,    # 19 digits (max signed int64)
        ]

        created_users = []
        for i, tid in enumerate(test_ids):
            user, _, _ = await BankingService.get_or_create_user(
                async_session,
                telegram_id=tid,
                username=f"user_digits_{len(str(tid))}_{i}",
                full_name=f"User {len(str(tid))} digits",
            )
            created_users.append((tid, user.id))
        await async_session.commit()

        for tid, expected_user_id in created_users:
            # Search as int
            found_int = await AdminService.search_user(async_session, tid)
            assert found_int is not None, f"Failed to find user with {len(str(tid))}-digit ID as int: {tid}"
            assert found_int.id == expected_user_id

            # Search as string
            found_str = await AdminService.search_user(async_session, str(tid))
            assert found_str is not None, f"Failed to find user with {len(str(tid))}-digit ID as string: '{tid}'"
            assert found_str.id == expected_user_id

    @pytest.mark.asyncio
    async def test_search_user_20_digit_query_unhandled_overflow_crash(self, async_session):
        """
        Verify robustness against 20-digit ID queries.
        Requirement 2 states: 'Verify AdminService.search_user handles 15-20 digit Telegram IDs'.
        app/services/admin.py:111 explicitly accepts `len(clean_str) <= 20`:
            if clean_str.isdigit() and len(clean_str) <= 20:
                num = int(clean_str)
                res = await session.execute(...)
        However, SQLite INTEGER supports only signed 64-bit integers (max 9223372036854775807, 19 digits).
        Any 20-digit number (e.g. '10000000000000000001') or 19-digit number > 2^63-1 crashes
        with an uncaught OverflowError rather than safely returning None!
        """
        # When 21 digits are queried, len > 20, so numeric check is skipped and it safely returns None:
        res_21 = await AdminService.search_user(async_session, "100000000000000000001")
        assert res_21 is None

        # When 20 digits are queried, it enters `len <= 20` and crashes with unhandled OverflowError!
        # Expected correct behavior for a robust search service: return None (user not found).
        # Actual behavior: raises OverflowError: Python int too large to convert to SQLite INTEGER.
        res_20 = await AdminService.search_user(async_session, "10000000000000000001")
        assert res_20 is None

    @pytest.mark.asyncio
    async def test_search_user_16_digit_card_vs_userid_fallback(self, async_session):
        """
        Verify card-vs-user-ID precedence and fallback behavior for 16-digit queries:
        1. If a 16-digit number matches a Card.card_number, return the card's user.
        2. If a 16-digit number does NOT match any card, fallback to matching User.telegram_id.
        """
        # User 1: Has a card with card_number "2200701111222233"
        user1, _, card1 = await BankingService.get_or_create_user(
            async_session,
            telegram_id=500001,
            username="card_holder_user",
            full_name="Card Holder",
        )
        card1.card_number = "2200701111222233"

        # User 2: Has telegram_id matching card1's number ("2200701111222233")
        # In card lookup precedence, searching card1's number returns user1 (the card owner)
        user2, _, _ = await BankingService.get_or_create_user(
            async_session,
            telegram_id=2200701111222233,
            username="same_id_as_card1",
            full_name="Same ID As Card1",
        )

        # User 3: Has 16-digit telegram_id "2200709999888877" and NO card exists with this number
        user3, _, card3 = await BankingService.get_or_create_user(
            async_session,
            telegram_id=2200709999888877,
            username="no_matching_card_user",
            full_name="No Card Match",
        )
        card3.card_number = "2200704444555566"  # distinct card number

        await async_session.commit()

        # Search for "2200701111222233" -> Card match takes precedence -> returns user1
        found_card_match = await AdminService.search_user(async_session, "2200701111222233")
        assert found_card_match is not None
        assert found_card_match.id == user1.id

        # Search for "2200709999888877" (formatted with spaces) -> No card matches -> falls back to user3
        found_fallback_spaces = await AdminService.search_user(async_session, "2200 7099 9988 8877")
        assert found_fallback_spaces is not None
        assert found_fallback_spaces.id == user3.id

        # Search for "2200-7099-9988-8877" (with dashes) -> No card matches -> falls back to user3
        found_fallback_dashes = await AdminService.search_user(async_session, "2200-7099-9988-8877")
        assert found_fallback_dashes is not None
        assert found_fallback_dashes.id == user3.id

        # Search for "2200709999888877" (plain) -> No card matches -> falls back to user3
        found_fallback_plain = await AdminService.search_user(async_session, "2200709999888877")
        assert found_fallback_plain is not None
        assert found_fallback_plain.id == user3.id

    @pytest.mark.asyncio
    async def test_search_user_sql_wildcard_leak_prevention(self, async_session):
        """
        Verify search_user eliminates SQL wildcard leaks:
        '%' and '_' characters must NOT act as wildcards matching arbitrary users.
        """
        user_alpha, _, _ = await BankingService.get_or_create_user(
            async_session, telegram_id=600001, username="alpha_client", full_name="Alpha Client"
        )
        user_beta, _, _ = await BankingService.get_or_create_user(
            async_session, telegram_id=600002, username="beta_client", full_name="Beta Client"
        )
        # Create users with literal wildcard symbols in usernames
        user_wild_pct, _, _ = await BankingService.get_or_create_user(
            async_session, telegram_id=600003, username="wild%user", full_name="Percent Client"
        )
        user_wild_und, _, _ = await BankingService.get_or_create_user(
            async_session, telegram_id=600004, username="wild_user", full_name="Underscore Client"
        )
        await async_session.commit()

        # 1. Bare wildcards must return None, not match alpha_client or beta_client
        assert await AdminService.search_user(async_session, "%") is None
        assert await AdminService.search_user(async_session, "@%") is None
        assert await AdminService.search_user(async_session, "_") is None
        assert await AdminService.search_user(async_session, "@_") is None

        # 2. Substrings with wildcards must NOT do prefix/wildcard matching
        assert await AdminService.search_user(async_session, "alpha%") is None
        assert await AdminService.search_user(async_session, "@alpha%") is None
        assert await AdminService.search_user(async_session, "beta_") is None
        assert await AdminService.search_user(async_session, "%client") is None

        # 3. Exact matching of literal wildcard names MUST work
        found_pct = await AdminService.search_user(async_session, "wild%user")
        assert found_pct is not None
        assert found_pct.id == user_wild_pct.id

        found_pct_at = await AdminService.search_user(async_session, "@wild%user")
        assert found_pct_at is not None
        assert found_pct_at.id == user_wild_pct.id

        found_und = await AdminService.search_user(async_session, "wild_user")
        assert found_und is not None
        assert found_und.id == user_wild_und.id

        # 4. Whitespace and empty strings return None
        assert await AdminService.search_user(async_session, "") is None
        assert await AdminService.search_user(async_session, "   ") is None


class TestCashbackDuplicateCategoriesEmpirical:
    """Empirically stress-test CashbackService.select_categories duplicate handling."""

    @pytest.mark.asyncio
    async def test_select_categories_rejects_duplicate_ids(self, async_session, sample_user):
        """Verify duplicate category IDs raise clean ValueError without crashing or DB corruption."""
        categories = await CashbackService.get_available_categories(async_session)
        assert len(categories) >= 4, "Expected at least 4 available cashback categories"
        c0, c1, c2, c3 = categories[0].id, categories[1].id, categories[2].id, categories[3].id

        duplicate_cases = [
            [c0, c0],
            [c0, c0, c1],
            [c0, c1, c0],
            [c0, c1, c2, c0],
            [c0, c0, c0, c0],
            [c1, c2, c2, c3],
        ]

        for dup_list in duplicate_cases:
            with pytest.raises(ValueError, match="Категории не должны повторяться"):
                await CashbackService.select_categories(
                    async_session,
                    user_id=sample_user.id,
                    category_ids=dup_list,
                    month_year="2026-07",
                )

        # Verify database has 0 selections recorded for this month
        current_cats = await CashbackService.get_user_categories(
            async_session, sample_user.id, month_year="2026-07"
        )
        assert len(current_cats) == 0, "No duplicate categories should have been committed"

        # Verify that a subsequent valid distinct selection succeeds cleanly
        await CashbackService.select_categories(
            async_session,
            user_id=sample_user.id,
            category_ids=[c0, c1, c2, c3],
            month_year="2026-07",
        )
        saved_cats = await CashbackService.get_user_categories(
            async_session, sample_user.id, month_year="2026-07"
        )
        assert len(saved_cats) == 4

    @pytest.mark.asyncio
    async def test_select_categories_boundary_conditions(self, async_session, sample_user):
        """Verify category selection bounds (>4 categories, invalid IDs)."""
        categories = await CashbackService.get_available_categories(async_session)
        valid_ids = [c.id for c in categories]

        # More than 4 categories raises ValueError
        if len(valid_ids) >= 5:
            with pytest.raises(ValueError, match="максимум 4"):
                await CashbackService.select_categories(
                    async_session, sample_user.id, valid_ids[:5], month_year="2026-08"
                )

        # Non-existent category ID raises ValueError
        with pytest.raises(ValueError, match="не существует"):
            await CashbackService.select_categories(
                async_session, sample_user.id, [999999], month_year="2026-08"
            )


class TestReceiptServiceFeeHandlingEmpirical:
    """Empirically stress-test ReceiptService.format_receipt handling of fee."""

    def test_format_receipt_fee_none_and_values(self):
        """Verify format_receipt handles fee=None, fee=0.00, and fee=Decimal values without TypeError."""
        # 1. fee=None
        tx_none = Transaction(
            id="tx-fee-none-empirical",
            tx_type="P2P_TRANSFER",
            amount=Decimal("500.00"),
            fee=None,
            status="COMPLETED",
            category="Переводы",
            description="Оплата услуг",
            fiscal_code="ФП-999888777",
            auth_code="AUTH-555444",
        )
        receipt_none = ReceiptService.format_receipt(tx_none)
        assert "Комиссия:           0.00 ₽" in receipt_none
        assert "Сумма операции:     <b>500.00 ₽</b>" in receipt_none
        assert "✅ Исполнено" in receipt_none

        # 2. fee=Decimal("0.00")
        tx_zero = Transaction(
            id="tx-fee-zero-empirical",
            tx_type="P2P_TRANSFER",
            amount=Decimal("1500.00"),
            fee=Decimal("0.00"),
            status="COMPLETED",
            category="Переводы",
            description="Возврат долга",
            fiscal_code="ФП-111222333",
            auth_code="AUTH-111222",
        )
        receipt_zero = ReceiptService.format_receipt(tx_zero)
        assert "Комиссия:           0.00 ₽" in receipt_zero

        # 3. fee=Decimal("45.50")
        tx_fee = Transaction(
            id="tx-fee-positive-empirical",
            tx_type="CARD_TRANSFER",
            amount=Decimal("2000.00"),
            fee=Decimal("45.50"),
            status="COMPLETED",
            category="Переводы на карту",
            description="Комиссионный перевод",
            fiscal_code="ФП-444555666",
            auth_code="AUTH-777888",
        )
        receipt_fee = ReceiptService.format_receipt(tx_fee)
        assert "Комиссия:           45.50 ₽" in receipt_fee

        # 4. Cashback display
        receipt_with_cashback = ReceiptService.format_receipt(
            tx_none, cashback_earned=Decimal("25.00")
        )
        assert "💛 Кэшбэк по операции: <b>+25.00 ₽</b>" in receipt_with_cashback
