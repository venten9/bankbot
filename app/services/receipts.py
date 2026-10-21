"""Digital Receipt Formatter and Visual Expense Analytics Progress Bar Engine."""

import datetime
import html
import random
from decimal import Decimal
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Account, Card, Transaction, User
from app.services.card import CardService


class ReceiptService:
    """Service providing fiscal transaction receipt formatting and visual spending analytics."""

    @staticmethod
    def generate_fiscal_code() -> str:
        """Generate a Russian fiscal storage attribute (ФП-XXXXXXXXXX)."""
        return f"ФП-{random.randint(1000000000, 9999999999)}"

    @staticmethod
    def generate_auth_code() -> str:
        """Generate a 6-digit transaction authorization code (AUTH-XXXXXX)."""
        return f"AUTH-{random.randint(100000, 999999)}"

    @staticmethod
    def format_progress_bar(percentage: float, total_blocks: int = 10) -> str:
        """Render a 10-block Unicode visual progress bar (e.g., ████░░░░░░)."""
        clamped_pct = max(0.0, min(100.0, percentage))
        k = int(round((clamped_pct / 100.0) * total_blocks))
        k = max(0, min(total_blocks, k))
        return "█" * k + "░" * (total_blocks - k)

    @classmethod
    def format_receipt(
        cls,
        transaction: Transaction,
        sender_user: User | None = None,
        recipient_user: User | None = None,
        sender_card: Card | None = None,
        cashback_earned: Decimal | None = None,
    ) -> str:
        """Format an official digital neobank receipt."""
        tx_short_id = transaction.id[:8].upper()
        date_str = (
            transaction.created_at.strftime("%d.%m.%Y %H:%M:%S MSK")
            if transaction.created_at
            else datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S MSK")
        )

        # Sender description
        if sender_card:
            sender_str = f"{CardService.format_card_number(sender_card.card_number, masked=True)}"
            if sender_user:
                s_name = html.escape(sender_user.full_name or "Клиент", quote=False)
                sender_str += f" ({s_name})"
        elif sender_user:
            s_name = html.escape(sender_user.full_name or "Клиент", quote=False)
            sender_str = f"{s_name}"
            if sender_user.username:
                s_uname = html.escape(sender_user.username, quote=False)
                sender_str += f" (@{s_uname})"
        else:
            sender_str = "Т-Банк Онлайн"

        # Recipient description
        if recipient_user:
            r_name = html.escape(recipient_user.full_name or "Клиент", quote=False)
            recipient_str = f"{r_name}"
            if recipient_user.username:
                r_uname = html.escape(recipient_user.username, quote=False)
                recipient_str += f" (@{r_uname})"
        else:
            recipient_str = "Т-Банк Клиент"

        if transaction.description:
            desc_escaped = html.escape(transaction.description, quote=False)
            desc_str = f'"{desc_escaped}"'
        else:
            desc_str = "Частный перевод"

        category_str = html.escape(transaction.category or "Переводы", quote=False)
        fee_amount = transaction.fee if transaction.fee is not None else Decimal("0.00")
        auth_code_str = html.escape(transaction.auth_code or "", quote=False)
        fiscal_code_str = html.escape(transaction.fiscal_code or "", quote=False)

        receipt_lines = [
            f"🧾 <b>КВИТАНЦИЯ ОБ ОПЕРАЦИИ #{tx_short_id}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🟡 <b>Т-Банк Онлайн (АО «Т-Банк»)</b>",
            "",
            f"Сумма операции:     <b>{transaction.amount:.2f} ₽</b>",
            f"Категория:          {category_str}",
            "Статус:             ✅ Исполнено",
            "",
            f"Отправитель:        {sender_str}",
            f"Получатель:         {recipient_str}",
            f"Назначение:         {desc_str}",
            "",
            f"Дата и время:       {date_str}",
            f"Код авторизации:    <code>{auth_code_str}</code>",
            f"Фискальный признак: <code>{fiscal_code_str}</code>",
            f"Комиссия:           {fee_amount:.2f} ₽",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        if cashback_earned and cashback_earned > Decimal("0.00"):
            receipt_lines.append(f"💛 Кэшбэк по операции: <b>+{cashback_earned:.2f} ₽</b>")

        return "\n".join(receipt_lines)

    @classmethod
    async def get_expense_analytics(
        cls, session: AsyncSession, user_id: int, month_year: str | None = None
    ) -> dict:
        """Aggregate monthly expenses by category and generate visual progress bars."""
        cur_month = month_year or datetime.datetime.now().strftime("%Y-%m")
        start_date = datetime.datetime.strptime(f"{cur_month}-01", "%Y-%m-%d")
        # Approximate month end
        if start_date.month == 12:
            end_date = datetime.datetime(start_date.year + 1, 1, 1)
        else:
            end_date = datetime.datetime(start_date.year, start_date.month + 1, 1)

        # Get user's accounts
        acc_res = await session.execute(
            select(Account.id).where(Account.user_id == user_id)
        )
        account_ids = [row[0] for row in acc_res.all()]

        if not account_ids:
            return {
                "month": cur_month,
                "total_spent": Decimal("0.00"),
                "categories": [],
                "formatted_report": f"📊 Аналитика трат за {cur_month}\nВ этом месяце расходов еще не было. Ваши деньги целы!",
            }

        # Query transactions where user's account was the sender (outgoing debit)
        tx_query = (
            select(
                Transaction.category,
                func.sum(Transaction.amount).label("category_total"),
            )
            .where(Transaction.sender_account_id.in_(account_ids))
            .where(Transaction.created_at >= start_date)
            .where(Transaction.created_at < end_date)
            .where(Transaction.tx_type.notin_(["SAVINGS_DEPOSIT", "STOCK_SELL"]))
            .group_by(Transaction.category)
            .order_by(func.sum(Transaction.amount).desc())
        )

        res = await session.execute(tx_query)
        cat_rows = res.all()

        total_spent = sum((row[1] for row in cat_rows), Decimal("0.00"))

        if total_spent == Decimal("0.00") or not cat_rows:
            return {
                "month": cur_month,
                "total_spent": Decimal("0.00"),
                "categories": [],
                "formatted_report": f"📊 Аналитика трат за {cur_month}\nВ этом месяце расходов еще не было. Ваши деньги целы!",
            }

        categories_data = []
        report_lines = [
            f"📊 <b>Аналитика трат за {cur_month}</b>",
            f"Общая сумма расходов: <b>{total_spent:.2f} ₽</b>",
            "",
        ]

        top_category = cat_rows[0][0] if cat_rows else "Разное"

        for idx, (cat_name, cat_amount) in enumerate(cat_rows, start=1):
            pct = float((cat_amount / total_spent) * 100)
            bar = cls.format_progress_bar(pct, total_blocks=10)
            categories_data.append({
                "category": cat_name,
                "amount": cat_amount,
                "percentage": pct,
                "progress_bar": bar,
            })
            cat_escaped = html.escape(cat_name or "Разное", quote=False)
            report_lines.append(f"{idx}. {cat_escaped}: {cat_amount:.2f} ₽ ({pct:.1f}%)")
            report_lines.append(f"   <code>{bar}</code>")

        return {
            "month": cur_month,
            "total_spent": total_spent,
            "categories": categories_data,
            "formatted_report": "\n".join(report_lines),
        }
