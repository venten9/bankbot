"""Virtual Assistant 'Олег' Router: Witty Intent Evaluation and Sarcastic Fallback."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.reply import get_main_menu_keyboard
from app.database.models import User
from app.services.banking import BankingService
from app.services.oleg import OlegAssistant

oleg_router = Router(name="oleg_router")


@oleg_router.message(Command("oleg"))
@oleg_router.message(F.text == "💬 Олег")
async def handle_oleg_prompt(
    message: Message, session: AsyncSession, user: User
) -> None:
    """Prompt user to converse with Oleg the assistant."""
    overview = await BankingService.get_user_overview(session, user.id)
    text = (
        "🤖 <b>Олег на связи!</b>\n\n"
        "Слушаю вас. Можете спросить про баланс, кэшбэк, попросить совет по инвестициям "
        "или написать что угодно. Но помните: я саркастичен, экономен и неподкупен."
    )
    await message.answer(text=text, reply_markup=get_main_menu_keyboard())


@oleg_router.message(F.text)
async def handle_oleg_dialogue(
    message: Message, session: AsyncSession, user: User
) -> None:
    """Evaluate incoming text message with Oleg's intent classifier and reply with wit."""
    # Exclude command-like messages that failed to match other routes
    if message.text and message.text.startswith("/"):
        await message.answer(
            "Неизвестная команда. Воспользуйтесь меню или /help.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    overview = await BankingService.get_user_overview(session, user.id)
    context = {
        "main_balance": overview["main_balance"],
        "cashback_balance": overview["cashback_balance"],
        "savings_balance": overview["savings_balance"],
    }
    answer = OlegAssistant.get_response(message.text or "", context=context)
    await message.answer(
        text=f"💬 <b>Олег:</b>\n{answer}",
        reply_markup=get_main_menu_keyboard(),
    )
