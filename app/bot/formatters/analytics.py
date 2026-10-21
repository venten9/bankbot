"""Expense Analytics Visual Formatter."""

from sqlalchemy.ext.asyncio import AsyncSession
from app.services.receipts import ReceiptService


async def format_expense_analytics(
    session: AsyncSession, user_id: int, month_year: str | None = None
) -> str:
    """Generate visual monthly expense analytics with 10-block Unicode progress bars."""
    data = await ReceiptService.get_expense_analytics(
        session=session, user_id=user_id, month_year=month_year
    )
    return data["formatted_report"]
