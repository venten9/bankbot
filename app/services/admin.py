"""Administrative Management Service: Metrics Aggregation, User Ops, and Audited Adjustments."""

import asyncio
import logging
import random
import re
import time
import uuid
from decimal import Decimal
from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)

from app.database.models import (
    Account,
    AdminLog,
    Card,
    LedgerEntry,
    SavingsAccount,
    Transaction,
    User,
)
from app.services.card import CardService
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

