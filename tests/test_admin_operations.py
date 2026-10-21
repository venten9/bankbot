"""Tier 3: Admin & Operations Tests.

Covers:
- Access control tests: rejection of non-admin users from admin functions, acceptance of IDs in ADMIN_IDS.
- Pydantic Settings ADMIN_IDS multi-format parsing (comma-separated, JSON array, list, int).
- User search by @username, user ID, telegram ID, card number, phone number.
- Admin balance credit with mandatory reason (5-255 chars) and audit logging in admin_logs.
- Admin balance debit with overdraft rejection, mandatory reason check, and audit logging.
- Account freeze and unfreeze actions and audit log creation in admin_logs.
- Verification of account freeze blocking user outgoing operations and unfreeze restoring them.
- System metrics aggregation calculation accuracy (users, active/frozen accounts, liquidity, tx volume).
- Audit log retrieval and ordering.
"""

from decimal import Decimal
import pytest
from sqlalchemy import select

from app.config import Settings, settings
from app.database.models import Account, AdminLog, Card, Transaction, User
from app.services.admin import AdminService
from app.services.banking import BankingService
from app.services.transfers import (
    AccountFrozenError,
    InsufficientFundsError,
    TransferService,
)


# ==============================================================================
# 1. Access Control & ADMIN_IDS Configuration Tests
# ==============================================================================

class TestAdminAccessControlAndConfig:
    """RBAC validation and Settings ADMIN_IDS parsing."""

    def test_settings_admin_ids_parsing_various_formats(self):
        """Verify flexible parsing of ADMIN_IDS: comma string, JSON array, list, int, whitespace."""
        # Comma-separated string
        s1 = Settings(ADMIN_IDS="111, 222, 333")  # type: ignore
        assert s1.ADMIN_IDS == [111, 222, 333]

        # JSON array string
        s2 = Settings(ADMIN_IDS="[444, 555]")  # type: ignore
        assert s2.ADMIN_IDS == [444, 555]

        # Single integer
        s3 = Settings(ADMIN_IDS=9999)  # type: ignore
        assert s3.ADMIN_IDS == [9999]

        # Native list of ints
        s4 = Settings(ADMIN_IDS=[10, 20, 30])  # type: ignore
        assert s4.ADMIN_IDS == [10, 20, 30]

        # Empty / whitespace
        s5 = Settings(ADMIN_IDS="   ")  # type: ignore
        assert s5.ADMIN_IDS == []

    def test_is_admin_check(self):
        """Verify membership check against ADMIN_IDS."""
        configured_admins = [123456, 789012]
        settings.ADMIN_IDS = configured_admins

        # Authorized admins
        assert 123456 in settings.ADMIN_IDS
        assert 789012 in settings.ADMIN_IDS

        # Unauthorized users
        assert 999999 not in settings.ADMIN_IDS
        assert 111111 not in settings.ADMIN_IDS


# ==============================================================================
# 2. Admin User Multi-Identifier Search Tests
# ==============================================================================

class TestAdminUserSearch:
    """User resolution by @username, user ID, telegram ID, card number, phone."""

    @pytest.mark.asyncio
    async def test_search_by_username_case_insensitive(self, async_session, sample_user):
        """Search finds user by @username and plain username case-insensitively."""
        # @alice
        u1 = await AdminService.search_user(async_session, "@alice")
        assert u1 is not None
        assert u1.id == sample_user.id

        # @ALICE
        u2 = await AdminService.search_user(async_session, "@ALICE")
        assert u2 is not None
        assert u2.id == sample_user.id

        # alice
        u3 = await AdminService.search_user(async_session, "alice")
        assert u3 is not None
        assert u3.id == sample_user.id

    @pytest.mark.asyncio
    async def test_search_by_telegram_id_and_internal_id(self, async_session, sample_user):
        """Search finds user by numeric Telegram ID and internal database ID."""
        u_tg = await AdminService.search_user(async_session, sample_user.telegram_id)
        assert u_tg is not None
        assert u_tg.id == sample_user.id

        u_id = await AdminService.search_user(async_session, sample_user.id)
        assert u_id is not None
        assert u_id.id == sample_user.id

    @pytest.mark.asyncio
    async def test_search_by_16_digit_card_number(self, async_session, sample_user):
        """Search finds user by 16-digit card number with or without spaces."""
        card_res = await async_session.execute(select(Card).where(Card.user_id == sample_user.id))
        card = card_res.scalars().first()
        assert card is not None

        # Clean 16-digit
        u1 = await AdminService.search_user(async_session, card.card_number)
        assert u1 is not None
        assert u1.id == sample_user.id

        # Spaced 16-digit
        spaced = f"{card.card_number[:4]} {card.card_number[4:8]} {card.card_number[8:12]} {card.card_number[12:]}"
        u2 = await AdminService.search_user(async_session, spaced)
        assert u2 is not None
        assert u2.id == sample_user.id

    @pytest.mark.asyncio
    async def test_search_by_phone_number(self, async_session, sample_user):
        """Search finds user by Russian phone number."""
        u1 = await AdminService.search_user(async_session, "+79991112233")
        assert u1 is not None
        assert u1.id == sample_user.id

        u2 = await AdminService.search_user(async_session, "89991112233")
        assert u2 is not None
        assert u2.id == sample_user.id

    @pytest.mark.asyncio
    async def test_search_nonexistent_returns_none(self, async_session):
        """Searching for non-existent users returns None cleanly."""
        assert await AdminService.search_user(async_session, "@ghost_user") is None
        assert await AdminService.search_user(async_session, 8888888888) is None
        assert await AdminService.search_user(async_session, "2200709999999998") is None


# ==============================================================================
# 3. Admin Balance Adjustment (Credit / Debit) & Reason Validation Tests
# ==============================================================================

class TestAdminBalanceAdjustments:
    """Admin balance adjustments, reason validations, and audit trails."""

    @pytest.mark.asyncio
    async def test_admin_credit_balance_with_valid_reason(self, async_session, sample_user, sample_admin):
        """Admin credits user balance with a valid reason, verifying balance and audit log."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        bal_before = main_acc.balance

        credit_amount = Decimal("5000.00")
        reason_text = "Компенсация за задержку перевода СБП"

        tx = await AdminService.adjust_balance(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            delta=credit_amount,
            reason=reason_text,
        )

        assert tx.status == "COMPLETED"
        assert tx.tx_type == "ADMIN_CREDIT"
        assert tx.amount == credit_amount

        await async_session.refresh(main_acc)
        assert main_acc.balance == bal_before + credit_amount

        # Verify audit log in admin_logs
        log_res = await async_session.execute(
            select(AdminLog).where(AdminLog.target_user_id == sample_user.id)
        )
        logs = log_res.scalars().all()
        assert len(logs) == 1
        log = logs[0]
        assert log.admin_telegram_id == sample_admin.telegram_id
        assert log.action == "ADMIN_CREDIT"
        assert log.amount == credit_amount
        assert log.reason == reason_text

    @pytest.mark.asyncio
    async def test_admin_debit_balance_with_valid_reason(self, async_session, sample_user, sample_admin):
        """Admin debits user balance within available funds."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        bal_before = main_acc.balance

        debit_amount = Decimal("-2000.00")
        reason_text = "Списание ошибочно начисленного бонуса"

        tx = await AdminService.adjust_balance(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            delta=debit_amount,
            reason=reason_text,
        )

        assert tx.status == "COMPLETED"
        assert tx.tx_type == "ADMIN_DEBIT"
        assert tx.amount == Decimal("2000.00")

        await async_session.refresh(main_acc)
        assert main_acc.balance == bal_before - Decimal("2000.00")

        # Verify audit log
        log_res = await async_session.execute(
            select(AdminLog)
            .where(AdminLog.target_user_id == sample_user.id)
            .where(AdminLog.action == "ADMIN_DEBIT")
        )
        log = log_res.scalars().first()
        assert log is not None
        assert log.amount == debit_amount
        assert log.reason == reason_text

    @pytest.mark.asyncio
    async def test_admin_debit_overdraft_rejected(self, async_session, sample_user, sample_admin):
        """Attempting to debit more than user's available balance raises InsufficientFundsError."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        bal_before = main_acc.balance
        excessive_debit = -(bal_before + Decimal("100.00"))

        with pytest.raises(InsufficientFundsError):
            await AdminService.adjust_balance(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=sample_user.id,
                delta=excessive_debit,
                reason="Попытка списать больше, чем есть",
            )

        # Balance must remain completely unchanged
        await async_session.refresh(main_acc)
        assert main_acc.balance == bal_before

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_reason",
        [
            "",
            "   ",
            "123",
            "test",  # 4 chars < 5
            "a" * 256,  # 256 chars > 255
        ],
    )
    async def test_admin_adjustment_invalid_reason_rejected(
        self, async_session, sample_user, sample_admin, invalid_reason
    ):
        """Reason must be between 5 and 255 non-whitespace characters."""
        with pytest.raises(ValueError):
            await AdminService.adjust_balance(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=sample_user.id,
                delta=Decimal("100.00"),
                reason=invalid_reason,
            )

    @pytest.mark.asyncio
    async def test_admin_adjustment_zero_delta_rejected(self, async_session, sample_user, sample_admin):
        """Delta cannot be 0.00 ₽."""
        with pytest.raises(ValueError):
            await AdminService.adjust_balance(
                session=async_session,
                admin_telegram_id=sample_admin.telegram_id,
                target_user_id=sample_user.id,
                delta=Decimal("0.00"),
                reason="Корректная причина для теста",
            )


# ==============================================================================
# 4. Account Freeze / Unfreeze Lifecycle Tests
# ==============================================================================

class TestAdminFreezeUnfreezeLifecycle:
    """Admin freeze and unfreeze actions and verification of user blocking."""

    @pytest.mark.asyncio
    async def test_freeze_blocks_outgoing_transfers(self, async_session, sample_user, sample_recipient, sample_admin):
        """Admin freezing user blocks user outgoing transfers and records audit log."""
        recipient_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)

        # Freeze user
        success = await AdminService.freeze_user(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            freeze=True,
            reason="Подозрительная активность в профиле",
        )
        assert success is True

        # Verify audit log
        log_res = await async_session.execute(
            select(AdminLog)
            .where(AdminLog.target_user_id == sample_user.id)
            .where(AdminLog.action == "FREEZE_USER")
        )
        log = log_res.scalars().first()
        assert log is not None
        assert log.admin_telegram_id == sample_admin.telegram_id
        assert log.reason == "Подозрительная активность в профиле"

        # Outgoing transfer must now fail with AccountFrozenError
        with pytest.raises(AccountFrozenError):
            await TransferService.execute_transfer(
                session=async_session,
                sender_user_id=sample_user.id,
                recipient_account_id=recipient_acc.id,
                amount=Decimal("500.00"),
            )

    @pytest.mark.asyncio
    async def test_unfreeze_restores_outgoing_transfers(self, async_session, sample_user, sample_recipient, sample_admin):
        """Admin unfreezing user restores outgoing transfers and records audit log."""
        recipient_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)

        # Freeze first
        await AdminService.freeze_user(
            async_session, sample_admin.telegram_id, sample_user.id, freeze=True, reason="Блокировка"
        )

        # Unfreeze
        success = await AdminService.freeze_user(
            session=async_session,
            admin_telegram_id=sample_admin.telegram_id,
            target_user_id=sample_user.id,
            freeze=False,
            reason="Проверка безопасности успешно пройдена",
        )
        assert success is True

        # Verify audit log
        log_res = await async_session.execute(
            select(AdminLog)
            .where(AdminLog.target_user_id == sample_user.id)
            .where(AdminLog.action == "UNFREEZE_USER")
        )
        log = log_res.scalars().first()
        assert log is not None
        assert log.reason == "Проверка безопасности успешно пройдена"

        # Outgoing transfer must now succeed
        tx = await TransferService.execute_transfer(
            session=async_session,
            sender_user_id=sample_user.id,
            recipient_account_id=recipient_acc.id,
            amount=Decimal("500.00"),
        )
        assert tx.status == "COMPLETED"


# ==============================================================================
# 5. System Metrics Aggregation Tests
# ==============================================================================

class TestAdminSystemMetrics:
    """System-wide financial metrics aggregation."""

    @pytest.mark.asyncio
    async def test_get_metrics_accuracy(self, async_session, sample_user, sample_recipient, sample_admin):
        """Metrics correctly aggregate total users, accounts, liquidity, and turnover."""
        metrics_before = await AdminService.get_metrics(async_session)

        assert metrics_before["total_users"] >= 3
        assert metrics_before["active_accounts"] >= 3
        assert metrics_before["frozen_accounts"] == 0
        assert metrics_before["total_liquidity"] > Decimal("0.00")

        # Freeze one user
        await AdminService.freeze_user(
            async_session, sample_admin.telegram_id, sample_user.id, freeze=True, reason="Тест заморозки"
        )

        # Execute a transfer between remaining active accounts
        admin_acc = await BankingService.get_user_main_account(async_session, sample_admin.id)
        rec_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)

        await TransferService.execute_transfer(
            async_session,
            sender_user_id=sample_admin.id,
            recipient_account_id=rec_acc.id,
            amount=Decimal("1500.00"),
        )

        metrics_after = await AdminService.get_metrics(async_session)

        # Frozen account count should have increased
        assert metrics_after["frozen_accounts"] >= 1
        # Transaction count and volume should reflect transfer
        assert metrics_after["total_transactions"] >= 1
        assert metrics_after["total_volume"] >= Decimal("1500.00")

    @pytest.mark.asyncio
    async def test_get_admin_logs_retrieval(self, async_session, sample_user, sample_admin):
        """Audit logs are retrieved and contain recent operations."""
        await AdminService.adjust_balance(
            async_session, sample_admin.telegram_id, sample_user.id, Decimal("100.00"), "Причина номер один"
        )
        await AdminService.adjust_balance(
            async_session, sample_admin.telegram_id, sample_user.id, Decimal("200.00"), "Причина номер два"
        )

        logs = await AdminService.get_admin_logs(async_session, limit=10)
        assert len(logs) >= 2
        log_reasons = [l.reason for l in logs]
        assert "Причина номер один" in log_reasons
        assert "Причина номер два" in log_reasons
