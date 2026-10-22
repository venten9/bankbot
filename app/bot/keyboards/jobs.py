"""Inline Keyboard Markups for Jobs, Careers, and Hustles (Milestone 6)."""

import datetime
from typing import Any, Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database.models import UserJob
from app.services.jobs import JobBranch, JobService


def get_jobs_menu_keyboard(
    active_jobs: list[UserJob],
    now: datetime.datetime | None = None,
) -> InlineKeyboardMarkup:
    """Return main career dashboard inline keyboard.

    Shows dynamic buttons for active jobs (status, shift start, salary claim, promotion),
    quick hustles, job market catalog, and navigation back to main menu.
    """
    buttons: list[list[InlineKeyboardButton]] = []
    current_time = now or datetime.datetime.now()

    # 1. Active jobs dynamic action rows
    for job in active_jobs:
        job_info = JobService.get_job_info(job.branch, job.level)

        # Shift status / action button
        if not job.is_on_shift:
            buttons.append([
                InlineKeyboardButton(
                    text=f"▶️ Начать смену: {job_info['title']}",
                    callback_data=f"jobs:shift:start:{job.branch}",
                )
            ])
        else:
            elapsed = (
                (current_time - job.shift_started_at).total_seconds()
                if job.shift_started_at
                else 0
            )
            duration = JobService.DEFAULT_SHIFT_DURATION_SECONDS
            if elapsed >= duration:
                buttons.append([
                    InlineKeyboardButton(
                        text=f"💰 Забрать зарплату ({job.branch}): {job_info['salary']:.0f} ₽",
                        callback_data=f"jobs:shift:claim:{job.branch}",
                    )
                ])
            else:
                rem_mins = max(1, int((duration - elapsed) // 60))
                buttons.append([
                    InlineKeyboardButton(
                        text=f"⏳ На смене (~{rem_mins}м): {job_info['title']}",
                        callback_data=f"jobs:shift:claim:{job.branch}",
                    )
                ])

        # Promotion button if qualified
        if job.level < 5:
            req_shifts = job_info["shifts_to_promote"]
            if job.shifts_completed >= req_shifts:
                buttons.append([
                    InlineKeyboardButton(
                        text=f"⭐ Повышение доступно! ({job.branch})",
                        callback_data=f"jobs:promote:{job.branch}",
                    )
                ])

    # 2. Main «Заработок» Hub Core Portals
    buttons.append([
        InlineKeyboardButton(
            text="💼 Мои работы и смены",
            callback_data="jobs:market" if not active_jobs else "jobs:menu",
        ),
        InlineKeyboardButton(
            text="⚡️ Быстрая шабашка",
            callback_data="jobs:hustle",
        ),
    ])
    buttons.append([
        InlineKeyboardButton(
            text="⚽️ Букмекер (Ставки до 20M ₽)",
            callback_data="bet:menu",
        ),
    ])
    buttons.append([
        InlineKeyboardButton(
            text="🎲 PvP Дуэли и Азарт",
            callback_data="duel:menu",
        ),
    ])

    # 3. Vacancy catalog button
    buttons.append([
        InlineKeyboardButton(
            text="📋 Биржа вакансий",
            callback_data="jobs:market",
        ),
    ])

    # 4. Navigation row (Refresh & Back to Main Bank Menu)
    buttons.append([
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data="jobs:menu",
        ),
        InlineKeyboardButton(
            text="🔙 В главное меню",
            callback_data="main_menu",
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_job_market_keyboard(
    active_branches: Iterable[str] | None = None,
) -> InlineKeyboardMarkup:
    """Return job market catalog keyboard for branch selection.

    Categorized by the 4 career branches: IT, SERVICE, FINANCE, SHADOW.
    """
    active_set = set(active_branches or [])

    def branch_label(branch: str, default_label: str) -> str:
        if branch in active_set:
            return f"{default_label} (Вы тут)"
        return default_label

    buttons = [
        [
            InlineKeyboardButton(
                text=branch_label(JobBranch.IT, "💻 IT / Офис"),
                callback_data=f"jobs:market:{JobBranch.IT}",
            ),
            InlineKeyboardButton(
                text=branch_label(JobBranch.SERVICE, "🍔 Общепит и Услуги"),
                callback_data=f"jobs:market:{JobBranch.SERVICE}",
            ),
        ],
        [
            InlineKeyboardButton(
                text=branch_label(JobBranch.FINANCE, "📊 Финансы и Банкинг"),
                callback_data=f"jobs:market:{JobBranch.FINANCE}",
            ),
            InlineKeyboardButton(
                text=branch_label(JobBranch.SHADOW, "🕶 Теневой сектор"),
                callback_data=f"jobs:market:{JobBranch.SHADOW}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="« В меню карьеры",
                callback_data="jobs:menu",
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_branch_detail_keyboard(
    branch: str,
    is_employed: bool,
    active_count: int = 0,
) -> InlineKeyboardMarkup:
    """Return branch detail keyboard with Hire or Quit button.

    If user is currently employed in this branch -> shows Quit button.
    Else -> shows Hire/Apply button.
    """
    buttons: list[list[InlineKeyboardButton]] = []

    if is_employed:
        buttons.append([
            InlineKeyboardButton(
                text="🚪 Уволиться с этой должности",
                callback_data=f"jobs:quit:{branch}",
            )
        ])
    else:
        apply_label = "💼 Устроиться на работу"
        if active_count >= JobService.MAX_ACTIVE_JOBS:
            apply_label = "💼 Устроиться (лимит 2 мест)"
        buttons.append([
            InlineKeyboardButton(
                text=apply_label,
                callback_data=f"jobs:apply:{branch}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="« К отраслям",
            callback_data="jobs:market",
        ),
        InlineKeyboardButton(
            text="« В меню карьеры",
            callback_data="jobs:menu",
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_hustle_keyboard(
    options: list[dict[str, Any]],
    hustle_id: str | None = None,
) -> InlineKeyboardMarkup:
    """Return interactive decision inline keyboard for active quick hustle dilemma."""
    buttons: list[list[InlineKeyboardButton]] = []

    for idx, opt in enumerate(options):
        raw_text = opt.get("text", f"Вариант {idx + 1}")
        btn_text = f"{idx + 1}️⃣ {raw_text[:40]}" if len(raw_text) > 40 else f"{idx + 1}️⃣ {raw_text}"
        cb_data = (
            f"jobs:hustle:choose:{hustle_id}:{idx}"
            if hustle_id
            else f"jobs:hustle:choose:{idx}"
        )
        buttons.append([
            InlineKeyboardButton(
                text=btn_text,
                callback_data=cb_data,
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="❌ Отказаться / Назад",
            callback_data="jobs:menu",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Aliases for flexible import compatibility
get_jobs_dashboard_keyboard = get_jobs_menu_keyboard
get_jobs_market_keyboard = get_job_market_keyboard
get_job_branch_detail_keyboard = get_branch_detail_keyboard
