"""Administrative Management Service: Metrics Aggregation, User Ops, and Audited Adjustments."""

import asyncio
import datetime
import logging
import random
import re
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)

from app.config import settings
from app.database.models import (
    Account,
    AdminLog,
    Card,
    InvestmentPortfolio,
    LedgerEntry,
    SavingsAccount,
    Stock,
    Transaction,
    User,
    UserCashbackSelection,
    UserJob,
    UserProperty,
    SportsBet,
    PvPDuel,
    SportsMatch,
)
from app.services.card import CardService
from app.services.investments import InvestmentService
from app.services.property import PROPERTY_CATALOG, PropertyService, _PROPERTY_INDEX
from app.services.transfers import InsufficientFundsError



class AdminService:
    """Service providing back-office metrics aggregation, user search, and audited adjustments."""

    @classmethod
    async def get_metrics(cls, session: AsyncSession) -> dict:
        """Aggregate real-time system metrics: users, active/frozen accounts, liquidity, tx volume."""
        # 1. Total registered users
        user_cnt = (await session.execute(select(func.count(User.id)))).scalar_one() or 0

        # 2. Active vs frozen accounts
        active_acc_cnt = (
            await session.execute(
                select(func.count(Account.id)).where(Account.is_frozen == False)  # noqa: E712
            )
        ).scalar_one() or 0

        frozen_acc_cnt = (
            await session.execute(
                select(func.count(Account.id)).where(Account.is_frozen == True)  # noqa: E712
            )
        ).scalar_one() or 0

        # 3. Total liquidity: sum of Account balances + SavingsAccount balances
        accounts_bal = (
            await session.execute(select(func.sum(Account.balance)))
        ).scalar_one() or Decimal("0.00")
        savings_bal = (
            await session.execute(select(func.sum(SavingsAccount.balance)))
        ).scalar_one() or Decimal("0.00")
        total_liquidity = accounts_bal + savings_bal

        # 4. Total transactions and total volume
        tx_cnt = (await session.execute(select(func.count(Transaction.id)))).scalar_one() or 0
        tx_vol = (
            await session.execute(
                select(func.sum(Transaction.amount)).where(Transaction.status == "COMPLETED")
            )
        ).scalar_one() or Decimal("0.00")

        return {
            "total_users": user_cnt,
            "active_accounts": active_acc_cnt,
            "frozen_accounts": frozen_acc_cnt,
            "total_liquidity": total_liquidity,
            "total_transactions": tx_cnt,
            "total_volume": tx_vol,
        }

    @classmethod
    async def search_user(cls, session: AsyncSession, query: str | int) -> User | None:
        """Search user by @username, user ID, telegram ID, 16-digit card, or phone number."""
        base_query = select(User).options(
            selectinload(User.accounts),
            selectinload(User.cards),
            selectinload(User.savings_account),
        )

        if isinstance(query, int):
            res = await session.execute(
                base_query.where((User.telegram_id == query) | (User.id == query))
            )
            return res.scalars().first()

        clean_str = str(query).strip()
        if not clean_str:
            return None

        # 16-digit Card Number (with fallback to numeric user ID if no card matches)
        stripped_card = clean_str.replace(" ", "").replace("-", "")
        if len(stripped_card) == 16 and stripped_card.isdigit():
            card_res = await session.execute(
                select(Card.user_id).where(Card.card_number == stripped_card)
            )
            user_id = card_res.scalar_one_or_none()
            if user_id:
                res = await session.execute(base_query.where(User.id == user_id))
                return res.scalars().first()
            # Fallback: if no card matches, match 16-digit number as User.telegram_id or User.id
            num_card = int(stripped_card)
            res = await session.execute(
                base_query.where((User.telegram_id == num_card) | (User.id == num_card))
            )
            u = res.scalars().first()
            if u:
                return u

        # Numeric ID (Telegram ID or internal User ID, up to 20 digits, e.g. 1-15, 17-20 digits)
        if clean_str.isdigit() and len(clean_str) <= 20:
            try:
                num = int(clean_str)
                if num > 9223372036854775807 or num < -9223372036854775808:
                    return None
                res = await session.execute(
                    base_query.where((User.telegram_id == num) | (User.id == num))
                )
                u = res.scalars().first()
                if u:
                    return u
            except (OverflowError, ValueError):
                return None

        # Username with @ (exact case-insensitive match, safe against wildcards)
        if clean_str.startswith("@"):
            clean_username = clean_str[1:]
            res = await session.execute(
                base_query.where(func.lower(User.username) == clean_username.lower())
            )
            u = res.scalars().first()
            if u:
                return u

        # Phone number
        phone_digits = re.sub(r"\D", "", clean_str)
        if len(phone_digits) in (10, 11):
            norm_phone = f"+7{phone_digits[-10:]}"
            res = await session.execute(
                base_query.where(User.phone_number == norm_phone)
            )
            u = res.scalars().first()
            if u:
                return u

        # Plain username (exact case-insensitive match, safe against wildcards)
        res = await session.execute(
            base_query.where(func.lower(User.username) == clean_str.lower())
        )
        return res.scalars().first()

    @classmethod
    async def freeze_user(
        cls,
        session: AsyncSession,
        admin_telegram_id: int,
        target_user_id: int,
        freeze: bool = True,
        reason: str = "Решение администрации",
    ) -> bool:
        """Freeze or unfreeze all accounts and cards of a user with mandatory audit logging."""
        user_res = await session.execute(
            select(User)
            .where(User.id == target_user_id)
            .options(selectinload(User.accounts), selectinload(User.cards))
        )
        user = user_res.scalars().first()
        if not user:
            raise ValueError(f"Пользователь {target_user_id} не найден.")

        user.is_blocked = freeze

        # Freeze/unfreeze all user accounts
        for acc in user.accounts:
            acc.is_frozen = freeze

        # Freeze/unfreeze all user cards
        for card in user.cards:
            card.is_frozen = freeze
            card.status = "FROZEN" if freeze else "ACTIVE"

        # Record admin audit log
        log_entry = AdminLog(
            admin_telegram_id=admin_telegram_id,
            target_telegram_id=user.telegram_id,
            target_user_id=user.id,
            action="FREEZE_USER" if freeze else "UNFREEZE_USER",
            reason=reason,
        )
        session.add(log_entry)
        await session.flush()

        return True

    @classmethod
    async def adjust_balance(
        cls,
        session: AsyncSession,
        admin_telegram_id: int,
        target_user_id: int,
        delta: Decimal | float | int,
        reason: str,
    ) -> Transaction:
        """Credit or debit user balance with mandatory reason validation (5-255 chars) and audit logging."""
        # 1. Reason validation (5-255 characters)
        clean_reason = reason.strip() if reason else ""
        if len(clean_reason) < 5 or len(clean_reason) > 255:
            raise ValueError(
                "Причина корректировки обязательна и должна содержать от 5 до 255 символов."
            )

        # 2. Delta validation
        dec_delta = Decimal(str(delta)).quantize(Decimal("0.01"))
        if dec_delta == Decimal("0.00"):
            raise ValueError("Сумма корректировки не может быть равной нулю.")

        # 3. Find user and main account
        user_res = await session.execute(
            select(User).where(User.id == target_user_id)
        )
        user = user_res.scalars().first()
        if not user:
            raise ValueError(f"Пользователь {target_user_id} не найден.")

        acc_res = await session.execute(
            select(Account)
            .where(Account.user_id == target_user_id)
            .where(Account.account_type == "MAIN")
        )
        account = acc_res.scalars().first()
        if not account:
            raise ValueError(f"Основной счет пользователя {target_user_id} не найден.")

        # 4. If debit (delta < 0), verify balance sufficiency
        if dec_delta < Decimal("0.00"):
            debit_abs = abs(dec_delta)
            if account.balance < debit_abs:
                raise InsufficientFundsError(
                    f"Невозможно списать средства: баланс {account.balance} ₽ меньше суммы списания {debit_abs} ₽."
                )
            tx_type = "ADMIN_DEBIT"
        else:
            tx_type = "ADMIN_CREDIT"

        # 5. Apply balance update
        account.balance += dec_delta
        await session.flush()

        # 6. Create Transaction record
        tx_id = str(uuid.uuid4())
        tx = Transaction(
            id=tx_id,
            tx_type=tx_type,
            amount=abs(dec_delta),
            fee=Decimal("0.00"),
            status="COMPLETED",
            category="Админ-корректировка",
            sender_account_id=account.id if dec_delta < 0 else None,
            recipient_account_id=account.id if dec_delta > 0 else None,
            description=clean_reason,
            fiscal_code=f"ФП-{random.randint(1000000000, 9999999999)}",
            auth_code=f"AUTH-{random.randint(100000, 999999)}",
        )
        session.add(tx)
        await session.flush()

        # 7. Record AdminLog audit trail
        admin_log = AdminLog(
            admin_telegram_id=admin_telegram_id,
            target_telegram_id=user.telegram_id,
            target_user_id=user.id,
            action=tx_type,
            amount=dec_delta,
            reason=clean_reason,
        )
        session.add(admin_log)
        await session.flush()

        return tx

    @classmethod
    async def get_admin_logs(cls, session: AsyncSession, limit: int = 50) -> list[AdminLog]:
        """Query recent administrator audit logs."""
        res = await session.execute(
            select(AdminLog).order_by(AdminLog.created_at.desc()).limit(limit)
        )
        return list(res.scalars().all())

    @classmethod
    async def get_all_recipient_ids(cls, session: AsyncSession) -> list[int]:
        """Query all registered user Telegram IDs for broadcast dispatch."""
        res = await session.execute(select(User.telegram_id))
        return list(res.scalars().all())

    @classmethod
    async def execute_broadcast(
        cls,
        bot: Bot,
        session: AsyncSession,
        admin_telegram_id: int,
        message_text: str,
        rate_limit_per_sec: int | None = None,
        delay: float | None = None,
    ) -> dict:
        """
        Asynchronously dispatch broadcast message to all registered users with safe rate-limiting (~25 msg/s).

        Catches TelegramForbiddenError, TelegramBadRequest, and TelegramRetryAfter without terminating loop.
        Records persistent AdminLog entry and returns delivery metrics.
        """
        effective_delay = delay if delay is not None else (1.0 / (rate_limit_per_sec or 25))

        recipients = await cls.get_all_recipient_ids(session)
        total = len(recipients)
        sent_count = 0
        blocked_count = 0
        failed_count = 0
        retried_count = 0
        start_time = time.monotonic()

        for user_id in recipients:
            try:
                await bot.send_message(chat_id=user_id, text=message_text, parse_mode="HTML")
                sent_count += 1
            except TelegramForbiddenError as exc:
                blocked_count += 1
                failed_count += 1
                logger.info("Broadcast: User %s blocked bot: %s", user_id, exc)
            except TelegramRetryAfter as exc:
                retried_count += 1
                logger.warning("Broadcast: Rate limit hit, sleeping %s s", exc.retry_after)
                await asyncio.sleep(exc.retry_after)
                try:
                    await bot.send_message(chat_id=user_id, text=message_text, parse_mode="HTML")
                    sent_count += 1
                except TelegramForbiddenError:
                    blocked_count += 1
                    failed_count += 1
                except Exception as retry_exc:
                    failed_count += 1
                    logger.error("Broadcast: Retry failed for user %s: %s", user_id, retry_exc)
            except TelegramBadRequest as exc:
                failed_count += 1
                logger.warning("Broadcast: BadRequest for user %s: %s", user_id, exc)
            except TelegramAPIError as exc:
                failed_count += 1
                logger.error("Broadcast: API error for user %s: %s", user_id, exc)
            except Exception as exc:
                failed_count += 1
                logger.exception("Broadcast: Unexpected error for user %s: %s", user_id, exc)

            if effective_delay > 0:
                await asyncio.sleep(effective_delay)

        duration = max(time.monotonic() - start_time, 0.001)
        effective_speed = (sent_count / duration) if duration > 0 else 0.0

        # Persistent Audit Logging
        admin_log = AdminLog(
            admin_telegram_id=admin_telegram_id,
            target_telegram_id=None,
            target_user_id=None,
            action="BROADCAST",
            amount=None,
            reason=(
                f"Глобальная рассылка: {sent_count}/{total} доставлено, "
                f"{blocked_count} заблокировано, {failed_count} ошибок. "
                f"Текст: {message_text[:120]}"
            ),
        )
        session.add(admin_log)
        await session.flush()

        return {
            "total": total,
            "sent": sent_count,
            "blocked": blocked_count,
            "failed": failed_count,
            "retried": retried_count,
            "duration_seconds": round(duration, 2),
            "effective_speed": round(effective_speed, 1),
        }

    # =========================================================================
    # Admin Superpowers & Cheat Engine (R2 - R6)
    # =========================================================================

    @classmethod
    async def cleanup_test_users(
        cls,
        session: AsyncSession,
        extra_telegram_ids: list[int] | None = None,
        extra_usernames: list[str] | None = None,
        admin_telegram_id: int | None = None,
    ) -> dict[str, int]:
        """
        Safely purge mock/test accounts and dependent records in strict reverse dependency order.

        Prevents SQLite foreign key constraint violations under PRAGMA foreign_keys=ON.
        Strictly excludes real admin (1302123809) and any telegram IDs in settings.ADMIN_IDS.
        """
        admin_ids_cfg = (
            settings.ADMIN_IDS if isinstance(settings.ADMIN_IDS, list) else [settings.ADMIN_IDS]
        )
        protected_ids = set(admin_ids_cfg) | {1302123809}

        mock_ids = {123, 8881001, 8881002, 999888777}
        if extra_telegram_ids:
            mock_ids.update(extra_telegram_ids)
        mock_ids = mock_ids - protected_ids

        conditions = [User.telegram_id.in_(list(mock_ids))]
        conditions.append(func.lower(User.username).like("test%"))
        conditions.append(func.lower(User.username).like("mock%"))
        if extra_usernames:
            for uname in extra_usernames:
                conditions.append(func.lower(User.username) == uname.strip().lower())

        user_stmt = (
            select(User)
            .where(or_(*conditions))
            .where(User.telegram_id.notin_(protected_ids))
        )
        res = await session.execute(user_stmt)
        candidates = list(res.scalars().all())

        # Strict safety guard: never delete protected accounts or vvvet1
        test_users = [
            u
            for u in candidates
            if u.telegram_id not in protected_ids
            and (u.username is None or u.username.lower() != "vvvet1")
        ]

        if not test_users:
            return {
                "users": 0,
                "accounts": 0,
                "cards": 0,
                "transactions": 0,
                "ledger_entries": 0,
                "savings": 0,
                "portfolios": 0,
                "cashback_selections": 0,
                "jobs": 0,
                "properties": 0,
                "sports_bets": 0,
                "pvp_duels": 0,
                "admin_logs": 0,
                "deleted_users": 0,
                "deleted_accounts": 0,
                "deleted_transactions": 0,
            }

        test_user_ids = [u.id for u in test_users]
        test_telegram_ids = [u.telegram_id for u in test_users]

        # 1. Accounts belonging to test users
        acc_stmt = select(Account.id).where(Account.user_id.in_(test_user_ids))
        test_account_ids = list((await session.execute(acc_stmt)).scalars().all())

        # 2. Transactions linked to these accounts
        if test_account_ids:
            tx_stmt = select(Transaction.id).where(
                or_(
                    Transaction.sender_account_id.in_(test_account_ids),
                    Transaction.recipient_account_id.in_(test_account_ids),
                )
            )
            test_tx_ids = list((await session.execute(tx_stmt)).scalars().all())
        else:
            test_tx_ids = []

        # 3. AdminLog (references users.id with no ON DELETE CASCADE)
        del_logs_res = await session.execute(
            delete(AdminLog).where(
                or_(
                    AdminLog.target_user_id.in_(test_user_ids),
                    AdminLog.target_telegram_id.in_(test_telegram_ids),
                )
            )
        )
        deleted_admin_logs = (
            del_logs_res.rowcount
            if del_logs_res.rowcount is not None and del_logs_res.rowcount >= 0
            else 0
        )

        # 4. LedgerEntry (references transactions.id)
        if test_tx_ids or test_account_ids:
            ledger_cond = []
            if test_tx_ids:
                ledger_cond.append(LedgerEntry.transaction_id.in_(test_tx_ids))
            if test_account_ids:
                ledger_cond.append(LedgerEntry.account_id.in_(test_account_ids))
            del_ledgers_res = await session.execute(
                delete(LedgerEntry).where(or_(*ledger_cond))
            )
            deleted_ledgers = (
                del_ledgers_res.rowcount
                if del_ledgers_res.rowcount is not None and del_ledgers_res.rowcount >= 0
                else 0
            )
        else:
            deleted_ledgers = 0

        # 5. Transaction (references accounts.id)
        if test_tx_ids or test_account_ids:
            tx_cond = []
            if test_tx_ids:
                tx_cond.append(Transaction.id.in_(test_tx_ids))
            if test_account_ids:
                tx_cond.append(Transaction.sender_account_id.in_(test_account_ids))
                tx_cond.append(Transaction.recipient_account_id.in_(test_account_ids))
            del_tx_res = await session.execute(
                delete(Transaction).where(or_(*tx_cond))
            )
            deleted_transactions = (
                del_tx_res.rowcount
                if del_tx_res.rowcount is not None and del_tx_res.rowcount >= 0
                else 0
            )
        else:
            deleted_transactions = 0

        # 6. Child records referencing user_id and/or account_id
        card_cond = [Card.user_id.in_(test_user_ids)]
        if test_account_ids:
            card_cond.append(Card.account_id.in_(test_account_ids))
        del_cards_res = await session.execute(delete(Card).where(or_(*card_cond)))
        deleted_cards = (
            del_cards_res.rowcount
            if del_cards_res.rowcount is not None and del_cards_res.rowcount >= 0
            else 0
        )

        savings_cond = [SavingsAccount.user_id.in_(test_user_ids)]
        if test_account_ids:
            savings_cond.append(SavingsAccount.account_id.in_(test_account_ids))
        del_savings_res = await session.execute(
            delete(SavingsAccount).where(or_(*savings_cond))
        )
        deleted_savings = (
            del_savings_res.rowcount
            if del_savings_res.rowcount is not None and del_savings_res.rowcount >= 0
            else 0
        )

        del_ports_res = await session.execute(
            delete(InvestmentPortfolio).where(InvestmentPortfolio.user_id.in_(test_user_ids))
        )
        deleted_portfolios = (
            del_ports_res.rowcount
            if del_ports_res.rowcount is not None and del_ports_res.rowcount >= 0
            else 0
        )

        del_cb_res = await session.execute(
            delete(UserCashbackSelection).where(
                UserCashbackSelection.user_id.in_(test_user_ids)
            )
        )
        deleted_cashbacks = (
            del_cb_res.rowcount
            if del_cb_res.rowcount is not None and del_cb_res.rowcount >= 0
            else 0
        )

        del_jobs_res = await session.execute(
            delete(UserJob).where(UserJob.user_id.in_(test_user_ids))
        )
        deleted_jobs = (
            del_jobs_res.rowcount
            if del_jobs_res.rowcount is not None and del_jobs_res.rowcount >= 0
            else 0
        )

        del_props_res = await session.execute(
            delete(UserProperty).where(UserProperty.user_id.in_(test_user_ids))
        )
        deleted_properties = (
            del_props_res.rowcount
            if del_props_res.rowcount is not None and del_props_res.rowcount >= 0
            else 0
        )

        del_bets_res = await session.execute(
            delete(SportsBet).where(SportsBet.user_id.in_(test_user_ids))
        )
        deleted_sports_bets = (
            del_bets_res.rowcount
            if del_bets_res.rowcount is not None and del_bets_res.rowcount >= 0
            else 0
        )

        del_duels_res = await session.execute(
            delete(PvPDuel).where(
                or_(
                    PvPDuel.creator_user_id.in_(test_user_ids),
                    PvPDuel.opponent_user_id.in_(test_user_ids),
                    PvPDuel.winner_user_id.in_(test_user_ids),
                )
            )
        )
        deleted_pvp_duels = (
            del_duels_res.rowcount
            if del_duels_res.rowcount is not None and del_duels_res.rowcount >= 0
            else 0
        )

        # 7. Accounts
        del_acc_res = await session.execute(
            delete(Account).where(Account.user_id.in_(test_user_ids))
        )
        deleted_accounts = (
            del_acc_res.rowcount
            if del_acc_res.rowcount is not None and del_acc_res.rowcount >= 0
            else 0
        )

        # 8. Users
        del_users_res = await session.execute(
            delete(User).where(User.id.in_(test_user_ids))
        )
        deleted_users = (
            del_users_res.rowcount
            if del_users_res.rowcount is not None and del_users_res.rowcount >= 0
            else len(test_user_ids)
        )

        await session.flush()

        if admin_telegram_id:
            admin_log = AdminLog(
                admin_telegram_id=admin_telegram_id,
                target_telegram_id=None,
                target_user_id=None,
                action="CLEANUP_TEST_USERS",
                amount=None,
                reason=(
                    f"Очистка тестовых данных: удалено {deleted_users} пользователей, "
                    f"{deleted_accounts} счетов, {deleted_transactions} транзакций"
                ),
            )
            session.add(admin_log)
            await session.flush()

        return {
            "users": deleted_users,
            "accounts": deleted_accounts,
            "cards": deleted_cards,
            "transactions": deleted_transactions,
            "ledger_entries": deleted_ledgers,
            "savings": deleted_savings,
            "portfolios": deleted_portfolios,
            "cashback_selections": deleted_cashbacks,
            "jobs": deleted_jobs,
            "properties": deleted_properties,
            "sports_bets": deleted_sports_bets,
            "pvp_duels": deleted_pvp_duels,
            "admin_logs": deleted_admin_logs,
            "deleted_users": deleted_users,
            "deleted_accounts": deleted_accounts,
            "deleted_transactions": deleted_transactions,
        }

    @classmethod
    async def cheat_balance(
        cls,
        session: AsyncSession,
        admin_telegram_id: int,
        target_user_id: int,
        amount: Decimal | float | int | str,
    ) -> Transaction:
        """Atomically credit target user's main account with double-entry ledger and AdminLog audit."""
        dec_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        if dec_amount <= Decimal("0.00"):
            raise ValueError("Сумма накрутки баланса должна быть больше нуля.")

        user_res = await session.execute(
            select(User).where(
                (User.id == target_user_id) | (User.telegram_id == target_user_id)
            )
        )
        user = user_res.scalars().first()
        if not user:
            raise ValueError(f"Пользователь {target_user_id} не найден.")

        acc_res = await session.execute(
            select(Account).where(
                Account.user_id == user.id, Account.account_type == "MAIN"
            )
        )
        account = acc_res.scalars().first()
        if not account:
            raise ValueError(f"Основной счет пользователя {user.id} не найден.")

        # Atomic balance update
        account.balance += dec_amount
        await session.flush()

        tx_id = str(uuid.uuid4())
        desc = f"Чит: +{dec_amount:,.2f} ₽"
        tx = Transaction(
            id=tx_id,
            tx_type="ADMIN_CHEAT",
            amount=dec_amount,
            fee=Decimal("0.00"),
            status="COMPLETED",
            category="Чит-панель",
            sender_account_id=None,
            recipient_account_id=account.id,
            description=desc,
            fiscal_code=f"ФП-{random.randint(1000000000, 9999999999)}",
            auth_code=f"AUTH-{random.randint(100000, 999999)}",
        )
        session.add(tx)
        await session.flush()

        ledger = LedgerEntry(
            transaction_id=tx.id,
            account_id=account.id,
            direction="CREDIT",
            amount=dec_amount,
            balance_after=account.balance,
        )
        session.add(ledger)
        await session.flush()

        admin_log = AdminLog(
            admin_telegram_id=admin_telegram_id,
            target_telegram_id=user.telegram_id,
            target_user_id=user.id,
            action="ADMIN_CHEAT_BALANCE",
            amount=dec_amount,
            reason=f"Чит: пополнение счета +{dec_amount:,.2f} ₽",
        )
        session.add(admin_log)
        await session.flush()

        return tx

    cheat_add_balance = cheat_balance

    @classmethod
    async def cheat_cashback(
        cls,
        session: AsyncSession,
        admin_telegram_id: int,
        target_user_id: int,
        amount: Decimal | float | int | str,
    ) -> Decimal:
        """Atomically credit target user's cashback balance with AdminLog audit."""
        dec_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        if dec_amount <= Decimal("0.00"):
            raise ValueError("Сумма начисления кэшбэка должна быть больше нуля.")

        user_res = await session.execute(
            select(User).where(
                (User.id == target_user_id) | (User.telegram_id == target_user_id)
            )
        )
        user = user_res.scalars().first()
        if not user:
            raise ValueError(f"Пользователь {target_user_id} не найден.")

        user.cashback_balance += dec_amount
        await session.flush()

        admin_log = AdminLog(
            admin_telegram_id=admin_telegram_id,
            target_telegram_id=user.telegram_id,
            target_user_id=user.id,
            action="ADMIN_CHEAT_CASHBACK",
            amount=dec_amount,
            reason=f"Чит: начисление +{dec_amount:,.2f} кэшбэка",
        )
        session.add(admin_log)
        await session.flush()

        return user.cashback_balance

    cheat_add_cashback = cheat_cashback

    @classmethod
    async def reset_cooldowns(
        cls,
        session: AsyncSession,
        admin_telegram_id: int,
        target_user_id: int,
    ) -> dict[str, Any]:
        """
        Fast-forward and clear cooldowns across all features for target user:
        - Wheel: clears last_daily_spin_at
        - Job shifts: rewinds shift_started_at by 3 hours for active jobs on shift
        - Rental income: rewinds last_rent_collected_at by 25 hours for real estate
        - Hustle: clears last_hustle_at
        Audited via AdminLog.
        """
        user_res = await session.execute(
            select(User)
            .where((User.id == target_user_id) | (User.telegram_id == target_user_id))
            .options(selectinload(User.jobs), selectinload(User.properties))
        )
        user = user_res.scalars().first()
        if not user:
            raise ValueError(f"Пользователь {target_user_id} не найден.")

        now = datetime.datetime.now()

        # 1. Wheel
        user.last_daily_spin_at = None

        # 2. Hustle
        user.last_hustle_at = None

        # 3. Job shifts on shift
        jobs_res = await session.execute(
            select(UserJob).where(UserJob.user_id == user.id)
        )
        all_jobs = list(jobs_res.scalars().all())
        jobs_reset = 0
        for job in all_jobs:
            if job.is_on_shift:
                job.shift_started_at = now - datetime.timedelta(hours=3)
                jobs_reset += 1

        # 4. Rental income for real estate
        props_res = await session.execute(
            select(UserProperty).where(UserProperty.user_id == user.id)
        )
        all_props = list(props_res.scalars().all())
        rentals_reset = 0
        for prop in all_props:
            if prop.category == "REAL_ESTATE":
                prop.last_rent_collected_at = now - datetime.timedelta(hours=25)
                rentals_reset += 1

        # 5. Fast-forward scheduled bookmaker matches
        await session.execute(
            update(SportsMatch)
            .where(SportsMatch.status.in_(["SCHEDULED", "LIVE"]))
            .values(scheduled_at=now - datetime.timedelta(seconds=5))
        )

        admin_log = AdminLog(
            admin_telegram_id=admin_telegram_id,
            target_telegram_id=user.telegram_id,
            target_user_id=user.id,
            action="RESET_COOLDOWNS",
            amount=None,
            reason="Сброс таймеров: Колесо, Смены, Аренда, Шабашки",
        )
        session.add(admin_log)
        await session.flush()

        return {
            "user_id": user.id,
            "wheel_reset": True,
            "hustle_reset": True,
            "jobs_reset": jobs_reset,
            "rentals_reset": rentals_reset,
            "properties_reset": rentals_reset,
        }

    reset_user_cooldowns = reset_cooldowns

    @classmethod
    async def grant_property(
        cls,
        session: AsyncSession,
        admin_telegram_id: int,
        target_user_id: int,
        item_id: str,
    ) -> UserProperty:
        """
        Grant property or luxury asset to user for 0.00 ₽ cost basis.
        If user already owns the asset, returns existing property without duplicate errors.
        """
        item = PropertyService.get_property_item(item_id)
        if not item:
            raise ValueError(f"Неизвестный актив: {item_id}")

        user_res = await session.execute(
            select(User).where(
                (User.id == target_user_id) | (User.telegram_id == target_user_id)
            )
        )
        user = user_res.scalars().first()
        if not user:
            raise ValueError(f"Пользователь {target_user_id} не найден.")

        # Check existing ownership
        prop_res = await session.execute(
            select(UserProperty).where(
                UserProperty.user_id == user.id,
                UserProperty.item_id == item.item_id,
            )
        )
        existing_prop = prop_res.scalars().first()
        if existing_prop:
            return existing_prop

        now = datetime.datetime.now()
        new_prop = UserProperty(
            user_id=user.id,
            category=item.category,
            item_id=item.item_id,
            purchase_price=Decimal("0.00"),
            last_rent_collected_at=now if item.category == "REAL_ESTATE" else None,
        )
        session.add(new_prop)
        await session.flush()

        admin_log = AdminLog(
            admin_telegram_id=admin_telegram_id,
            target_telegram_id=user.telegram_id,
            target_user_id=user.id,
            action="GRANT_PROPERTY",
            amount=Decimal("0.00"),
            reason=f"Выдача актива: {item.name}",
        )
        session.add(admin_log)
        await session.flush()

        return new_prop

    @classmethod
    async def grant_stocks(
        cls,
        session: AsyncSession,
        admin_telegram_id: int,
        target_user_id: int,
        ticker: str,
        shares: int = 1000,
    ) -> InvestmentPortfolio:
        """
        Grant parody stock shares to user's investment portfolio at 0 ₽ cost basis.
        Ensures stock exists, increments shares if portfolio exists, or creates new portfolio.
        """
        if shares <= 0:
            raise ValueError("Количество акций должно быть больше нуля.")

        user_res = await session.execute(
            select(User).where(
                (User.id == target_user_id) | (User.telegram_id == target_user_id)
            )
        )
        user = user_res.scalars().first()
        if not user:
            raise ValueError(f"Пользователь {target_user_id} не найден.")

        await InvestmentService.init_stocks(session)

        clean_ticker = ticker.strip().upper()
        stock_res = await session.execute(
            select(Stock).where(func.upper(Stock.ticker) == clean_ticker)
        )
        stock = stock_res.scalars().first()
        if not stock:
            raise ValueError(f"Неизвестная акция: {ticker}")

        port_res = await session.execute(
            select(InvestmentPortfolio).where(
                InvestmentPortfolio.user_id == user.id,
                InvestmentPortfolio.stock_id == stock.id,
            )
        )
        portfolio = port_res.scalars().first()
        if portfolio:
            portfolio.shares += shares
        else:
            portfolio = InvestmentPortfolio(
                user_id=user.id,
                stock_id=stock.id,
                shares=shares,
                average_buy_price=stock.current_price,
            )
            session.add(portfolio)

        await session.flush()

        admin_log = AdminLog(
            admin_telegram_id=admin_telegram_id,
            target_telegram_id=user.telegram_id,
            target_user_id=user.id,
            action="GRANT_STOCK",
            amount=Decimal(str(shares)),
            reason=f"Выдача {shares} акций {stock.ticker}",
        )
        session.add(admin_log)
        await session.flush()

        return portfolio

    grant_stock = grant_stocks

    @classmethod
    async def manipulate_market(
        cls,
        session: AsyncSession,
        admin_telegram_id: int,
        action: str,
    ) -> list[Stock]:
        """
        Global market manipulation:
        - PUMP: * 1.50 (+50%)
        - DUMP: * 0.50 (-50%)
        - CRASH: * 0.20 (-80%)
        Strictly floored at Decimal('1.00'). Audited via AdminLog.
        """
        await InvestmentService.init_stocks(session)

        act = action.strip().upper()
        if act == "PUMP":
            multiplier = Decimal("1.50")
        elif act == "DUMP":
            multiplier = Decimal("0.50")
        elif act == "CRASH":
            multiplier = Decimal("0.20")
        else:
            raise ValueError(f"Неизвестное действие манипуляции рынком: {action}")

        res = await session.execute(select(Stock))
        stocks = list(res.scalars().all())

        for stock in stocks:
            new_price = max(
                Decimal("1.00"),
                (stock.current_price * multiplier).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
            )
            stock.current_price = new_price

        await session.flush()

        admin_log = AdminLog(
            admin_telegram_id=admin_telegram_id,
            target_telegram_id=None,
            target_user_id=None,
            action=f"MARKET_{act}",
            amount=multiplier,
            reason=f"Манипуляция рынком: {act}",
        )
        session.add(admin_log)
        await session.flush()

        return stocks

    @classmethod
    async def pump_market(
        cls, session: AsyncSession, admin_telegram_id: int
    ) -> list[Stock]:
        """Convenience helper: Pump all market stocks (+50%)."""
        return await cls.manipulate_market(session, admin_telegram_id, "PUMP")

    @classmethod
    async def dump_market(
        cls, session: AsyncSession, admin_telegram_id: int
    ) -> list[Stock]:
        """Convenience helper: Dump all market stocks (-50%)."""
        return await cls.manipulate_market(session, admin_telegram_id, "DUMP")

    @classmethod
    async def crash_market(
        cls, session: AsyncSession, admin_telegram_id: int
    ) -> list[Stock]:
        """Convenience helper: Flash-crash all market stocks (-80%)."""
        return await cls.manipulate_market(session, admin_telegram_id, "CRASH")

    @classmethod
    async def set_stock_price(
        cls,
        session: AsyncSession,
        admin_telegram_id: int,
        ticker: str,
        target_price: Decimal | float | int | str,
    ) -> Stock:
        """
        Set exact stock price, floored at Decimal('1.00').
        Audited via AdminLog.
        """
        await InvestmentService.init_stocks(session)

        clean_ticker = ticker.strip().upper()
        res = await session.execute(
            select(Stock).where(func.upper(Stock.ticker) == clean_ticker)
        )
        stock = res.scalars().first()
        if not stock:
            raise ValueError(f"Неизвестная акция: {ticker}")

        price = max(
            Decimal("1.00"),
            Decimal(str(target_price)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
        )
        stock.current_price = price
        await session.flush()

        admin_log = AdminLog(
            admin_telegram_id=admin_telegram_id,
            target_telegram_id=None,
            target_user_id=None,
            action="SET_STOCK_PRICE",
            amount=price,
            reason=f"Установка цены {stock.ticker} = {price} ₽",
        )
        session.add(admin_log)
        await session.flush()

        return stock

    @classmethod
    async def manipulate_stock(
        cls,
        session: AsyncSession,
        admin_telegram_id: int,
        ticker: str,
        action_or_price: str | Decimal | float | int,
    ) -> Stock:
        """
        Manipulate single stock: +25% (PUMP), -25% (DUMP), or set exact target price.
        Audited via AdminLog.
        """
        await InvestmentService.init_stocks(session)
        clean_ticker = ticker.strip().upper()
        res = await session.execute(
            select(Stock).where(func.upper(Stock.ticker) == clean_ticker)
        )
        stock = res.scalars().first()
        if not stock:
            raise ValueError(f"Неизвестная акция: {ticker}")

        if isinstance(action_or_price, str) and action_or_price.upper() in (
            "PUMP",
            "DUMP",
        ):
            mult = (
                Decimal("1.25")
                if action_or_price.upper() == "PUMP"
                else Decimal("0.75")
            )
            new_price = max(
                Decimal("1.00"),
                (stock.current_price * mult).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
            )
            stock.current_price = new_price
            action_name = f"STOCK_{action_or_price.upper()}"
            reason = (
                f"Манипуляция акцией {stock.ticker}: {action_or_price.upper()} -> {new_price} ₽"
            )
        else:
            new_price = max(
                Decimal("1.00"),
                Decimal(str(action_or_price)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
            )
            stock.current_price = new_price
            action_name = "SET_STOCK_PRICE"
            reason = f"Установка цены {stock.ticker} = {new_price} ₽"

        await session.flush()

        admin_log = AdminLog(
            admin_telegram_id=admin_telegram_id,
            target_telegram_id=None,
            target_user_id=None,
            action=action_name,
            amount=new_price,
            reason=reason,
        )
        session.add(admin_log)
        await session.flush()

        return stock


