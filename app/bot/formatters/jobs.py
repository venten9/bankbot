"""Jobs, Careers & Quick Hustles Visual Formatters for Telegram UI (Milestone 6)."""

import datetime
import html
from decimal import Decimal
from typing import Any

from app.database.models import User, UserJob
from app.services.jobs import JOB_CATALOG, JobBranch, JobService


def format_progress_bar(percentage: float, total_blocks: int = 10) -> str:
    """Render a Unicode progress bar (e.g., █████░░░░░ 50%)."""
    clamped_pct = max(0.0, min(100.0, float(percentage)))
    k = int(round((clamped_pct / 100.0) * total_blocks))
    k = max(0, min(total_blocks, k))
    return "█" * k + "░" * (total_blocks - k)


def format_promotion_progress_bar(
    completed: int, required: int, total_blocks: int = 10
) -> str:
    """Render a promotion progress bar with completed/required counts."""
    if required <= 0:
        return f"{'█' * total_blocks} MAX"
    pct = (completed / required) * 100.0
    bar = format_progress_bar(pct, total_blocks)
    return f"{bar} {completed}/{required}"


def format_time_remaining(seconds: int | float) -> str:
    """Format duration in seconds into friendly Russian string."""
    secs_int = int(seconds)
    if secs_int <= 0:
        return "Готово"
    hours = secs_int // 3600
    minutes = (secs_int % 3600) // 60
    secs = secs_int % 60
    if hours > 0:
        return f"{hours} ч {minutes} мин"
    if minutes > 0:
        return f"{minutes} мин {secs} сек"
    return f"{secs} сек"


def format_career_dashboard(
    user: User,
    active_jobs: list[UserJob],
    user_stats: dict[str, Any] | None = None,
    now: datetime.datetime | None = None,
) -> str:
    """Format comprehensive career dashboard card for Telegram HTML."""
    current_time = now or datetime.datetime.now()
    user_display = html.escape(user.full_name or user.username or f"Клиент #{user.id}", quote=False)

    lines = [
        "💼 <b>Т-Банк Карьера и Бизнес</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"👤 Сотрудник: <b>{user_display}</b>",
        f"📌 Занято вакансий: <b>{len(active_jobs)}/2</b>\n",
    ]

    if not active_jobs:
        lines.append(
            "<i>У вас пока нет активной работы. Откройте «Биржу вакансий», "
            "чтобы устроиться в IT, Общепит, Финансы или Теневой сектор!</i>\n"
        )
    else:
        for idx, job in enumerate(active_jobs, 1):
            job_info = JobService.get_job_info(job.branch, job.level)
            branch_title = JobBranch.get_title(job.branch)
            lines.append(f"<b>{idx}. {branch_title}</b>: {html.escape(job_info['title'])} (Уровень {job.level}/5)")
            lines.append(f"   💵 Оклад за смену: <b>{job_info['salary']:,.2f} ₽</b>")

            # Shift status
            if not job.is_on_shift:
                lines.append("   ⏱ Статус: ⏸ Не на смене")
            else:
                elapsed = (
                    (current_time - job.shift_started_at).total_seconds()
                    if job.shift_started_at
                    else 0
                )
                duration = JobService.DEFAULT_SHIFT_DURATION_SECONDS
                if elapsed >= duration:
                    lines.append("   ⏱ Статус: 🔔 <b>Смена окончена! Заберите зарплату.</b>")
                else:
                    rem_sec = duration - elapsed
                    lines.append(
                        f"   ⏱ Статус: ⏳ <b>На смене</b> (осталось ~{format_time_remaining(rem_sec)})"
                    )

            # Promotion progress
            if job.level >= 5:
                lines.append("   📈 Прогресс: 🏆 <i>Максимальная должность</i>\n")
            else:
                req = job_info["shifts_to_promote"]
                pct = min(100.0, (job.shifts_completed / req) * 100.0) if req > 0 else 100.0
                bar = format_progress_bar(pct, 10)
                if job.shifts_completed >= req:
                    lines.append(
                        f"   📈 Прогресс: [{bar}] {job.shifts_completed}/{req} — ⭐ <b>Доступно повышение!</b>\n"
                    )
                else:
                    lines.append(f"   📈 Прогресс: [{bar}] {job.shifts_completed}/{req} смен\n")

    # Hustle section
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    cd = JobService.DEFAULT_HUSTLE_COOLDOWN_SECONDS
    if user.last_hustle_at:
        elapsed_hustle = (current_time - user.last_hustle_at).total_seconds()
        if elapsed_hustle < cd:
            rem_cd = cd - elapsed_hustle
            lines.append(f"🎲 <b>Быстрые шабашки</b>: ⏳ Отдых (~{format_time_remaining(rem_cd)})")
        else:
            lines.append("🎲 <b>Быстрые шабашки</b>: ⚡️ <b>Доступны прямо сейчас!</b>")
    else:
        lines.append("🎲 <b>Быстрые шабашки</b>: ⚡️ <b>Доступны прямо сейчас!</b>")

    lines.append("\n<i>Совмещайте до 2 работ, растите по карьерной лестнице и зарабатывайте на шабашках!</i>")
    return "\n".join(lines)


def format_market_catalog(
    branch: str,
    branch_jobs: dict[int, dict[str, Any]] | None = None,
    user_job: UserJob | None = None,
) -> str:
    """Format branch profession catalog with 5 tiers, salaries, and hiring state."""
    catalog = branch_jobs or JOB_CATALOG.get(branch, {})
    branch_title = JobBranch.get_title(branch)

    lines = [
        f"🏛 <b>Карьерная лестница: {branch_title}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if user_job and user_job.is_active:
        lines.append(
            f"📌 Текущий статус: <b>Вы работаете здесь</b> (Уровень {user_job.level}/5)\n"
            f"Отработано смен на текущем уровне: <b>{user_job.shifts_completed}</b>\n"
        )
    elif user_job and not user_job.is_active:
        lines.append(
            f"📌 Текущий статус: <b>Ранее уволен</b> (Сохранен уровень {user_job.level}/5, смен: {user_job.shifts_completed})\n"
            "<i>При повторном трудоустройстве вы вернетесь на сохраненный уровень!</i>\n"
        )
    else:
        lines.append("📌 Текущий статус: <i>Вы еще не работали в этой сфере</i>\n")

    for level in range(1, 6):
        tier = catalog.get(level, {})
        title = html.escape(tier.get("title", f"Должность {level}"))
        salary = tier.get("salary", Decimal("0.00"))
        req_shifts = tier.get("shifts_to_promote", 0)
        desc = html.escape(tier.get("desc", ""))

        if user_job and user_job.is_active and user_job.level == level:
            marker = "👉 "
        elif user_job and user_job.level >= level:
            marker = "✅ "
        else:
            marker = "🔒 " if level > 1 else "🔹 "

        req_str = f"Нужно {req_shifts} смен" if req_shifts > 0 else "Вершина карьеры"
        lines.append(f"<b>{marker}Уровень {level}: {title}</b>")
        lines.append(f"   💵 Оклад: <b>{salary:,.2f} ₽</b> | {req_str}")
        lines.append(f"   <i>{desc}</i>\n")

    return "\n".join(lines)


def format_branch_catalog(active_jobs_count: int = 0) -> str:
    """Format market branch selector overview."""
    return (
        "🏛 <b>Биржа труда Т-Банка</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите отрасль для просмотра карьерной лестницы и трудоустройства.\n\n"
        f"💼 Ваших активных работ: <b>{active_jobs_count}/2</b>\n\n"
        "💻 <b>IT / Офис</b> — от эникея до CTO с опционами\n"
        "🍔 <b>Общепит и Услуги</b> — от промоутера-сосиски до короля шаурмы\n"
        "📊 <b>Финансы и Банкинг</b> — от агента у метро до топа Т-Банка\n"
        "🕶 <b>Теневой сектор</b> — рискованные схемы с огромным кушем\n"
    )


def format_branch_detail(
    branch: str,
    user_job: UserJob | None = None,
    active_jobs_count: int = 0,
) -> str:
    """Format branch detail view (alias helper)."""
    return format_market_catalog(branch, JOB_CATALOG.get(branch, {}), user_job)


def format_hustle_quest(quest: dict[str, Any]) -> str:
    """Format interactive quick hustle situation dilemma and decision options."""
    title = html.escape(quest.get("title", "Быстрая подработка"))
    text = html.escape(quest.get("text", ""))
    branch = quest.get("branch", "")
    branch_title = JobBranch.get_title(branch) if branch else "Разное"

    lines = [
        f"🎲 <b>Шабашка: {title}</b> ({branch_title})",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"<i>{text}</i>\n",
        "<b>Варианты решения:</b>",
    ]

    for idx, opt in enumerate(quest.get("options", [])):
        opt_text = html.escape(opt.get("text", f"Вариант {idx + 1}"))
        success_pct = int(round(opt.get("success_rate", 0.5) * 100))
        rew_min = opt.get("reward_min", 0)
        rew_max = opt.get("reward_max", 0)
        pen_min = opt.get("penalty_min", 0)
        pen_max = opt.get("penalty_max", 0)

        rew_str = (
            f"+{rew_min:,.0f}..+{rew_max:,.0f} ₽"
            if rew_min != rew_max
            else f"+{rew_min:,.0f} ₽"
        )
        if pen_max > 0:
            pen_str = (
                f"-{pen_min:,.0f}..-{pen_max:,.0f} ₽"
                if pen_min != pen_max
                else f"-{pen_min:,.0f} ₽"
            )
        else:
            pen_str = "Без штрафа (0 ₽)"

        lines.append(
            f"<b>{idx + 1}️⃣ {opt_text}</b>\n"
            f"   🎯 Шанс успеха: <b>{success_pct}%</b>\n"
            f"   💰 Награда: <b>{rew_str}</b>\n"
            f"   ⚠️ Риск: <b>{pen_str}</b>\n"
        )

    lines.append("<i>Выберите вариант на клавиатуре ниже:</i>")
    return "\n".join(lines)


def format_hustle_result(
    result: dict[str, Any] | None = None,
    *,
    quest: dict[str, Any] | None = None,
    choice_idx: int = 0,
    delta: Decimal | None = None,
    outcome_msg: str = "",
    new_balance: Decimal | None = None,
) -> str:
    """Format outcome card for quick hustle decision."""
    if result:
        delta = delta if delta is not None else result.get("delta")
        outcome_msg = outcome_msg or result.get("outcome_msg") or result.get("msg") or ""
        new_balance = new_balance if new_balance is not None else result.get("new_balance")
        quest = quest or result.get("quest")

    delta_val = delta if delta is not None else Decimal("0.00")
    escaped_msg = html.escape(outcome_msg)

    if delta_val > 0:
        header = "🎉 <b>Успешная подработка!</b>"
        delta_str = f"<b>+{delta_val:,.2f} ₽</b>"
    elif delta_val < 0:
        header = "💥 <b>Неудача на шабашке!</b>"
        delta_str = f"<b>-{abs(delta_val):,.2f} ₽</b>"
    else:
        header = "⚖️ <b>Результат шабашки</b>"
        delta_str = "<b>0.00 ₽</b> (защита от овердрафта)"

    lines = [
        header,
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        escaped_msg if escaped_msg else f"Сумма изменения: {delta_str}",
        "",
        f"💵 Изменение баланса: {delta_str}",
    ]

    if new_balance is not None:
        lines.append(f"💳 Текущий баланс карты: <b>{new_balance:,.2f} ₽</b>")

    lines.append("\n⏳ <i>Следующая шабашка будет доступна через 15 минут.</i>")
    return "\n".join(lines)


def format_shift_claimed(
    job: UserJob,
    event_name: str,
    salary_credited: Decimal,
    new_balance: Decimal,
    job_info: dict[str, Any] | None = None,
) -> str:
    """Format result card upon shift completion and salary crediting."""
    info = job_info or JobService.get_job_info(job.branch, job.level)
    branch_title = JobBranch.get_title(job.branch)
    escaped_event = html.escape(event_name)

    lines = [
        "💰 <b>Смена успешно завершена!</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🏢 Отрасль: <b>{branch_title}</b>",
        f"💼 Должность: <b>{html.escape(info['title'])}</b> (Уровень {job.level}/5)",
        f"💵 Начислено на карту: <b>+{salary_credited:,.2f} ₽</b>",
        f"💳 Новый баланс карты: <b>{new_balance:,.2f} ₽</b>",
    ]

    if escaped_event and escaped_event != "Обычная смена":
        if "Облава" in escaped_event or "Штраф" in escaped_event or "Выговор" in escaped_event:
            lines.append(f"\n⚠️ <b>Событие на смене:</b>\n<i>{escaped_event}</i>")
        else:
            lines.append(f"\n🎁 <b>Событие на смене:</b>\n<i>{escaped_event}</i>")

    lines.append(f"\n📊 Отработано смен на уровне: <b>{job.shifts_completed}</b>")

    if job.level < 5:
        req = info["shifts_to_promote"]
        if job.shifts_completed >= req:
            lines.append(
                "\n⭐ <b>Вам доступно повышение в должности!</b>\n"
                "Нажмите «Повышение» в меню карьеры."
            )
        else:
            lines.append(f"До следующего повышения: <b>{req - job.shifts_completed}</b> смен(ы).")
    else:
        lines.append("\n🏆 <i>Вы достигли максимального уровня в этой профессии!</i>")

    return "\n".join(lines)
