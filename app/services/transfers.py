"""Transfer Service: Recipient Resolution, Atomic Double-Entry Transfers, and Concurrency Guards."""

import re
import uuid
import random
from decimal import Decimal
from typing import Any
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Account, Card, LedgerEntry, Transaction, User
from app.services.card import CardService


class TransferError(Exception):
    """Base exception for transfer failures."""
    pass


class InsufficientFundsError(TransferError):
    """Raised when account balance is lower than transfer amount."""
    pass


class AccountFrozenError(TransferError):
    """Raised when attempting outgoing transfer from a frozen account or card."""
    pass


class SelfTransferError(TransferError):
    """Raised when sender attempts to transfer funds to their own same account."""
    pass


class RecipientNotFoundError(TransferError):
    """Raised when the specified recipient cannot be identified."""
    pass


class TransferService:
    """Service providing recipient resolution and atomic double-entry balance transfers."""

    @staticmethod
    def normalize_phone(phone: str) -> str | None:
        """Normalize Russian phone numbers to +7XXXXXXXXXX format."""
        digits = re.sub(r"\D", "", phone)
        if len(digits) == 11:
            if digits.startswith("8") or digits.startswith("7"):
                return f"+7{digits[1:]}"
        elif len(digits) == 10:
            return f"+7{digits}"
        return None

    @classmethod
    async def resolve_recipient(
        cls, session: AsyncSession, query: str | int
    ) -> Account | None:
        """Resolve recipient Account by @username, phone (+7...), 16-digit card, or Telegram ID."""
        if isinstance(query, int):
            # Resolve by telegram_id or internal user ID
            res = await session.execute(
                select(Account)
                .join(User, Account.user_id == User.id)
                .where((User.telegram_id == query) | (User.id == query))
                .where(Account.account_type == "MAIN")
            )
            return res.scalars().first()

        clean_query = str(query).strip()

        # 1. Check if numeric string (Telegram ID or User ID)
        if clean_query.isdigit() and len(clean_query) < 15:
            num_id = int(clean_query)
            res = await session.execute(
                select(Account)
                .join(User, Account.user_id == User.id)
                .where((User.telegram_id == num_id) | (User.id == num_id))
                .where(Account.account_type == "MAIN")
            )
            acc = res.scalars().first()
            if acc:
                return acc

        # 2. Check 16-digit Card Number
        stripped_card = clean_query.replace(" ", "").replace("-", "")
        if len(stripped_card) == 16 and stripped_card.isdigit():
            if CardService.validate_luhn(stripped_card):
                card_res = await session.execute(
                    select(Card).where(Card.card_number == stripped_card)
                )
                card = card_res.scalars().first()
                if card:
                    acc_res = await session.execute(
                        select(Account).where(Account.id == card.account_id)
                    )
                    return acc_res.scalars().first()

        # 3. Check Telegram @username
        if clean_query.startswith("@"):
            clean_username = clean_query[1:]
            res = await session.execute(
                select(Account)
                .join(User, Account.user_id == User.id)
                .where(User.username.ilike(clean_username))
                .where(Account.account_type == "MAIN")
            )
            acc = res.scalars().first()
            if acc:
                return acc

        # 4. Check Phone Number
        norm_phone = cls.normalize_phone(clean_query)
        if norm_phone:
            res = await session.execute(
                select(Account)
                .join(User, Account.user_id == User.id)
                .where(User.phone_number == norm_phone)
                .where(Account.account_type == "MAIN")
            )
            acc = res.scalars().first()
            if acc:
                return acc

        # 5. Plain username without leading @
        res = await session.execute(
            select(Account)
            .join(User, Account.user_id == User.id)
            .where(User.username.ilike(clean_query))
            .where(Account.account_type == "MAIN")
        )
        return res.scalars().first()

    @classmethod
    async def execute_transfer(
        cls,
        session: AsyncSession,
        sender_user_id: int | str,
        recipient_account_id: str,
        amount: Decimal | float | int,
        category: str = "Переводы",
        description: str | None = None,
        idempotency_key: str | None = None,
    ) -> Transaction:
        """Execute an atomic double-entry balance transfer with rowcount checks and overdraft prevention."""
        # 1. Parse and validate amount
        dec_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        if dec_amount <= Decimal("0.00"):
            raise ValueError(f"Сумма перевода должна быть строго положительной: {dec_amount}")

        # 2. Check idempotency if key provided
        if idempotency_key:
            existing_tx = await session.execute(
                select(Transaction).where(Transaction.idempotency_key == idempotency_key)
            )
            cached_tx = existing_tx.scalars().first()
            if cached_tx:
                return cached_tx

        # 3. Resolve sender account
        if isinstance(sender_user_id, int):
            sender_res = await session.execute(
                select(Account)
                .where(Account.user_id == sender_user_id)
                .where(Account.account_type == "MAIN")
            )
            sender_account = sender_res.scalars().first()
        else:
            sender_res = await session.execute(
                select(Account).where(Account.id == str(sender_user_id))
            )
            sender_account = sender_res.scalars().first()

        if not sender_account:
            raise TransferError("Счет отправителя не найден.")

        # 4. Check sender frozen status
        if sender_account.is_frozen:
            raise AccountFrozenError("Карта / счет отправителя заморожен.")

        # 5. Check self-transfer
        if sender_account.id == recipient_account_id:
            raise SelfTransferError("Нельзя перевести деньги самому себе на тот же счет.")

        # 6. Resolve recipient account
        rec_res = await session.execute(
            select(Account).where(Account.id == recipient_account_id)
        )
        recipient_account = rec_res.scalars().first()
        if not recipient_account:
            raise RecipientNotFoundError("Счет получателя не найден.")

        # 7. Atomic conditional debit update with rowcount verification
        debit_stmt = (
            update(Account)
            .where(Account.id == sender_account.id)
            .where(Account.balance >= dec_amount)
            .where(Account.is_frozen == False)  # noqa: E712
            .values(balance=Account.balance - dec_amount)
        )
        debit_res = await session.execute(debit_stmt)

        if debit_res.rowcount == 0:
            # Check why debit failed
            recheck_res = await session.execute(
                select(Account).where(Account.id == sender_account.id)
            )
            cur_acc = recheck_res.scalars().first()
            if not cur_acc or cur_acc.is_frozen:
                raise AccountFrozenError("Карта / счет отправителя заморожен.")
            raise InsufficientFundsError(
                f"Недостаточно средств. Доступно: {cur_acc.balance} ₽, необходимо: {dec_amount} ₽."
            )

        # 8. Atomic credit update on recipient
        credit_stmt = (
            update(Account)
            .where(Account.id == recipient_account.id)
            .values(balance=Account.balance + dec_amount)
        )
        await session.execute(credit_stmt)

        # 9. Query post-transfer balances for ledger records
        s_balance = (
            await session.execute(
                select(Account.balance).where(Account.id == sender_account.id)
            )
        ).scalar_one()
        r_balance = (
            await session.execute(
                select(Account.balance).where(Account.id == recipient_account.id)
            )
        ).scalar_one()

        # 10. Create Transaction entity
        tx_id = str(uuid.uuid4())
        fiscal_code = f"ФП-{random.randint(1000000000, 9999999999)}"
        auth_code = f"AUTH-{random.randint(100000, 999999)}"

        transaction = Transaction(
            id=tx_id,
            idempotency_key=idempotency_key,
            tx_type="P2P_TRANSFER",
            amount=dec_amount,
            fee=Decimal("0.00"),
            status="COMPLETED",
            category=category,
            sender_account_id=sender_account.id,
            recipient_account_id=recipient_account.id,
            description=description,
            fiscal_code=fiscal_code,
            auth_code=auth_code,
        )

        # 11. Create balanced double-entry LedgerEntry records
        debit_entry = LedgerEntry(
            transaction_id=tx_id,
            account_id=sender_account.id,
            direction="DEBIT",
            amount=dec_amount,
            balance_after=s_balance,
        )
        credit_entry = LedgerEntry(
            transaction_id=tx_id,
            account_id=recipient_account.id,
            direction="CREDIT",
            amount=dec_amount,
            balance_after=r_balance,
        )
        transaction.ledger_entries = [debit_entry, credit_entry]
        session.add(transaction)
        await session.flush()

        return transaction
