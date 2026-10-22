"""Virtual Debit Card Service: Luhn Generation, Validation, and State Machine."""

import datetime
import random
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Account, Card

MIR_BIN = "220070"


class CardService:
    """Service handling card issuance, Luhn validation, formatting, and freeze actions."""

    @staticmethod
    def calculate_luhn_check_digit(partial_number: str) -> int:
        """Calculate the 16th Luhn check digit for a 15-digit number."""
        digits = [int(d) for d in partial_number]
        total = 0
        for i, digit in enumerate(digits):
            # For 16-digit card, odd 1-indexed (even 0-indexed) positions are doubled
            if i % 2 == 0:
                doubled = digit * 2
                total += doubled - 9 if doubled > 9 else doubled
            else:
                total += digit
        return (10 - (total % 10)) % 10

    @classmethod
    def generate_card_number(cls, prefix: str = MIR_BIN) -> str:
        """Generate a 16-digit Luhn-compliant card number starting with MIR BIN 2200 70."""
        # Remaining digits up to 15 digits
        remaining_needed = 15 - len(prefix)
        middle_digits = "".join(str(random.randint(0, 9)) for _ in range(remaining_needed))
        partial = prefix + middle_digits
        check_digit = cls.calculate_luhn_check_digit(partial)
        return partial + str(check_digit)

    @staticmethod
    def validate_luhn(card_number: str) -> bool:
        """Validate whether a card number string satisfies the Luhn MOD-10 checksum."""
        clean = card_number.replace(" ", "").replace("-", "")
        if not clean.isdigit() or len(clean) != 16:
            return False

        digits = [int(d) for d in clean]
        total = 0
        for i, digit in enumerate(digits):
            if i % 2 == 0:
                doubled = digit * 2
                total += doubled - 9 if doubled > 9 else doubled
            else:
                total += digit
        return total % 10 == 0

    @staticmethod
    def generate_cvv() -> str:
        """Generate a secure 3-digit CVV string."""
        return f"{random.randint(0, 999):03d}"

    @staticmethod
    def generate_expiry(years_ahead: int = 5) -> str:
        """Generate an expiry date string formatted as MM/YY."""
        now = datetime.datetime.now()
        month = now.month
        year = (now.year + years_ahead) % 100
        return f"{month:02d}/{year:02d}"

    @staticmethod
    def format_card_number(card_number: str, masked: bool = False) -> str:
        """Format 16-digit card number with 4-digit blocks, optionally masking middle digits."""
        clean = card_number.replace(" ", "").replace("-", "")
        if len(clean) != 16:
            return card_number
        if masked:
            # Format: 2200 70•• •••• 5842
            return f"{clean[:4]} {clean[4:6]}•• •••• {clean[-4:]}"
        return f"{clean[:4]} {clean[4:8]} {clean[8:12]} {clean[12:]}"

    @staticmethod
    async def freeze_card(
        session: AsyncSession, card_id: int | None = None, user_id: int | None = None
    ) -> bool:
        """Freeze a card and its associated account, preventing outgoing transfers."""
        query = select(Card)
        if card_id is not None:
            query = query.where(Card.id == card_id)
        elif user_id is not None:
            query = query.where(Card.user_id == user_id)
        else:
            raise ValueError("Either card_id or user_id must be provided")

        result = await session.execute(query)
        card = result.scalars().first()
        if not card:
            return False

        card.is_frozen = True
        card.status = "FROZEN"

        # Also freeze the underlying account
        await session.execute(
            update(Account).where(Account.id == card.account_id).values(is_frozen=True)
        )
        await session.flush()
        return True

    @staticmethod
    async def unfreeze_card(
        session: AsyncSession, card_id: int | None = None, user_id: int | None = None
    ) -> bool:
        """Unfreeze a card and restore its active state."""
        query = select(Card)
        if card_id is not None:
            query = query.where(Card.id == card_id)
        elif user_id is not None:
            query = query.where(Card.user_id == user_id)
        else:
            raise ValueError("Either card_id or user_id must be provided")

        result = await session.execute(query)
        card = result.scalars().first()
        if not card:
            return False

        card.is_frozen = False
        card.status = "ACTIVE"

        # Also unfreeze the underlying account
        await session.execute(
            update(Account).where(Account.id == card.account_id).values(is_frozen=False)
        )
        await session.flush()
        return True

    @classmethod
    async def change_card_number(
        cls, session: AsyncSession, user_id: int, new_number: str
    ) -> Card:
        """Change a user's card number to a custom 16-digit number."""
        clean = str(new_number or "").replace(" ", "").replace("-", "").strip()
        if len(clean) != 16 or not clean.isdigit():
            raise ValueError("Номер карты должен состоять ровно из 16 цифр.")

        res = await session.execute(select(Card).where(Card.user_id == user_id))
        card = res.scalars().first()
        if not card:
            raise ValueError(f"Карта пользователя {user_id} не найдена.")

        if card.card_number == clean:
            return card

        dup_res = await session.execute(
            select(Card).where(Card.card_number == clean, Card.user_id != user_id)
        )
        if dup_res.scalars().first() is not None:
            raise ValueError(
                "Этот номер карты уже занят другим клиентом. Пожалуйста, выберите другой."
            )

        card.card_number = clean
        await session.flush()
        return card

