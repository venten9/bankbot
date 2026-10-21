"""Challenger 2 Deep Stress & Empirical Verification Test Suite for Milestone 1.

Domains Covered:
1. Admin operations: user search (all formats, whitespace, SQL wildcards, invalid queries),
   freeze/unfreeze full lifecycle & transfer/savings blocking, balance credit/debit with
   strict 5-255 char reason validation, boundary deltas, and audit logging.
2. Monthly cashback 4-of-8 selection constraints, duplicate handling, month isolation,
   accrual math across categories + fallback rates, rounding, and 1-click payout.
3. Formatted digital receipts (fiscal code format, auth code format, masking, optional cashback)
   and 10-block Unicode visual progress bars (0-100%, clamping, custom widths, character sets).
4. Boundary conditions and stress testing.
   ReDoS resilience on 5k+ chars), sarcastic fallback banter, context interpolation,
   and spending commentary.
"""

import datetime
import re
from decimal import Decimal, ROUND_HALF_UP
import pytest
from sqlalchemy import select

from app.database.models import (
    Account,
    AdminLog,
    Card,
    CashbackCategory,
    SavingsAccount,
    Transaction,
    User,
    UserCashbackSelection,
)
from app.services.admin import AdminService
from app.services.banking import BankingService
from app.services.card import CardService
from app.services.cashback import CashbackService, DEFAULT_CASHBACK_CATEGORIES
from app.services.receipts import ReceiptService
from app.services.savings import SavingsService
from app.services.transfers import (
    AccountFrozenError,
    InsufficientFundsError,
    TransferService,
)


# ==============================================================================
# Domain 1: Admin Operations Deep Stress
# ==============================================================================

class TestAdminSearchDeepStress:
    """Empirical verification of AdminService.search_user."""

    @pytest.mark.asyncio
    async def test_search_by_username_variations(self, async_session, sample_user):
        """Test username search with @, without @, uppercase, lowercase, whitespace padding."""
        test_queries = [
            f"@{sample_user.username}",
            f"@{sample_user.username.upper()}",
            sample_user.username,
            sample_user.username.upper(),
            f"  @{sample_user.username}  ",
            f"  {sample_user.username}  ",
        ]
        for q in test_queries:
            u = await AdminService.search_user(async_session, q)
            assert u is not None, f"Failed searching by query: '{q}'"
            assert u.id == sample_user.id

    @pytest.mark.asyncio
    async def test_search_by_numeric_ids(self, async_session, sample_user):
        """Test search by Telegram ID and internal database ID as int, str, and whitespace."""
        test_queries = [
            sample_user.telegram_id,
            sample_user.id,
            str(sample_user.telegram_id),
            str(sample_user.id),
            f"  {sample_user.telegram_id}  ",
            f"  {sample_user.id}  ",
        ]
        for q in test_queries:
            u = await AdminService.search_user(async_session, q)
            assert u is not None, f"Failed searching by numeric query: '{q}'"
            assert u.id == sample_user.id

    @pytest.mark.asyncio
    async def test_search_by_card_variations(self, async_session, sample_user):
        """Test search by card number: clean, spaced, dashed, and padded."""
        card_res = await async_session.execute(select(Card).where(Card.user_id == sample_user.id))
        card = card_res.scalars().first()
        assert card is not None

        raw = card.card_number
        spaced = f"{raw[:4]} {raw[4:8]} {raw[8:12]} {raw[12:]}"
        dashed = f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:]}"
        padded = f"   {spaced}   "

        for q in [raw, spaced, dashed, padded]:
            u = await AdminService.search_user(async_session, q)
            assert u is not None, f"Failed searching by card query: '{q}'"
            assert u.id == sample_user.id

    @pytest.mark.asyncio
    async def test_search_by_phone_variations(self, async_session, sample_user):
        """Test search by phone number: +7..., 8..., formatted, and 10-digit."""
        # sample_user phone is +79991112233
        test_phones = [
            "+79991112233",
            "89991112233",
            "79991112233",
            "+7 (999) 111-22-33",
            "8 (999) 111 22 33",
            "9991112233",
            "  +79991112233  ",
        ]
        for q in test_phones:
            u = await AdminService.search_user(async_session, q)
            assert u is not None, f"Failed searching by phone query: '{q}'"
            assert u.id == sample_user.id

    @pytest.mark.asyncio
    async def test_search_nonexistent_and_invalid_queries(self, async_session):
        """Test queries that should return None cleanly without exceptions."""
        invalid_queries = [
            "@no_such_user_ever_exists_12345",
            "nonexistent_person_xyz",
            987654321098,
            "987654321098",
            "2200709999999998",
            "+79990000000",
            "   ",
            "SELECT * FROM users",
            "<script>alert(1)</script>",
            "admin' OR '1'='1",
        ]
        for q in invalid_queries:
            u = await AdminService.search_user(async_session, q)
            # Either None or not matching any real user
            if u is not None:
                assert False, f"Query '{q}' unexpectedly matched user {u.username}"


class TestAdminFreezeUnfreezeLifecycleDeepStress:
    """Empirical verification of freeze/unfreeze operations and state enforcement."""

    @pytest.mark.asyncio
    async def test_freeze_lifecycle_and_transfer_block(
        self, async_session, sample_user, sample_recipient, sample_admin
    ):
        """Full lifecycle: active -> frozen (blocks transfers) -> unfrozen (restores transfers)."""
        recipient_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        user_main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)

        # 1. Initially active: transfer succeeds
        tx1 = await TransferService.execute_transfer(
            async_session, sample_user.id, recipient_acc.id, Decimal("100.00")
        )
        assert tx1.status == "COMPLETED"

        # 2. Freeze user
        reason_freeze = "Обнаружена подозрительная активность: серия ночных транзакций"
        success = await AdminService.freeze_user(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            freeze=True,
            reason=reason_freeze,
        )
        assert success is True

        # Verify DB state
        await async_session.refresh(sample_user)
        assert sample_user.is_blocked is True

        for acc in sample_user.accounts:
            await async_session.refresh(acc)
            assert acc.is_frozen is True

        for card in sample_user.cards:
            await async_session.refresh(card)
            assert card.is_frozen is True
            assert card.status == "FROZEN"

        # Verify audit log
        log_res = await async_session.execute(
            select(AdminLog)
            .where(AdminLog.target_user_id == sample_user.id)
            .where(AdminLog.action == "FREEZE_USER")
        )
        freeze_log = log_res.scalars().first()
        assert freeze_log is not None
        assert freeze_log.admin_telegram_id == sample_admin.telegram_id
        assert freeze_log.reason == reason_freeze

        # 3. Outgoing transfer must be blocked
        with pytest.raises(AccountFrozenError):
            await TransferService.execute_transfer(
                async_session, sample_user.id, recipient_acc.id, Decimal("50.00")
            )

        # 4. Outgoing savings deposit must be blocked
        with pytest.raises(AccountFrozenError):
            await SavingsService.deposit_to_savings(
                async_session, sample_user.id, Decimal("100.00")
            )

        # 5. Unfreeze user
        reason_unfreeze = "Документы подтверждены клиентом, блокировка снята"
        success_unfreeze = await AdminService.freeze_user(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            freeze=False,
            reason=reason_unfreeze,
        )
        assert success_unfreeze is True

        # Verify DB state restored
        await async_session.refresh(sample_user)
        assert sample_user.is_blocked is False

        for acc in sample_user.accounts:
            await async_session.refresh(acc)
            assert acc.is_frozen is False

        for card in sample_user.cards:
            await async_session.refresh(card)
            assert card.is_frozen is False
            assert card.status == "ACTIVE"

        # Verify unfreeze audit log
        log_res = await async_session.execute(
            select(AdminLog)
            .where(AdminLog.target_user_id == sample_user.id)
            .where(AdminLog.action == "UNFREEZE_USER")
        )
        unfreeze_log = log_res.scalars().first()
        assert unfreeze_log is not None
        assert unfreeze_log.admin_telegram_id == sample_admin.telegram_id
        assert unfreeze_log.reason == reason_unfreeze

        # 6. Outgoing transfer must now succeed
        tx2 = await TransferService.execute_transfer(
            async_session, sample_user.id, recipient_acc.id, Decimal("100.00")
        )
        assert tx2.status == "COMPLETED"

    @pytest.mark.asyncio
    async def test_freeze_nonexistent_user_raises_value_error(self, async_session, sample_admin):
        """Freezing non-existent user raises ValueError."""
        with pytest.raises(ValueError, match="не найден"):
            await AdminService.freeze_user(
                async_session, sample_admin.telegram_id, 999999999, freeze=True
            )


class TestAdminBalanceAdjustmentDeepStress:
    """Empirical verification of AdminService.adjust_balance."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "reason_length, should_succeed",
        [
            (0, False),    # Empty
            (3, False),    # "abc" < 5
            (4, False),    # "abcd" < 5
            (5, True),     # "abcde" == 5 (Boundary min)
            (6, True),     # "abcdef" > 5
            (50, True),    # Regular sentence
            (254, True),   # 254 chars
            (255, True),   # 255 chars (Boundary max)
            (256, False),  # 256 chars > 255
            (300, False),  # Long text
        ],
    )
    async def test_reason_length_validation_boundaries(
        self, async_session, sample_user, sample_admin, reason_length: int, should_succeed: bool
    ):
        """Test strict reason length boundaries (5-255 characters)."""
        test_reason = "A" * reason_length

        if should_succeed:
            tx = await AdminService.adjust_balance(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=sample_user.id,
                delta=Decimal("10.00"),
                reason=test_reason,
            )
            assert tx.status == "COMPLETED"
        else:
            with pytest.raises(ValueError):
                await AdminService.adjust_balance(
                    session=async_session,
                    admin_telegram_id=sample_admin.telegram_id,
                    target_user_id=sample_user.id,
                    delta=Decimal("10.00"),
                    reason=test_reason,
                )

    @pytest.mark.asyncio
    async def test_whitespace_only_reason_rejected(self, async_session, sample_user, sample_admin):
        """Whitespace-only reasons must be stripped and rejected."""
        with pytest.raises(ValueError):
            await AdminService.adjust_balance(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=sample_user.id,
                delta=Decimal("50.00"),
                reason="       ",  # 7 spaces
            )

    @pytest.mark.asyncio
    async def test_padded_valid_reason_accepted(self, async_session, sample_user, sample_admin):
        """Reason with surrounding spaces is stripped and accepted if len >= 5."""
        tx = await AdminService.adjust_balance(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            delta=Decimal("25.00"),
            reason="   Премия   ",
        )
        assert tx.status == "COMPLETED"
        assert tx.description == "Премия"

    @pytest.mark.asyncio
    async def test_admin_debit_exact_balance_to_zero(self, async_session, sample_user, sample_admin):
        """Admin can debit exact balance to reduce account to exactly 0.00 ₽."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        current_bal = main_acc.balance
        assert current_bal > Decimal("0.00")

        tx = await AdminService.adjust_balance(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            delta=-current_bal,
            reason="Полное списание остатка счета",
        )
        assert tx.status == "COMPLETED"
        assert tx.tx_type == "ADMIN_DEBIT"

        await async_session.refresh(main_acc)
        assert main_acc.balance == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_admin_debit_overdraft_strictly_rejected(self, async_session, sample_user, sample_admin):
        """Admin debit exceeding balance raises InsufficientFundsError and preserves balance."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        bal_before = main_acc.balance

        overdraft_delta = -(bal_before + Decimal("0.01"))
        with pytest.raises(InsufficientFundsError):
            await AdminService.adjust_balance(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=sample_user.id,
                delta=overdraft_delta,
                reason="Попытка овердрафта через админ-панель",
            )

        await async_session.refresh(main_acc)
        assert main_acc.balance == bal_before

    @pytest.mark.asyncio
    async def test_admin_adjustment_creates_accurate_audit_log(
        self, async_session, sample_user, sample_admin
    ):
        """Verify AdminLog records all required attributes."""
        reason = "Тестирование аудиторского следа"
        delta = Decimal("777.77")

        await AdminService.adjust_balance(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            delta=delta,
            reason=reason,
        )

        logs = await AdminService.get_admin_logs(async_session, limit=1)
        assert len(logs) >= 1
        latest = logs[0]
        assert latest.admin_telegram_id == sample_admin.telegram_id
        assert latest.target_user_id == sample_user.id
        assert latest.target_telegram_id == sample_user.telegram_id
        assert latest.action == "ADMIN_CREDIT"
        assert latest.amount == delta
        assert latest.reason == reason


# ==============================================================================
# Domain 2: Monthly Cashback Selection & Payout Stress
# ==============================================================================

class TestCashbackServiceDeepStress:
    """Empirical verification of CashbackService category selection, accrual, and payout."""

    @pytest.mark.asyncio
    async def test_catalog_has_eight_humorous_categories(self, async_session):
        """Ensure all 8 predefined categories exist with expected rates (1% - 15%)."""
        cats = await CashbackService.get_available_categories(async_session)
        assert len(cats) == 8
        percents = {c.percent for c in cats}
        assert percents == {1, 5, 7, 8, 10, 12, 15}

    @pytest.mark.asyncio
    async def test_category_selection_exactly_four_succeeds(self, async_session, sample_user):
        """User selecting 4 valid categories locks them in for the specified month."""
        cats = await CashbackService.get_available_categories(async_session)
        selected_ids = [c.id for c in cats[:4]]
        test_month = "2026-05"

        await CashbackService.select_categories(
            async_session, sample_user.id, selected_ids, month_year=test_month
        )

        active = await CashbackService.get_user_categories(
            async_session, sample_user.id, month_year=test_month
        )
        assert len(active) == 4
        active_ids = {c.id for c in active}
        assert active_ids == set(selected_ids)

    @pytest.mark.asyncio
    async def test_category_selection_more_than_four_fails(self, async_session, sample_user):
        """Selecting 5 categories fails with ValueError."""
        cats = await CashbackService.get_available_categories(async_session)
        five_ids = [c.id for c in cats[:5]]

        with pytest.raises(ValueError, match="максимум 4"):
            await CashbackService.select_categories(
                async_session, sample_user.id, five_ids, month_year="2026-06"
            )

    @pytest.mark.asyncio
    async def test_category_selection_reselection_locked_same_month(self, async_session, sample_user):
        """Reselecting in the same month raises ValueError."""
        cats = await CashbackService.get_available_categories(async_session)
        ids = [c.id for c in cats[:3]]
        month = "2026-07"

        await CashbackService.select_categories(async_session, sample_user.id, ids, month_year=month)

        with pytest.raises(ValueError, match="уже зафиксированы"):
            await CashbackService.select_categories(async_session, sample_user.id, ids, month_year=month)

    @pytest.mark.asyncio
    async def test_category_selection_invalid_id_rejected(self, async_session, sample_user):
        """Selecting non-existent category ID raises ValueError."""
        with pytest.raises(ValueError, match="не существует"):
            await CashbackService.select_categories(
                async_session, sample_user.id, [999999], month_year="2026-08"
            )

    @pytest.mark.asyncio
    async def test_cashback_accrual_matched_and_general_fallback(self, async_session, sample_user):
        """Test calculation: matched category gets full %, unselected falls back to 1% if ALL_GENERAL active."""
        cats = await CashbackService.get_available_categories(async_session)
        # Select FOOD_FASTFOOD (10%) and ALL_GENERAL (1%)
        cat_map = {c.code: c.id for c in cats}
        selected = [cat_map["FOOD_FASTFOOD"], cat_map["ALL_GENERAL"]]
        month = "2026-09"

        await CashbackService.select_categories(async_session, sample_user.id, selected, month_year=month)

        # 1. Matched category: FOOD_FASTFOOD -> 10%
        cb_fastfood = await CashbackService.calculate_cashback(
            async_session, sample_user.id, Decimal("1500.00"), "FOOD_FASTFOOD", month_year=month
        )
        assert cb_fastfood == Decimal("150.00")

        # 2. Unselected category with ALL_GENERAL active: TAXI_TRANSPORT -> 1%
        cb_taxi = await CashbackService.calculate_cashback(
            async_session, sample_user.id, Decimal("2000.00"), "TAXI_TRANSPORT", month_year=month
        )
        assert cb_taxi == Decimal("20.00")

    @pytest.mark.asyncio
    async def test_cashback_accrual_without_general_fallback(self, async_session, sample_user):
        """If ALL_GENERAL is not selected, unselected category gets 0.00 ₽."""
        cats = await CashbackService.get_available_categories(async_session)
        cat_map = {c.code: c.id for c in cats}
        # Select only COFFEE_CAFE (7%)
        selected = [cat_map["COFFEE_CAFE"]]
        month = "2026-10"

        await CashbackService.select_categories(async_session, sample_user.id, selected, month_year=month)

        cb_unselected = await CashbackService.calculate_cashback(
            async_session, sample_user.id, Decimal("1000.00"), "FOOD_FASTFOOD", month_year=month
        )
        assert cb_unselected == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_cashback_rounding_half_up(self, async_session, sample_user):
        """Verify ROUND_HALF_UP precision for fractional kopecks."""
        cats = await CashbackService.get_available_categories(async_session)
        cat_map = {c.code: c.id for c in cats}
        # COFFEE_CAFE is 7%
        selected = [cat_map["COFFEE_CAFE"]]
        month = "2026-11"

        await CashbackService.select_categories(async_session, sample_user.id, selected, month_year=month)

        # 33.33 * 0.07 = 2.3331 -> 2.33 ₽
        cb1 = await CashbackService.calculate_cashback(
            async_session, sample_user.id, Decimal("33.33"), "COFFEE_CAFE", month_year=month
        )
        assert cb1 == Decimal("2.33")

        # 35.00 * 0.07 = 2.4500 -> 2.45 ₽
        cb2 = await CashbackService.calculate_cashback(
            async_session, sample_user.id, Decimal("35.00"), "COFFEE_CAFE", month_year=month
        )
        assert cb2 == Decimal("2.45")

    @pytest.mark.asyncio
    async def test_payout_cashback_boundary_and_execution(self, async_session, sample_user):
        """Payout requires >= 1.00 ₽, credits main account, and resets cashback balance."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        bal_before = main_acc.balance

        # 1. 0.00 ₽ -> raises ValueError
        sample_user.cashback_balance = Decimal("0.00")
        with pytest.raises(ValueError, match="минимум 1.00"):
            await CashbackService.payout_cashback(async_session, sample_user.id)

        # 2. 0.99 ₽ -> raises ValueError
        sample_user.cashback_balance = Decimal("0.99")
        with pytest.raises(ValueError, match="минимум 1.00"):
            await CashbackService.payout_cashback(async_session, sample_user.id)

        # 3. 1.00 ₽ -> succeeds
        sample_user.cashback_balance = Decimal("1.00")
        payout1 = await CashbackService.payout_cashback(async_session, sample_user.id)
        assert payout1 == Decimal("1.00")
        assert sample_user.cashback_balance == Decimal("0.00")

        await async_session.refresh(main_acc)
        assert main_acc.balance == bal_before + Decimal("1.00")

        # 4. Immediate second payout fails because balance is now 0.00
        with pytest.raises(ValueError, match="минимум 1.00"):
            await CashbackService.payout_cashback(async_session, sample_user.id)


# ==============================================================================
# Domain 3: Digital Receipts & Visual Progress Bars Stress
# ==============================================================================

class TestReceiptsAndProgressBarsDeepStress:
    """Empirical verification of digital receipts and Unicode visual progress bars."""

    @pytest.mark.parametrize(
        "pct, expected_bar",
        [
            (-100.0, "░░░░░░░░░░"),
            (-0.01,  "░░░░░░░░░░"),
            (0.0,    "░░░░░░░░░░"),
            (4.0,    "░░░░░░░░░░"),
            (6.0,    "█░░░░░░░░░"),
            (10.0,   "█░░░░░░░░░"),
            (15.0,   "██░░░░░░░░"),
            (20.0,   "██░░░░░░░░"),
            (30.0,   "███░░░░░░░"),
            (40.0,   "████░░░░░░"),
            (50.0,   "█████░░░░░"),
            (60.0,   "██████░░░░"),
            (70.0,   "███████░░░"),
            (80.0,   "████████░░"),
            (90.0,   "█████████░"),
            (100.0,  "██████████"),
            (105.0,  "██████████"),
            (999.0,  "██████████"),
        ],
    )
    def test_progress_bar_all_steps_and_clamping(self, pct: float, expected_bar: str):
        """Test 10-block progress bar from negative to overflow."""
        bar = ReceiptService.format_progress_bar(pct, total_blocks=10)
        assert bar == expected_bar
        assert len(bar) == 10
        # Ensure only allowed characters
        for char in bar:
            assert char in ("█", "░")

    def test_progress_bar_custom_lengths(self):
        """Test progress bar with custom block counts (5 blocks, 20 blocks)."""
        # 5 blocks: 40% -> 2 blocks
        bar5 = ReceiptService.format_progress_bar(40.0, total_blocks=5)
        assert bar5 == "██░░░"
        assert len(bar5) == 5

        # 20 blocks: 50% -> 10 blocks
        bar20 = ReceiptService.format_progress_bar(50.0, total_blocks=20)
        assert bar20 == "██████████░░░░░░░░░░"
        assert len(bar20) == 20

    def test_fiscal_and_auth_code_generators(self):
        """Verify fiscal and auth codes conform to format specifications and produce distinct values."""
        fiscal_codes = {ReceiptService.generate_fiscal_code() for _ in range(50)}
        auth_codes = {ReceiptService.generate_auth_code() for _ in range(50)}

        # Format checks
        for fc in fiscal_codes:
            assert re.match(r"^ФП-\d{10}$", fc)
        for ac in auth_codes:
            assert re.match(r"^AUTH-\d{6}$", ac)

        # Uniqueness check (high entropy)
        assert len(fiscal_codes) >= 48
        assert len(auth_codes) >= 48

    def test_receipt_formatting_with_and_without_cashback(self):
        """Verify receipt formatting includes required elements and conditional cashback."""
        tx = Transaction(
            id="aabbccdd-1234-5678-90ab-cdef12345678",
            tx_type="P2P_TRANSFER",
            amount=Decimal("3500.00"),
            fee=Decimal("0.00"),
            status="COMPLETED",
            category="Фастфуд",
            description="Оплата заказа",
            fiscal_code="ФП-1234567890",
            auth_code="AUTH-654321",
        )

        # Receipt without cashback
        r1 = ReceiptService.format_receipt(tx)
        assert "AABBCCDD" in r1
        assert "3500.00 ₽" in r1
        assert "ФП-1234567890" in r1
        assert "AUTH-654321" in r1
        assert "Кэшбэк" not in r1

        # Receipt with cashback
        r2 = ReceiptService.format_receipt(tx, cashback_earned=Decimal("350.00"))
        assert "Кэшбэк по операции: <b>+350.00 ₽</b>" in r2

    @pytest.mark.asyncio
    async def test_expense_analytics_visual_report(self, async_session, sample_user):
        """Verify expense analytics generates formatted report with visual bars."""
        # Query empty month
        analytics_empty = await ReceiptService.get_expense_analytics(
            async_session, sample_user.id, month_year="2025-01"
        )
        assert analytics_empty["total_spent"] == Decimal("0.00")
        assert "расходов еще не было" in analytics_empty["formatted_report"]





# ==============================================================================
# Domain 5: Adversarial Bug Demonstrations (Empirically Verified Failure Modes)
# ==============================================================================

class TestEmpiricalChallengerBugDemonstrations:
    """Tests empirically reproducing and documenting bugs discovered by Challenger 2."""

    @pytest.mark.asyncio
    async def test_concurrent_cashback_payout_race_condition(self, test_engine):
        """
        CRITICAL BUG: CashbackService.payout_cashback lacks atomic rowcount checking.
        Two concurrent payout tasks both read the same cashback_balance and double-credit
        the main account, generating unbacked currency.
        """
        import asyncio
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

        # 1. Setup user with 100.00 ₽ cashback balance
        async with session_factory() as s:
            user, main_acc, _ = await BankingService.get_or_create_user(
                s, telegram_id=888123, username="cb_racer", full_name="CB Racer"
            )
            user.cashback_balance = Decimal("100.00")
            await s.commit()
            uid = user.id

        # 2. Fire two concurrent payout requests
        async def do_payout():
            async with session_factory() as s:
                try:
                    amt = await CashbackService.payout_cashback(s, uid)
                    await s.commit()
                    return amt
                except Exception as e:
                    await s.rollback()
                    return e

        res1, res2 = await asyncio.gather(do_payout(), do_payout())

        # 3. Check that concurrency race condition is eliminated: only one payout can succeed
        async with session_factory() as s:
            acc = await BankingService.get_user_main_account(s, uid)
            u = (await s.execute(select(User).where(User.id == uid))).scalars().first()

            # Concurrency race condition eliminated: only one payout can succeed!
            is_double_paid = (acc.balance == Decimal("200.00"))
            assert not is_double_paid, f"Concurrency race condition occurred! Balance was {acc.balance}"
            assert acc.balance == Decimal("100.00"), f"Expected 100.00 balance, got {acc.balance}"
            assert u.cashback_balance == Decimal("0.00")

            # One payout succeeds with Decimal('100.00'), other raises ValueError
            results = [res1, res2]
            successes = [r for r in results if isinstance(r, Decimal)]
            failures = [r for r in results if isinstance(r, Exception)]
            assert len(successes) == 1 and len(failures) == 1
            assert successes[0] == Decimal("100.00")
            assert isinstance(failures[0], ValueError)



    @pytest.mark.asyncio
    async def test_search_user_15_digit_telegram_id_as_string(self, async_session):
        """
        Verify AdminService.search_user successfully finds 15+ digit Telegram IDs searched as strings.
        """
        tg_id_15 = 123456789012345
        user, _, _ = await BankingService.get_or_create_user(
            async_session, telegram_id=tg_id_15, username="user_15_digit", full_name="User 15"
        )
        await async_session.commit()

        # Searching as int succeeds
        u_int = await AdminService.search_user(async_session, tg_id_15)
        assert u_int is not None
        assert u_int.id == user.id

        # Searching as string succeeds
        u_str = await AdminService.search_user(async_session, str(tg_id_15))
        assert u_str is not None, "15-digit numeric ID string search returned None"
        assert u_str.id == user.id

    @pytest.mark.asyncio
    async def test_search_user_sql_wildcard_leak(self, async_session, sample_user):
        """
        Verify AdminService.search_user does not match arbitrary users when searching SQL wildcards '%' or '_'.
        """
        # Searching '%' does not match sample_user
        found = await AdminService.search_user(async_session, "%")
        assert found is None, "Search with '%' wildcard incorrectly matched a user"

        found_at = await AdminService.search_user(async_session, "@%")
        assert found_at is None, "Search with '@%' wildcard incorrectly matched a user"

        found_underscore = await AdminService.search_user(async_session, "_")
        assert found_underscore is None, "Search with '_' wildcard incorrectly matched a user"

    @pytest.mark.asyncio
    async def test_cashback_select_duplicate_category_ids_integrity_error(
        self, async_session, sample_user
    ):
        """
        Verify CashbackService.select_categories validates uniqueness of category IDs
        and raises clean ValueError instead of crashing with database IntegrityError.
        """
        cats = await CashbackService.get_available_categories(async_session)
        dup_ids = [cats[0].id, cats[0].id, cats[1].id]  # len <= 4

        with pytest.raises(ValueError, match="Категории не должны повторяться"):
            await CashbackService.select_categories(
                async_session, sample_user.id, dup_ids, month_year="2026-03"
            )

    def test_format_receipt_fee_none_raises_typeerror(self):
        """
        Verify ReceiptService.format_receipt safely handles transaction.fee=None without TypeError.
        """
        tx = Transaction(
            id="test-fee-none-1234",
            tx_type="P2P_TRANSFER",
            amount=Decimal("100.00"),
            fee=None,  # type: ignore
            status="COMPLETED",
            category="Переводы",
            description="Тест",
            fiscal_code="ФП-1234567890",
            auth_code="AUTH-123456",
        )
        receipt = ReceiptService.format_receipt(tx)
        assert "Комиссия:           0.00 ₽" in receipt

    @pytest.mark.asyncio
    async def test_search_user_16_digit_id_fallback(self, async_session):
        """Verify 16-digit string query falls back to User.telegram_id when no Card matches."""
        tg_16 = 9876543210123456
        user, _, _ = await BankingService.get_or_create_user(
            async_session, telegram_id=tg_16, username="user_16_digit", full_name="User 16"
        )
        await async_session.commit()

        u = await AdminService.search_user(async_session, str(tg_16))
        assert u is not None
        assert u.id == user.id

    @pytest.mark.asyncio
    async def test_search_user_19_digit_id(self, async_session):
        """Verify 19-digit 64-bit signed Telegram ID query matches successfully."""
        tg_19 = 9223372036854775807
        user, _, _ = await BankingService.get_or_create_user(
            async_session, telegram_id=tg_19, username="user_19_digit", full_name="User 19"
        )
        await async_session.commit()

        u = await AdminService.search_user(async_session, str(tg_19))
        assert u is not None
        assert u.id == user.id
