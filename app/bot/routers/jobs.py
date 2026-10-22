"""Jobs, Careers and Quick Hustles Router for Telegram UI (Milestone 6)."""

import datetime
import html
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.formatters.jobs import (
    format_branch_catalog,
    format_career_dashboard,
    format_hustle_quest,
    format_hustle_result,
    format_market_catalog,
    format_shift_claimed,
)
from app.bot.keyboards.jobs import (
    get_branch_detail_keyboard,
    get_hustle_keyboard,
    get_job_market_keyboard,
    get_jobs_menu_keyboard,
)
from app.database.models import User
from app.services.banking import BankingService
from app.services.jobs import JobBranch, JobService

jobs_router = Router(name="jobs")


@jobs_router.message(Command("jobs"))
@jobs_router.message(Command("work"))
@jobs_router.message(Command("zarabotok"))
@jobs_router.message(F.text == "💼 Заработок")
@jobs_router.message(F.text == "💼 Работа и бизнес")
async def handle_jobs_menu(
    message: Message,
    session: AsyncSession,
    user: User,
    state: FSMContext | None = None,
) -> None:
    """Display user's career dashboard with active jobs, shift timers, and actions."""
    if state is not None:
        await state.clear()

    active_jobs = await JobService.get_user_active_jobs(session, user.id)
    text = format_career_dashboard(user, active_jobs)
    keyboard = get_jobs_menu_keyboard(active_jobs)
    await message.answer(text=text, reply_markup=keyboard)


@jobs_router.callback_query(F.data == "jobs:menu")
async def handle_jobs_menu_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Refresh or return to career dashboard in-place."""
    active_jobs = await JobService.get_user_active_jobs(session, user.id)
    text = format_career_dashboard(user, active_jobs)
    keyboard = get_jobs_menu_keyboard(active_jobs)

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=keyboard)
        except Exception:
            pass
    await query.answer("Статус карьеры обновлен!")


@jobs_router.callback_query(F.data == "jobs:market")
async def handle_jobs_market(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Show job market branch selection catalog."""
    active_jobs = await JobService.get_user_active_jobs(session, user.id)
    active_branches = {j.branch for j in active_jobs}

    text = format_branch_catalog(len(active_jobs))
    keyboard = get_job_market_keyboard(active_branches)

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=keyboard)
        except Exception:
            pass
    await query.answer()


@jobs_router.callback_query(F.data.startswith("jobs:market:"))
async def handle_jobs_market_branch(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Show branch career ladder and Apply / Quit buttons."""
    if not query.data:
        await query.answer()
        return

    branch = query.data.split(":")[-1]
    if branch not in JobBranch.ALL:
        await query.answer("Неизвестная отрасль", show_alert=True)
        return

    user_job = await JobService.get_user_job_by_branch(session, user.id, branch)
    active_jobs = await JobService.get_user_active_jobs(session, user.id)
    branch_jobs = JobService.get_job_catalog().get(branch, {})

    text = format_market_catalog(branch, branch_jobs, user_job)
    is_employed = user_job.is_active if user_job else False
    keyboard = get_branch_detail_keyboard(
        branch, is_employed=is_employed, active_count=len(active_jobs)
    )

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=keyboard)
        except Exception:
            pass
    await query.answer()


@jobs_router.callback_query(F.data.startswith("jobs:apply:"))
async def handle_jobs_apply(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Apply for a profession in the specified branch."""
    if not query.data:
        await query.answer()
        return

    branch = query.data.split(":")[-1]
    try:
        new_job = await JobService.apply_for_job(session, user.id, branch)
        await session.commit()

        job_info = JobService.get_job_info(new_job.branch, new_job.level)
        await query.answer(
            f"🎉 Вы успешно трудоустроились: {job_info['title']}!",
            show_alert=True,
        )

        active_jobs = await JobService.get_user_active_jobs(session, user.id)
        branch_jobs = JobService.get_job_catalog().get(branch, {})
        text = format_market_catalog(branch, branch_jobs, new_job)
        keyboard = get_branch_detail_keyboard(
            branch, is_employed=True, active_count=len(active_jobs)
        )

        if query.message:
            try:
                await query.message.edit_text(text=text, reply_markup=keyboard)
            except Exception:
                pass
    except ValueError as e:
        await query.answer(str(e), show_alert=True)


@jobs_router.callback_query(F.data.startswith("jobs:quit:"))
async def handle_jobs_quit(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Quit an active profession, retaining level and shifts completed."""
    if not query.data:
        await query.answer()
        return

    branch = query.data.split(":")[-1]
    try:
        quit_job = await JobService.quit_job(session, user.id, branch)
        await session.commit()

        await query.answer(
            "Вы уволились с этой работы. Ваш накопленный прогресс сохранен!",
            show_alert=True,
        )

        active_jobs = await JobService.get_user_active_jobs(session, user.id)
        branch_jobs = JobService.get_job_catalog().get(branch, {})
        text = format_market_catalog(branch, branch_jobs, quit_job)
        keyboard = get_branch_detail_keyboard(
            branch, is_employed=False, active_count=len(active_jobs)
        )

        if query.message:
            try:
                await query.message.edit_text(text=text, reply_markup=keyboard)
            except Exception:
                pass
    except ValueError as e:
        await query.answer(str(e), show_alert=True)


@jobs_router.callback_query(F.data.startswith("jobs:shift:start:"))
async def handle_jobs_shift_start(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Start 2-hour work shift for a branch."""
    if not query.data:
        await query.answer()
        return

    branch = query.data.split(":")[-1]
    try:
        await JobService.start_shift(session, user.id, branch)
        await session.commit()

        await query.answer(
            "⏱ Смена начата! Длительность: 2 часа. Возвращайтесь за зарплатой!",
            show_alert=True,
        )

        active_jobs = await JobService.get_user_active_jobs(session, user.id)
        text = format_career_dashboard(user, active_jobs)
        keyboard = get_jobs_menu_keyboard(active_jobs)

        if query.message:
            try:
                await query.message.edit_text(text=text, reply_markup=keyboard)
            except Exception:
                pass
    except ValueError as e:
        await query.answer(str(e), show_alert=True)


@jobs_router.callback_query(F.data.startswith("jobs:shift:claim:"))
async def handle_jobs_shift_claim(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Claim salary for a completed shift."""
    if not query.data:
        await query.answer()
        return

    branch = query.data.split(":")[-1]
    try:
        payout, event_text = await JobService.claim_salary(session, user.id, branch)
        await session.commit()

        user_job = await JobService.get_user_job_by_branch(session, user.id, branch)
        overview = await BankingService.get_user_overview(session, user.id)
        main_bal = overview["main_balance"]

        if user_job:
            card_text = format_shift_claimed(user_job, event_text, payout, main_bal)
        else:
            card_text = f"💰 Зарплата получена: +{payout:,.2f} ₽"

        active_jobs = await JobService.get_user_active_jobs(session, user.id)
        keyboard = get_jobs_menu_keyboard(active_jobs)

        await query.answer(f"💰 Зарплата получена: +{payout:,.2f} ₽!", show_alert=True)

        if query.message:
            try:
                await query.message.edit_text(text=card_text, reply_markup=keyboard)
            except Exception:
                pass
    except ValueError as e:
        await query.answer(str(e), show_alert=True)


@jobs_router.callback_query(F.data.startswith("jobs:promote:"))
async def handle_jobs_promote(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Promote user to the next career tier if eligible."""
    if not query.data:
        await query.answer()
        return

    branch = query.data.split(":")[-1]
    try:
        promoted, msg = await JobService.promote_job(session, user.id, branch)
        await session.commit()

        if promoted:
            await query.answer("🎉 Поздравляем с повышением!", show_alert=True)
            active_jobs = await JobService.get_user_active_jobs(session, user.id)
            text = f"{msg}\n\n" + format_career_dashboard(user, active_jobs)
            keyboard = get_jobs_menu_keyboard(active_jobs)
            if query.message:
                try:
                    await query.message.edit_text(text=text, reply_markup=keyboard)
                except Exception:
                    pass
        else:
            await query.answer(msg, show_alert=True)
    except ValueError as e:
        await query.answer(str(e), show_alert=True)


@jobs_router.callback_query(F.data == "jobs:hustle")
async def handle_jobs_hustle(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Initiate a quick hustle dilemma with options."""
    now = datetime.datetime.now()
    cd = JobService.DEFAULT_HUSTLE_COOLDOWN_SECONDS
    if user.last_hustle_at:
        elapsed = (now - user.last_hustle_at).total_seconds()
        if elapsed < cd:
            rem_mins = max(1, int((cd - elapsed) // 60))
            await query.answer(
                f"Шабашкины руки должны отдохнуть! До следующей подработки: ~{rem_mins} мин.",
                show_alert=True,
            )
            return

    quest = JobService.get_hustle_scenario()
    text = format_hustle_quest(quest)
    keyboard = get_hustle_keyboard(quest["options"], hustle_id=quest["id"])

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=keyboard)
        except Exception:
            pass
    await query.answer()


@jobs_router.callback_query(F.data.startswith("jobs:hustle:choose:"))
async def handle_jobs_hustle_choose(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Execute selected quick hustle decision."""
    if not query.data:
        await query.answer()
        return

    parts = query.data.split(":")
    if len(parts) >= 5:
        hustle_id = parts[3]
        choice_idx = int(parts[4])
    else:
        choice_idx = int(parts[3])
        hustle_id = JobService.get_hustle_catalog()[0]["id"]

    try:
        delta, outcome_msg = await JobService.execute_hustle(
            session, user.id, hustle_id, choice_idx
        )
        await session.commit()

        overview = await BankingService.get_user_overview(session, user.id)
        main_bal = overview["main_balance"]
        quest = JobService.get_hustle_scenario(hustle_id)

        text = format_hustle_result(
            quest=quest,
            choice_idx=choice_idx,
            delta=delta,
            outcome_msg=outcome_msg,
            new_balance=main_bal,
        )

        if delta > 0:
            alert = f"💰 Заработано: +{delta:,.2f} ₽!"
        elif delta < 0:
            alert = f"💥 Потери: -{abs(delta):,.2f} ₽!"
        else:
            alert = "⚖️ Баланс не изменился (защита от овердрафта)"

        await query.answer(alert, show_alert=True)

        active_jobs = await JobService.get_user_active_jobs(session, user.id)
        keyboard = get_jobs_menu_keyboard(active_jobs)

        if query.message:
            try:
                await query.message.edit_text(text=text, reply_markup=keyboard)
            except Exception:
                pass
    except ValueError as e:
        await query.answer(str(e), show_alert=True)


@jobs_router.callback_query(F.data == "main_menu")
async def handle_main_menu(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Return to main neobank overview dashboard."""
    overview = await BankingService.get_user_overview(session, user.id)
    full_name = html.escape(user.full_name or "Клиент", quote=False)
    main_bal = overview["main_balance"]
    savings_bal = overview["savings_balance"]
    cb_bal = overview["cashback_balance"]
    port_val = overview["portfolio_value"]
    net_worth = overview["total_net_worth"]

    text = (
        f"🟡 <b>Добро пожаловать в Т-Банк Онлайн!</b>\n\n"
        f"Привет, <b>{full_name}</b>! Ваша дебетовая карта <b>Т-Банк Black</b> активна и готова к операциям.\n\n"
        f"💰 <b>Счета и балансы:</b>\n"
        f"• Карта Black: <b>{main_bal:.2f} ₽</b>\n"
        f"• Накопительный сейф (18%): <b>{savings_bal:.2f} ₽</b>\n"
        f"• Бонусный кэшбэк: <b>{cb_bal:.2f} ₽</b>\n"
        f"• Портфель Т-Инвестиций: <b>{port_val:.2f} ₽</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>Общий капитал:</b> <b>{net_worth:.2f} ₽</b>\n\n"
        f"Выберите нужный раздел в меню ниже:"
    )

    if query.message:
        try:
            await query.message.edit_text(text=text)
        except Exception:
            pass
    await query.answer("Главное меню")
