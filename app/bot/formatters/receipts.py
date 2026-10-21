"""Receipt Formatter for T-Bank Digital Receipts."""

from decimal import Decimal
from app.database.models import Card, Transaction, User
from app.services.receipts import ReceiptService


def format_receipt_message(
    transaction: Transaction,
    sender_user: User | None = None,
    recipient_user: User | None = None,
    sender_card: Card | None = None,
    cashback_earned: Decimal | None = None,
) -> str:
    """Format digital receipt for Telegram presentation using ReceiptService."""
    return ReceiptService.format_receipt(
        transaction=transaction,
        sender_user=sender_user,
        recipient_user=recipient_user,
        sender_card=sender_card,
        cashback_earned=cashback_earned,
    )
