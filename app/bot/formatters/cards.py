"""Card Visual Formatter for T-Bank Black Debit Cards."""

import html
from decimal import Decimal
from app.services.card import CardService


def format_card_view(
    card_data: dict, unmasked: bool = False, balance: Decimal | float | None = None
) -> str:
    """Format visual T-Bank Black card in Telegram HTML."""
    card_number = card_data.get("card_number", "2200700000000000")
    if unmasked:
        pan_display = CardService.format_card_number(card_number, masked=False)
        cvv_display = card_data.get("cvv", "•••")
    else:
        pan_display = CardService.format_card_number(card_number, masked=True)
        cvv_display = "•••"

    expiry = card_data.get("expiry_date", "12/30")
    raw_holder = card_data.get("cardholder_name") or card_data.get("full_name") or "TBANK CLIENT"
    holder = str(raw_holder).replace("\n", " ").replace("\r", "").strip().upper()
    if len(holder) > 36:
        holder = holder[:33] + "..."

    is_frozen = card_data.get("is_frozen", False)
    status_emoji = "❄️ Заморожена" if is_frozen else "🟢 Активна"

    bal_val = balance if balance is not None else card_data.get("balance", Decimal("0.00"))
    if isinstance(bal_val, (int, float, Decimal)):
        bal_str = f"{Decimal(str(bal_val)):.2f} ₽"
    else:
        bal_str = str(bal_val)

    # Monospace styled card block with raw padding
    card_box = (
        "┌──────────────────────────────────────┐\n"
        "│ 🟡 Т-Банк                     [МИР]  │\n"
        "│                                      │\n"
        f"│  {pan_display:<36}│\n"
        f"│  Срок: {expiry:<15} CVC: {cvv_display:<10}│\n"
        f"│  {holder:<36}│\n"
        "└──────────────────────────────────────┘"
    )

    # Escape HTML entities for Telegram HTML parse mode
    escaped_card_box = html.escape(card_box, quote=False)
    escaped_bal_str = html.escape(bal_str, quote=False)

    lines = [
        "💳 <b>Т-Банк Black (Дебетовая карта)</b>",
        f"<pre>{escaped_card_box}</pre>",
        f"💰 <b>Баланс:</b> <code>{escaped_bal_str}</code>",
        f"<b>Статус:</b> {status_emoji}",
        "<b>Лимит СБП:</b> 100 000.00 ₽ / месяц",
    ]
    return "\n".join(lines)
