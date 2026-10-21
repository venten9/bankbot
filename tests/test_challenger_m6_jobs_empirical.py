"""Empirical Challenger Test Suite for Milestone 6: Jobs Keyboards & Formatters.

Thoroughly stress-tests:
1. Keyboard factories in app/bot/keyboards/jobs.py:
   - 64-byte callback_data constraint on EVERY single button.
   - Layout matrix: 0 jobs, 1 job, 2 jobs.
   - Shift states: not on shift, on shift (< 2h), ready to claim (>= 2h).
   - Promotion states: eligible (level < 5, shifts >= req), ineligible, Level 5 max tier.
   - Branch detail keyboard toggling (apply vs quit, capacity warning for >= 2 active jobs).
   - Quick hustle keyboards for all scenarios in catalog, with and without hustle_id.
2. Formatter functions in app/bot/formatters/jobs.py:
   - Strict HTML escaping with adversarial XSS payloads.
   - Edge cases: Level 5 job (0 shifts to promote, no ZeroDivisionError), 0 shifts, negative / giant values.
   - Time formatting: 0s, 30s, 7200s, 100000s, negative durations.
   - Unicode progress bar boundary handling (0%, 50%, 100%, clamped negatives and overflows).
"""

import datetime
from decimal import Decimal
from typing import Any
import pytest

from app.bot.formatters.jobs import (
    format_branch_catalog,
    format_branch_detail,
    format_career_dashboard,
    format_hustle_quest,
    format_hustle_result,
    format_market_catalog,
    format_progress_bar,
    format_promotion_progress_bar,
    format_shift_claimed,
    format_time_remaining,
)
from app.bot.keyboards.jobs import (
    get_branch_detail_keyboard,
    get_hustle_keyboard,
    get_job_market_keyboard,
    get_jobs_menu_keyboard,
)
from app.database.models import User, UserJob
from app.services.jobs import HUSTLE_CATALOG, JOB_CATALOG, JobBranch, JobService


# ============================================================================
# 1. KEYBOARD FACTORY EMPIRICAL CHALLENGES
# ============================================================================


class TestJobsKeyboardEmpirical:
    """Empirical verification of keyboard factories, button limits, and layout logic."""

    @staticmethod
    def _assert_all_callbacks_within_64_bytes(keyboard) -> None:
        """Verify that every button in the keyboard has callback_data <= 64 bytes (UTF-8)."""
        assert keyboard.inline_keyboard, "Keyboard must have at least one button row"
        for row_idx, row in enumerate(keyboard.inline_keyboard):
            for btn_idx, btn in enumerate(row):
                assert btn.callback_data is not None, (
                    f"Button at [{row_idx}][{btn_idx}] text='{btn.text}' has None callback_data"
                )
                cb_bytes = btn.callback_data.encode("utf-8")
                assert len(cb_bytes) <= 64, (
                    f"Button '{btn.text}' callback_data '{btn.callback_data}' exceeds 64 bytes: "
                    f"{len(cb_bytes)} bytes"
                )

    def test_dashboard_keyboard_zero_jobs(self):
        """Zero jobs: only general actions (hustle, market) and navigation (refresh, back)."""
        kb = get_jobs_menu_keyboard([])
        self._assert_all_callbacks_within_64_bytes(kb)

        # Flatten buttons
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "jobs:hustle" in all_cb
        assert "jobs:market" in all_cb
        assert "jobs:menu" in all_cb
        assert "main_menu" in all_cb

        # Should not have any shift or promote buttons
        for cb in all_cb:
            assert not cb.startswith("jobs:shift:")
            assert not cb.startswith("jobs:promote:")

    def test_dashboard_keyboard_one_job_not_on_shift(self):
        """1 job not on shift: should show '▶️ Начать смену'."""
        job = UserJob(
            id=1,
            user_id=101,
            branch=JobBranch.IT,
            level=1,
            shifts_completed=0,
            is_active=True,
            is_on_shift=False,
            shift_started_at=None,
        )
        kb = get_jobs_menu_keyboard([job])
        self._assert_all_callbacks_within_64_bytes(kb)

        all_btns = [btn for row in kb.inline_keyboard for btn in row]
        start_btns = [b for b in all_btns if b.callback_data == "jobs:shift:start:IT"]
        assert len(start_btns) == 1
        assert "Начать смену" in start_btns[0].text

    def test_dashboard_keyboard_one_job_on_shift_in_progress(self):
        """1 job on shift with elapsed < 2 hours (e.g., 30 mins elapsed): shows countdown."""
        now = datetime.datetime.now()
        started = now - datetime.timedelta(minutes=30)
        job = UserJob(
            id=2,
            user_id=101,
            branch=JobBranch.SERVICE,
            level=2,
            shifts_completed=1,
            is_active=True,
            is_on_shift=True,
            shift_started_at=started,
        )
        kb = get_jobs_menu_keyboard([job], now=now)
        self._assert_all_callbacks_within_64_bytes(kb)

        all_btns = [btn for row in kb.inline_keyboard for btn in row]
        claim_btns = [b for b in all_btns if b.callback_data == "jobs:shift:claim:SERVICE"]
        assert len(claim_btns) == 1
        assert "На смене (~90м)" in claim_btns[0].text

    def test_dashboard_keyboard_one_job_on_shift_ready_to_claim(self):
        """1 job on shift with elapsed >= 2 hours: shows claim salary button with amount."""
        now = datetime.datetime.now()
        started = now - datetime.timedelta(seconds=7200)
        job = UserJob(
            id=3,
            user_id=101,
            branch=JobBranch.FINANCE,
            level=3,
            shifts_completed=4,
            is_active=True,
            is_on_shift=True,
            shift_started_at=started,
        )
        kb = get_jobs_menu_keyboard([job], now=now)
        self._assert_all_callbacks_within_64_bytes(kb)

        all_btns = [btn for row in kb.inline_keyboard for btn in row]
        claim_btns = [b for b in all_btns if b.callback_data == "jobs:shift:claim:FINANCE"]
        assert len(claim_btns) == 1
        assert "Забрать зарплату" in claim_btns[0].text
        job_info = JobService.get_job_info(JobBranch.FINANCE, 3)
        assert f"{job_info['salary']:.0f}" in claim_btns[0].text

    def test_dashboard_keyboard_promotion_button_appearance(self):
        """Job eligible for promotion (shifts_completed >= req) must show promotion button."""
        # Level 1 IT requires 3 shifts for promotion
        job_promo = UserJob(
            id=4,
            user_id=101,
            branch=JobBranch.IT,
            level=1,
            shifts_completed=3,
            is_active=True,
            is_on_shift=False,
        )
        kb = get_jobs_menu_keyboard([job_promo])
        self._assert_all_callbacks_within_64_bytes(kb)

        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "jobs:promote:IT" in all_cb

        # But if shifts_completed < 3, no promotion button
        job_not_ready = UserJob(
            id=5,
            user_id=101,
            branch=JobBranch.IT,
            level=1,
            shifts_completed=2,
            is_active=True,
            is_on_shift=False,
        )
        kb_not = get_jobs_menu_keyboard([job_not_ready])
        all_cb_not = [btn.callback_data for row in kb_not.inline_keyboard for btn in row]
        assert "jobs:promote:IT" not in all_cb_not

    def test_dashboard_keyboard_level_5_never_shows_promotion(self):
        """Level 5 (max tier) job must never show promotion button regardless of shift count."""
        job_lvl5 = UserJob(
            id=6,
            user_id=101,
            branch=JobBranch.SHADOW,
            level=5,
            shifts_completed=999,
            is_active=True,
            is_on_shift=False,
        )
        kb = get_jobs_menu_keyboard([job_lvl5])
        self._assert_all_callbacks_within_64_bytes(kb)

        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "jobs:promote:SHADOW" not in all_cb

    def test_dashboard_keyboard_two_jobs_complex_layout(self):
        """2 jobs with mixed statuses: Job 1 on shift < 2h, Job 2 ready to claim and eligible for promotion."""
        now = datetime.datetime.now()
        job1 = UserJob(
            id=7,
            user_id=101,
            branch=JobBranch.IT,
            level=2,
            shifts_completed=1,
            is_active=True,
            is_on_shift=True,
            shift_started_at=now - datetime.timedelta(minutes=45),
        )
        # Job 2: FINANCE level 1 has 3 shifts completed (req is 3) and shift completed 2h ago
        job2 = UserJob(
            id=8,
            user_id=101,
            branch=JobBranch.FINANCE,
            level=1,
            shifts_completed=3,
            is_active=True,
            is_on_shift=True,
            shift_started_at=now - datetime.timedelta(hours=3),
        )

        kb = get_jobs_menu_keyboard([job1, job2], now=now)
        self._assert_all_callbacks_within_64_bytes(kb)

        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        # IT on shift
        assert "jobs:shift:claim:IT" in all_cb
        # FINANCE ready to claim
        assert "jobs:shift:claim:FINANCE" in all_cb
        # FINANCE eligible for promotion
        assert "jobs:promote:FINANCE" in all_cb
        # IT NOT eligible for promotion
        assert "jobs:promote:IT" not in all_cb
        # General navigation
        assert "jobs:hustle" in all_cb
        assert "jobs:market" in all_cb
        assert "jobs:menu" in all_cb
        assert "main_menu" in all_cb

    def test_market_keyboard_active_branch_indicators(self):
        """Market keyboard must mark active branches with '(Вы тут)' and respect 64 bytes."""
        # Case 1: no active branches
        kb1 = get_job_market_keyboard([])
        self._assert_all_callbacks_within_64_bytes(kb1)
        all_text1 = [btn.text for row in kb1.inline_keyboard for btn in row]
        for t in all_text1:
            assert "(Вы тут)" not in t

        # Case 2: IT and SHADOW active
        kb2 = get_job_market_keyboard([JobBranch.IT, JobBranch.SHADOW])
        self._assert_all_callbacks_within_64_bytes(kb2)
        btn_map = {btn.callback_data: btn.text for row in kb2.inline_keyboard for btn in row}
        assert "(Вы тут)" in btn_map[f"jobs:market:{JobBranch.IT}"]
        assert "(Вы тут)" in btn_map[f"jobs:market:{JobBranch.SHADOW}"]
        assert "(Вы тут)" not in btn_map[f"jobs:market:{JobBranch.SERVICE}"]
        assert "(Вы тут)" not in btn_map[f"jobs:market:{JobBranch.FINANCE}"]

    def test_branch_detail_keyboard_toggles_apply_and_quit(self):
        """Branch detail keyboard toggles between Apply and Quit depending on employment."""
        for branch in JobBranch.ALL:
            # 1. Employed -> Shows Quit button
            kb_quit = get_branch_detail_keyboard(branch, is_employed=True)
            self._assert_all_callbacks_within_64_bytes(kb_quit)
            quit_cb = [btn.callback_data for row in kb_quit.inline_keyboard for btn in row]
            assert f"jobs:quit:{branch}" in quit_cb
            assert f"jobs:apply:{branch}" not in quit_cb

            # 2. Not employed, under capacity (<2) -> Shows standard Apply button
            kb_apply = get_branch_detail_keyboard(branch, is_employed=False, active_count=1)
            self._assert_all_callbacks_within_64_bytes(kb_apply)
            apply_btns = [
                btn for row in kb_apply.inline_keyboard for btn in row
                if btn.callback_data == f"jobs:apply:{branch}"
            ]
            assert len(apply_btns) == 1
            assert "лимит" not in apply_btns[0].text

            # 3. Not employed, at or over capacity (>=2) -> Shows capacity warning label
            kb_full = get_branch_detail_keyboard(branch, is_employed=False, active_count=2)
            self._assert_all_callbacks_within_64_bytes(kb_full)
            full_btns = [
                btn for row in kb_full.inline_keyboard for btn in row
                if btn.callback_data == f"jobs:apply:{branch}"
            ]
            assert len(full_btns) == 1
            assert "лимит 2 мест" in full_btns[0].text

    def test_hustle_keyboard_all_catalog_options_and_lengths(self):
        """Hustle keyboard generates valid buttons for all scenarios in catalog <= 64 bytes."""
        for quest in HUSTLE_CATALOG:
            # With hustle_id
            kb_with_id = get_hustle_keyboard(quest["options"], hustle_id=quest["id"])
            self._assert_all_callbacks_within_64_bytes(kb_with_id)

            # Check that each option is indexed properly
            cbs = [btn.callback_data for row in kb_with_id.inline_keyboard for btn in row]
            for idx in range(len(quest["options"])):
                expected_cb = f"jobs:hustle:choose:{quest['id']}:{idx}"
                assert expected_cb in cbs
            assert "jobs:menu" in cbs  # cancel button

            # Without hustle_id
            kb_no_id = get_hustle_keyboard(quest["options"])
            self._assert_all_callbacks_within_64_bytes(kb_no_id)
            cbs_no_id = [btn.callback_data for row in kb_no_id.inline_keyboard for btn in row]
            for idx in range(len(quest["options"])):
                assert f"jobs:hustle:choose:{idx}" in cbs_no_id

    def test_hustle_keyboard_adversarial_long_texts(self):
        """Very long option texts must be safely truncated to <= 40 chars in button text."""
        long_opt = [
            {"text": "A" * 150},
            {"text": "🚀 Экстремально длинное описание шабашки с кучей эмодзи и подробностей" * 3},
        ]
        kb = get_hustle_keyboard(long_opt, hustle_id="test_long")
        self._assert_all_callbacks_within_64_bytes(kb)
        btn1 = kb.inline_keyboard[0][0]
        # Text prefix is '1️⃣ ' (3 characters / graphemes) + max 40 chars
        assert len(btn1.text) <= 45


# ============================================================================
# 2. FORMATTER EMPIRICAL & ADVERSARIAL CHALLENGES
# ============================================================================


class TestJobsFormatterEmpirical:
    """Empirical verification of visual formatters, HTML sanitization, and mathematical boundaries."""

    def test_progress_bar_edge_cases(self):
        """Progress bar must clamp gracefully between 0% and 100% and maintain block count."""
        # Normal
        assert format_progress_bar(50.0, 10) == "█████░░░░░"
        assert format_progress_bar(100.0, 10) == "██████████"
        assert format_progress_bar(0.0, 10) == "░░░░░░░░░░"

        # Boundary clamping
        assert format_progress_bar(-50.0, 10) == "░░░░░░░░░░"
        assert format_progress_bar(999.0, 10) == "██████████"
        assert len(format_progress_bar(33.3, 10)) == 10
        assert len(format_progress_bar(66.6, 20)) == 20

    def test_promotion_progress_bar_level_5_no_zero_division(self):
        """Level 5 has required=0 shifts: must return '██████████ MAX' with NO ZeroDivisionError."""
        res_zero = format_promotion_progress_bar(completed=0, required=0)
        assert "MAX" in res_zero
        assert "██████████" in res_zero

        res_neg = format_promotion_progress_bar(completed=5, required=-1)
        assert "MAX" in res_neg

        # Normal cases
        res_norm = format_promotion_progress_bar(completed=2, required=5)
        assert "2/5" in res_norm

        # Giant numbers
        res_giant = format_promotion_progress_bar(completed=1_000_000, required=5_000_000)
        assert "1000000/5000000" in res_giant

    def test_format_time_remaining_exact_values(self):
        """Verify exact specified durations: 0s, 30s, 7200s, 100000s and negative."""
        assert format_time_remaining(0) == "Готово"
        assert format_time_remaining(-15) == "Готово"
        assert format_time_remaining(30) == "30 сек"
        assert format_time_remaining(59) == "59 сек"
        assert format_time_remaining(60) == "1 мин 0 сек"
        assert format_time_remaining(125) == "2 мин 5 сек"
        assert format_time_remaining(3599) == "59 мин 59 сек"
        assert format_time_remaining(7200) == "2 ч 0 мин"
        assert format_time_remaining(100000) == "27 ч 46 мин"

    def test_format_career_dashboard_html_escaping_and_adversarial_name(self):
        """Career dashboard must strictly escape malicious XSS payloads in user name."""
        evil_user = User(
            id=666,
            telegram_id=999999999,
            username="<script>bad()</script>",
            full_name="<script>alert('xss')</script>&\"'<b>PWN</b>",
        )
        dashboard_text = format_career_dashboard(evil_user, [])

        # Raw <script> or unescaped <b>PWN</b> must NOT appear
        assert "<script>" not in dashboard_text
        assert "<b>PWN</b>" not in dashboard_text
        # Escaped representations must appear
        assert "&lt;script&gt;alert('xss')&lt;/script&gt;&amp;\"'&lt;b&gt;PWN&lt;/b&gt;" in dashboard_text

    def test_format_career_dashboard_various_states(self):
        """Test dashboard rendering across 0, 1, 2 jobs and shift countdowns."""
        user = User(id=1, full_name="Иван Иванов", username="ivan")

        # 0 jobs
        dash0 = format_career_dashboard(user, [])
        assert "Занято вакансий: <b>0/2</b>" in dash0
        assert "У вас пока нет активной работы" in dash0

        # 1 job on shift
        now = datetime.datetime.now()
        job1 = UserJob(
            id=10,
            user_id=1,
            branch=JobBranch.IT,
            level=3,
            shifts_completed=2,
            is_active=True,
            is_on_shift=True,
            shift_started_at=now - datetime.timedelta(minutes=30),
        )
        dash1 = format_career_dashboard(user, [job1], now=now)
        assert "Занято вакансий: <b>1/2</b>" in dash1
        assert "Джуниор-программист" in dash1
        assert "На смене" in dash1

        # 1 job shift completed ready to claim
        job1_done = UserJob(
            id=10,
            user_id=1,
            branch=JobBranch.IT,
            level=3,
            shifts_completed=8,  # req is 8 -> promotion available!
            is_active=True,
            is_on_shift=True,
            shift_started_at=now - datetime.timedelta(hours=2, minutes=5),
        )
        dash_done = format_career_dashboard(user, [job1_done], now=now)
        assert "Смена окончена! Заберите зарплату" in dash_done
        assert "Доступно повышение!" in dash_done

        # 1 job Level 5 max tier
        job_max = UserJob(
            id=11,
            user_id=1,
            branch=JobBranch.IT,
            level=5,
            shifts_completed=50,
            is_active=True,
            is_on_shift=False,
        )
        dash_max = format_career_dashboard(user, [job_max], now=now)
        assert "Максимальная должность" in dash_max
        assert "Доступно повышение" not in dash_max

    def test_format_career_dashboard_hustle_cooldown_status(self):
        """Dashboard must accurately reflect hustle cooldown status."""
        user = User(id=1, full_name="Иван Иванов")
        now = datetime.datetime.now()

        # No prior hustle
        user.last_hustle_at = None
        d1 = format_career_dashboard(user, [], now=now)
        assert "Доступны прямо сейчас!" in d1

        # Hustle on cooldown (5 minutes ago out of 15)
        user.last_hustle_at = now - datetime.timedelta(minutes=5)
        d2 = format_career_dashboard(user, [], now=now)
        assert "Отдых (~10 мин 0 сек)" in d2

        # Hustle cooldown expired (20 minutes ago)
        user.last_hustle_at = now - datetime.timedelta(minutes=20)
        d3 = format_career_dashboard(user, [], now=now)
        assert "Доступны прямо сейчас!" in d3

    def test_format_market_catalog_all_branches(self):
        """Market catalog displays all 5 tiers per branch and properly marks user status."""
        for branch in JobBranch.ALL:
            # Not employed
            txt_none = format_market_catalog(branch)
            assert "Карьерная лестница:" in txt_none
            assert "Вы еще не работали в этой сфере" in txt_none
            for level in range(1, 6):
                assert f"Уровень {level}:" in txt_none

            # Actively employed at level 2
            user_job = UserJob(
                id=1,
                user_id=1,
                branch=branch,
                level=2,
                shifts_completed=3,
                is_active=True,
            )
            txt_employed = format_market_catalog(branch, user_job=user_job)
            assert "Вы работаете здесь" in txt_employed
            assert "Отработано смен на текущем уровне: <b>3</b>" in txt_employed

            # Inactive (resigned, progress retained)
            user_job.is_active = False
            txt_resigned = format_market_catalog(branch, user_job=user_job)
            assert "Ранее уволен" in txt_resigned
            assert "Сохранен уровень 2/5" in txt_resigned

    def test_format_hustle_quest_html_escaping(self):
        """Hustle quest dilemma must escape title, descriptions, and option texts."""
        evil_quest = {
            "title": "Хакерская атака <script>alert(1)</script>",
            "branch": JobBranch.IT,
            "text": "Взлом базы Т-Банка & <b>SQL injection</b>: DROP TABLE users;",
            "options": [
                {
                    "text": "Вставить ' OR 1=1; -- <img src=x onerror=alert('hack')>",
                    "success_rate": 0.5,
                    "reward_min": 1000,
                    "reward_max": 2000,
                    "penalty_min": 500,
                    "penalty_max": 1000,
                }
            ],
        }
        res = format_hustle_quest(evil_quest)
        assert "<script>" not in res
        assert "<img src=x" not in res
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in res
        assert "&amp; &lt;b&gt;SQL injection&lt;/b&gt;" in res
        assert "&lt;img src=x" in res

    def test_format_hustle_result_positive_negative_zero(self):
        """Hustle result card formats positive rewards, negative penalties, and overdraft protection."""
        # Positive
        res_pos = format_hustle_result(
            delta=Decimal("2500.00"),
            outcome_msg="Успех! Клиент доволен <script>",
            new_balance=Decimal("12500.00"),
        )
        assert "Успешная подработка!" in res_pos
        assert "+2,500.00 ₽" in res_pos
        assert "12,500.00 ₽" in res_pos
        assert "<script>" not in res_pos
        assert "&lt;script&gt;" in res_pos

        # Negative
        res_neg = format_hustle_result(
            delta=Decimal("-1500.00"),
            outcome_msg="Вас поймали с поличным!",
            new_balance=Decimal("3500.00"),
        )
        assert "Неудача на шабашке!" in res_neg
        assert "-1,500.00 ₽" in res_neg
        assert "3,500.00 ₽" in res_neg

        # Zero (Clamped by overdraft protection)
        res_zero = format_hustle_result(
            delta=Decimal("0.00"),
            outcome_msg="Штраф списан с нулевого баланса.",
            new_balance=Decimal("0.00"),
        )
        assert "защита от овердрафта" in res_zero
        assert "0.00 ₽" in res_zero

    def test_format_shift_claimed_events_and_giant_numbers(self):
        """Shift claim summary handles normal and workplace events, giant values, and promotion alerts."""
        job_promo = UserJob(
            id=1,
            user_id=1,
            branch=JobBranch.FINANCE,
            level=2,
            shifts_completed=5,  # req is 5 for level 2
            is_active=True,
        )
        # Workplace event with evil markup
        evil_event = "Облава налоговой инспекции! <script>evasion()</script> & -50%"
        res_claimed = format_shift_claimed(
            job=job_promo,
            event_name=evil_event,
            salary_credited=Decimal("5000000000.50"),
            new_balance=Decimal("9999999999.99"),
        )
        assert "<script>" not in res_claimed
        assert "&lt;script&gt;evasion()&lt;/script&gt; &amp; -50%" in res_claimed
        assert "+5,000,000,000.50 ₽" in res_claimed
        assert "9,999,999,999.99 ₽" in res_claimed
        assert "Вам доступно повышение в должности!" in res_claimed

        # Level 5 max tier shift claim
        job_lvl5 = UserJob(
            id=2,
            user_id=1,
            branch=JobBranch.IT,
            level=5,
            shifts_completed=20,
            is_active=True,
        )
        res_lvl5 = format_shift_claimed(
            job=job_lvl5,
            event_name="Обычная смена",
            salary_credited=Decimal("50000.00"),
            new_balance=Decimal("150000.00"),
        )
        assert "Вы достигли максимального уровня в этой профессии!" in res_lvl5
        assert "Доступно повышение" not in res_lvl5

    def test_reply_keyboard_contains_jobs_button(self):
        """Persistent main menu keyboard in reply.py must contain '💼 Работа и бизнес'."""
        from app.bot.keyboards.reply import get_main_menu_keyboard
        kb = get_main_menu_keyboard()
        button_texts = [btn.text for row in kb.keyboard for btn in row]
        assert "💼 Работа и бизнес" in button_texts
        assert any(row == ["💼 Работа и бизнес"] or "💼 Работа и бизнес" in [b.text for b in row] for row in kb.keyboard)

    def test_format_time_remaining_float_inputs(self):
        """Float seconds values are safely truncated to integers."""
        assert format_time_remaining(0.0) == "Готово"
        assert format_time_remaining(29.9) == "29 сек"
        assert format_time_remaining(7200.8) == "2 ч 0 мин"
        assert format_time_remaining(100000.5) == "27 ч 46 мин"

    def test_branch_catalog_overview_and_detail_alias(self):
        """Branch catalog overview shows active job slots and branch detail alias works."""
        overview_0 = format_branch_catalog(0)
        assert "0/2" in overview_0
        assert "IT / Офис" in overview_0
        assert "Общепит и Услуги" in overview_0
        assert "Финансы и Банкинг" in overview_0
        assert "Теневой сектор" in overview_0

        overview_2 = format_branch_catalog(2)
        assert "2/2" in overview_2

        # format_branch_detail should equal format_market_catalog
        detail_res = format_branch_detail(JobBranch.IT)
        cat_res = format_market_catalog(JobBranch.IT)
        assert detail_res == cat_res

    def test_keyboard_clock_skew_and_none_shift_started_at(self):
        """Dashboard keyboard handles clock skew (future start) or missing shift_started_at gracefully."""
        now = datetime.datetime.now()
        # Shift started in the future (clock skew)
        skewed_job = UserJob(
            id=1,
            user_id=10,
            branch=JobBranch.IT,
            level=1,
            shifts_completed=0,
            is_active=True,
            is_on_shift=True,
            shift_started_at=now + datetime.timedelta(minutes=10),
        )
        kb_skew = get_jobs_menu_keyboard([skewed_job], now=now)
        TestJobsKeyboardEmpirical._assert_all_callbacks_within_64_bytes(kb_skew)

        # shift_started_at is None while is_on_shift is True
        none_job = UserJob(
            id=2,
            user_id=10,
            branch=JobBranch.SERVICE,
            level=1,
            shifts_completed=0,
            is_active=True,
            is_on_shift=True,
            shift_started_at=None,
        )
        kb_none = get_jobs_menu_keyboard([none_job], now=now)
        TestJobsKeyboardEmpirical._assert_all_callbacks_within_64_bytes(kb_none)

    def test_hustle_keyboard_empty_options(self):
        """Hustle keyboard with empty options list still generates cancel button."""
        kb_empty = get_hustle_keyboard([])
        TestJobsKeyboardEmpirical._assert_all_callbacks_within_64_bytes(kb_empty)
        cbs = [btn.callback_data for row in kb_empty.inline_keyboard for btn in row]
        assert "jobs:menu" in cbs

    def test_router_attached_to_dispatcher(self):
        """jobs_router must be registered as a sub-router in create_dispatcher()."""
        from app.bot.bot import create_dispatcher
        from app.bot.routers.jobs import jobs_router
        dp = create_dispatcher()
        assert jobs_router in dp.sub_routers, "jobs_router is missing from create_dispatcher() sub_routers!"

