"""Empirical Challenger 2 Test Suite: Telegram UI & Security Challenge.

Adversarial stress-testing of:
1. Telegram 64-byte callback limit across all gambling and jobs inline keyboards.
2. HTML injection / XSS resistance across all gambling and jobs formatters.
3. Reply keyboard dual compatibility (CompatibleZarabotokButtonText).
4. Permanent absence of Oleg virtual assistant.
"""

import datetime
import html
import os
import re
import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiogram import F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.bot import create_dispatcher
from app.bot.formatters.gambling import (
    format_bet_placed,
    format_bookmaker_menu,
    format_duel_result,
    format_duels_lobby,
    format_match_detail,
    format_match_simulation,
    format_roulette_solo_round,
)
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
from app.bot.keyboards.gambling import (
    get_bet_amount_keyboard,
    get_bookmaker_menu_keyboard,
    get_duel_detail_keyboard,
    get_duel_stake_preset_keyboard,
    get_duels_menu_keyboard,
    get_match_detail_keyboard,
    get_roulette_solo_keyboard,
    get_roulette_stake_preset_keyboard,
)
from app.bot.keyboards.jobs import (
    get_branch_detail_keyboard,
    get_hustle_keyboard,
    get_job_market_keyboard,
    get_jobs_menu_keyboard,
)
from app.bot.keyboards.reply import (
    CompatibleZarabotokButtonText,
    get_cancel_reply_keyboard,
    get_main_menu_keyboard,
)
from app.database.models import User, UserJob
from app.services.jobs import HUSTLE_CATALOG, JobBranch, JobService


def assert_all_callbacks_within_64_bytes(
    keyboard: InlineKeyboardMarkup, context: str = ""
) -> int:
    """Assert all inline buttons have callback_data <= 64 bytes UTF-8.

    Returns the maximum byte length observed in this keyboard.
    """
    assert isinstance(keyboard, InlineKeyboardMarkup)
    assert len(keyboard.inline_keyboard) > 0, f"Keyboard is empty in context: {context}"
    max_len = 0
    for row_idx, row in enumerate(keyboard.inline_keyboard):
        for btn_idx, btn in enumerate(row):
            assert btn.callback_data is not None, (
                f"Missing callback_data at row {row_idx}, col {btn_idx} in {context}"
            )
            raw_bytes = btn.callback_data.encode("utf-8")
            byte_len = len(raw_bytes)
            max_len = max(max_len, byte_len)
            assert byte_len <= 64, (
                f"Callback data EXCEEDS 64 BYTES ({byte_len} bytes) at row {row_idx}, col {btn_idx} "
                f"in {context}: '{btn.callback_data}'"
            )
            assert len(btn.text) > 0, f"Empty button text at row {row_idx}, col {btn_idx} in {context}"
    return max_len


class TestTelegram64ByteCallbackLimit:
    """Empirical verification that all inline callbacks satisfy Telegram's strict 64-byte limit."""

    @pytest.mark.parametrize("filter_name", ["ALL", "FOOTBALL", "ESPORTS", "CUSTOM_FILTER"])
    def test_bookmaker_menu_keyboard_callbacks(self, filter_name: str):
        # Test with empty matches
        kb_empty = get_bookmaker_menu_keyboard([], current_filter=filter_name)
        max_empty = assert_all_callbacks_within_64_bytes(kb_empty, f"bookmaker_menu empty ({filter_name})")
        assert max_empty <= 64

        # Test with mock matches with 36-char RFC4122 UUIDs
        mock_matches = []
        for i in range(10):
            m = MagicMock()
            m.id = str(uuid.uuid4())
            m.sport = "FOOTBALL" if i % 2 == 0 else "ESPORTS"
            m.team_home = f"Команда {i} " + "А" * 20
            m.team_away = f"Соперник {i} " + "Б" * 20
            mock_matches.append(m)

        kb_matches = get_bookmaker_menu_keyboard(mock_matches, current_filter=filter_name)
        max_matches = assert_all_callbacks_within_64_bytes(
            kb_matches, f"bookmaker_menu with matches ({filter_name})"
        )
        assert max_matches <= 64

    @pytest.mark.parametrize("status", ["SCHEDULED", "LIVE", "FINISHED", "CANCELLED"])
    def test_match_detail_keyboard_callbacks(self, status: str):
        mock_match = MagicMock()
        mock_match.id = str(uuid.uuid4())
        mock_match.status = status
        mock_match.odds_home = 2.15
        mock_match.odds_draw = 3.20
        mock_match.odds_away = 3.45
        mock_match.odds_over = 1.90
        mock_match.odds_under = 1.85

        kb = get_match_detail_keyboard(mock_match, has_active_bets=True)
        max_len = assert_all_callbacks_within_64_bytes(kb, f"match_detail ({status})")
        assert max_len <= 64

    @pytest.mark.parametrize("outcome", ["H", "D", "A", "O", "U"])
    def test_bet_amount_keyboard_callbacks(self, outcome: str):
        test_uuid = str(uuid.uuid4())
        kb = get_bet_amount_keyboard(test_uuid, outcome)
        max_len = assert_all_callbacks_within_64_bytes(kb, f"bet_amount ({outcome})")
        assert max_len <= 64

    @pytest.mark.parametrize("game_filter", ["ALL", "DICE", "COINFLIP", "ROULETTE"])
    def test_duels_menu_keyboard_callbacks(self, game_filter: str):
        mock_duels = []
        for gt in ["DICE", "COINFLIP", "ROULETTE", "DICE", "COINFLIP", "ROULETTE"]:
            d = MagicMock()
            d.id = str(uuid.uuid4())
            d.game_type = gt
            d.stake = Decimal("15000000.00")
            mock_duels.append(d)

        kb = get_duels_menu_keyboard(mock_duels, current_filter=game_filter)
        max_len = assert_all_callbacks_within_64_bytes(kb, f"duels_menu ({game_filter})")
        assert max_len <= 64

    @pytest.mark.parametrize("game_type", ["DICE", "COINFLIP", "ROULETTE"])
    def test_duel_stake_preset_keyboard_callbacks(self, game_type: str):
        kb = get_duel_stake_preset_keyboard(game_type)
        max_len = assert_all_callbacks_within_64_bytes(kb, f"duel_stake_preset ({game_type})")
        assert max_len <= 64

    @pytest.mark.parametrize("is_creator", [True, False])
    @pytest.mark.parametrize("status", ["OPEN", "FINISHED"])
    def test_duel_detail_keyboard_callbacks(self, is_creator: bool, status: str):
        mock_duel = MagicMock()
        mock_duel.id = str(uuid.uuid4())
        mock_duel.status = status
        mock_duel.stake = Decimal("5000000.00")

        kb = get_duel_detail_keyboard(mock_duel, is_creator=is_creator)
        max_len = assert_all_callbacks_within_64_bytes(
            kb, f"duel_detail (creator={is_creator}, status={status})"
        )
        assert max_len <= 64

    @pytest.mark.parametrize("round_num", [1, 2, 3, 4, 5])
    @pytest.mark.parametrize("can_cashout", [True, False])
    def test_roulette_solo_keyboard_callbacks(self, round_num: int, can_cashout: bool):
        kb = get_roulette_solo_keyboard(round_num, can_cashout=can_cashout)
        max_len = assert_all_callbacks_within_64_bytes(
            kb, f"roulette_solo (round={round_num}, cashout={can_cashout})"
        )
        assert max_len <= 64

    def test_roulette_stake_preset_keyboard_callbacks(self):
        kb = get_roulette_stake_preset_keyboard()
        max_len = assert_all_callbacks_within_64_bytes(kb, "roulette_stake_preset")
        assert max_len <= 64

    def test_jobs_menu_keyboard_callbacks(self):
        now = datetime.datetime.now()
        kb_empty = get_jobs_menu_keyboard([], now=now)
        assert assert_all_callbacks_within_64_bytes(kb_empty, "jobs_menu empty") <= 64

        job1 = UserJob(
            id=1,
            user_id=10,
            branch=JobBranch.IT,
            level=2,
            shifts_completed=10,
            is_active=True,
            is_on_shift=False,
        )
        job2 = UserJob(
            id=2,
            user_id=10,
            branch=JobBranch.SERVICE,
            level=3,
            shifts_completed=20,
            is_active=True,
            is_on_shift=True,
            shift_started_at=now - datetime.timedelta(hours=3),
        )
        kb_active = get_jobs_menu_keyboard([job1, job2], now=now)
        max_active = assert_all_callbacks_within_64_bytes(kb_active, "jobs_menu active")
        assert max_active <= 64

    def test_job_market_and_branch_detail_keyboards_callbacks(self):
        kb_market = get_job_market_keyboard([JobBranch.IT])
        assert assert_all_callbacks_within_64_bytes(kb_market, "job_market") <= 64

        for b in [JobBranch.IT, JobBranch.SERVICE, JobBranch.FINANCE, JobBranch.SHADOW]:
            for is_emp in [True, False]:
                for count in [0, 1, 2]:
                    kb_b = get_branch_detail_keyboard(b, is_employed=is_emp, active_count=count)
                    assert assert_all_callbacks_within_64_bytes(
                        kb_b, f"branch_detail ({b}, employed={is_emp}, count={count})"
                    ) <= 64

    def test_hustle_keyboard_callbacks(self):
        for quest in HUSTLE_CATALOG:
            kb = get_hustle_keyboard(quest["options"], hustle_id=quest["id"])
            max_len = assert_all_callbacks_within_64_bytes(kb, f"hustle ({quest['id']})")
            assert max_len <= 64

        adv_id = str(uuid.uuid4())
        adv_opts = [{"text": "Вариант А" * 10}, {"text": "Вариант Б" * 10}]
        kb_adv = get_hustle_keyboard(adv_opts, hustle_id=adv_id)
        assert assert_all_callbacks_within_64_bytes(kb_adv, "adversarial hustle") <= 64


class TestHTMLInjectionAndXSSResistance:
    """Empirical verification that all formatters properly escape malicious HTML/XSS payloads."""

    MALICIOUS_PAYLOADS = [
        "<script>alert(1)</script>",
        '<b onmouseover="alert(\'xss\')">Взлом</b>',
        '<img src="x" onerror="alert(1)">',
        '<a href="javascript:alert(1)">Click Me</a>',
        "' OR '1'='1\" -- <svg/onload=alert(1)>",
        "<b>Жирный текст</b> & \"кавычки\" & 'апостроф' & <script>alert('xss')</script>",
        "⚽️ ФК «Спартак» <style>body{display:none;}</style>",
    ]

    def _assert_no_raw_tags(self, output: str, context: str):
        """Assert that no unescaped dangerous tags (starting with literal '<') exist in rendered output."""
        dangerous_patterns = [
            r"<script\b",
            r"<b\s+on",
            r"<img\b",
            r"<svg\b",
            r"<style\b",
            r"<a\b[^>]*href\s*=\s*['\"]?javascript:",
        ]
        for pat in dangerous_patterns:
            match = re.search(pat, output, re.IGNORECASE)
            assert match is None, (
                f"Raw unescaped dangerous tag found matching '{pat}' in {context}!\n"
                f"Output snippet:\n{output[:500]}"
            )

    def _assert_escaped_present(self, payload: str, rendered: str):
        """Assert payload is present in either quote=False or quote=True escaped form."""
        escaped_nofalse = html.escape(payload, quote=False)
        escaped_notrue = html.escape(payload, quote=True)
        assert (escaped_nofalse in rendered) or (escaped_notrue in rendered), (
            f"Escaped payload not found in rendered text! Payload: {payload}\nRendered: {rendered[:300]}"
        )

    def test_format_bookmaker_menu_xss_resistance(self):
        for payload in self.MALICIOUS_PAYLOADS:
            m = MagicMock()
            m.sport = "FOOTBALL"
            m.team_home = f"Команда {payload}"
            m.team_away = f"Соперник {payload}"
            m.odds_home = 1.95
            m.odds_away = 1.85

            rendered = format_bookmaker_menu([m], total_bets=3)
            self._assert_no_raw_tags(rendered, "format_bookmaker_menu")
            self._assert_escaped_present(payload, rendered)

    def test_format_match_detail_xss_resistance(self):
        for payload in self.MALICIOUS_PAYLOADS:
            m = MagicMock()
            m.sport = "ESPORTS"
            m.team_home = f"Home {payload}"
            m.team_away = f"Away {payload}"
            m.status = "LIVE"
            m.score_home = 2
            m.score_away = 1
            m.home_rating = 90.0
            m.away_rating = 88.0
            m.odds_home = 1.75
            m.odds_draw = 3.40
            m.odds_away = 2.10
            m.odds_over = 1.80
            m.odds_under = 1.95

            mock_bet = MagicMock()
            mock_bet.bet_type = "HOME"
            mock_bet.amount = Decimal("50000.00")
            mock_bet.odds = Decimal("1.75")
            mock_bet.status = "PENDING"

            rendered = format_match_detail(m, user_bets=[mock_bet])
            self._assert_no_raw_tags(rendered, "format_match_detail")
            self._assert_escaped_present(payload, rendered)

    def test_format_match_simulation_xss_resistance(self):
        for payload in self.MALICIOUS_PAYLOADS:
            m = MagicMock()
            m.sport = "FOOTBALL"
            m.team_home = f"Home {payload}"
            m.team_away = f"Away {payload}"
            m.score_home = 3
            m.score_away = 2

            events = [
                {"minute": 15, "description": f"Гол забил {payload}!", "type": "GOAL"},
                {"minute": 44, "description": f"Желтая карточка {payload}", "type": "YELLOW_CARD"},
            ]

            mock_bet = MagicMock()
            mock_bet.bet_type = "HOME"
            mock_bet.amount = Decimal("100000.00")
            mock_bet.status = "WON"
            mock_bet.potential_payout = Decimal("195000.00")

            rendered = format_match_simulation(m, events, user_bets=[mock_bet])
            self._assert_no_raw_tags(rendered, "format_match_simulation")
            self._assert_escaped_present(payload, rendered)

    def test_format_bet_placed_xss_resistance(self):
        for payload in self.MALICIOUS_PAYLOADS:
            m = MagicMock()
            m.team_home = f"Home {payload}"
            m.team_away = f"Away {payload}"

            b = MagicMock()
            b.id = str(uuid.uuid4())
            b.bet_type = "HOME"
            b.amount = Decimal("1000000.00")
            b.odds = Decimal("2.10")
            b.potential_payout = Decimal("2100000.00")

            rendered = format_bet_placed(b, m, new_balance=Decimal("5000000.00"))
            self._assert_no_raw_tags(rendered, "format_bet_placed")
            self._assert_escaped_present(payload, rendered)

    def test_format_duel_result_xss_resistance(self):
        for payload in self.MALICIOUS_PAYLOADS:
            d = MagicMock()
            d.game_type = "DICE"
            d.stake = Decimal("1000000.00")

            c = MagicMock()
            c.id = 101
            c.full_name = f"Создатель {payload}"
            c.username = f"user_{payload}"

            o = MagicMock()
            o.id = 202
            o.full_name = f"Соперник {payload}"
            o.username = f"opp_{payload}"

            w = c

            rendered = format_duel_result(
                duel=d,
                creator=c,
                opponent=o,
                winner=w,
                roll_c=6,
                roll_o=3,
                payout=Decimal("1940000.00"),
                commission=Decimal("60000.00"),
            )
            self._assert_no_raw_tags(rendered, "format_duel_result")
            self._assert_escaped_present(payload, rendered)

    def test_format_career_dashboard_xss_resistance(self):
        for payload in self.MALICIOUS_PAYLOADS:
            u = User(id=1, telegram_id=12345, full_name=f"Иван {payload}", username=f"ivan_{payload}")
            job = UserJob(
                id=1,
                user_id=1,
                branch=JobBranch.IT,
                level=2,
                shifts_completed=5,
                is_active=True,
                is_on_shift=False,
            )

            rendered = format_career_dashboard(u, [job])
            self._assert_no_raw_tags(rendered, "format_career_dashboard")
            self._assert_escaped_present(payload, rendered)

    def test_format_hustle_quest_and_result_xss_resistance(self):
        for payload in self.MALICIOUS_PAYLOADS:
            quest = {
                "id": "hustle_xss_test",
                "branch": JobBranch.IT,
                "title": f"Квест {payload}",
                "text": f"Описание ситуации {payload}",
                "options": [
                    {
                        "text": f"Выбор 1 {payload}",
                        "success_rate": 0.6,
                        "reward_min": 1000,
                        "reward_max": 2000,
                        "penalty_min": 500,
                        "penalty_max": 1000,
                    }
                ],
            }

            rendered_q = format_hustle_quest(quest)
            self._assert_no_raw_tags(rendered_q, "format_hustle_quest")
            self._assert_escaped_present(payload, rendered_q)

            rendered_r = format_hustle_result(
                quest=quest,
                choice_idx=0,
                delta=Decimal("1500.00"),
                outcome_msg=f"Успешный исход {payload}",
                new_balance=Decimal("25000.00"),
            )
            self._assert_no_raw_tags(rendered_r, "format_hustle_result")
            self._assert_escaped_present(payload, rendered_r)

    def test_format_shift_claimed_xss_resistance(self):
        for payload in self.MALICIOUS_PAYLOADS:
            job = UserJob(
                id=1,
                user_id=1,
                branch=JobBranch.IT,
                level=1,
                shifts_completed=3,
                is_active=True,
                is_on_shift=False,
            )
            rendered = format_shift_claimed(
                job=job,
                event_name=f"Премия {payload}",
                salary_credited=Decimal("5000.00"),
                new_balance=Decimal("10000.00"),
            )
            self._assert_no_raw_tags(rendered, "format_shift_claimed")
            self._assert_escaped_present(payload, rendered)


class TestReplyKeyboardDualCompatibility:
    """Empirical verification of CompatibleZarabotokButtonText dual compatibility."""

    def test_compatible_zarabotok_text_equality_invariants(self):
        z = CompatibleZarabotokButtonText("💼 Заработок")

        # 1. String subclass invariants
        assert isinstance(z, str)
        assert str(z) == "💼 Заработок"

        # 2. Equality with legacy string
        assert z == "💼 Работа и бизнес"
        assert "💼 Работа и бизнес" == z

        # 3. Equality with new string
        assert z == "💼 Заработок"
        assert "💼 Заработок" == z

        # 4. Inequality with other strings
        assert z != "💳 Моя карта"
        assert "💳 Моя карта" != z
        assert z != "Заработок"

        # 5. Hashing consistency in dict and set
        s = {z}
        assert z in s
        assert "💼 Заработок" in s
        d = {z: 42}
        assert d[z] == 42
        assert d["💼 Заработок"] == 42

    def test_reply_keyboard_contains_both_buttons_empirically(self):
        kb = get_main_menu_keyboard()
        button_texts = [btn.text for row in kb.keyboard for btn in row]

        # Both legacy and new text MUST evaluate True in the button list
        assert "💼 Работа и бизнес" in button_texts, "Legacy '💼 Работа и бизнес' must be in button_texts"
        assert "💼 Заработок" in button_texts, "New '💼 Заработок' must be in button_texts"

        # Verify exact position: Row 3, Col 0
        zarabotok_btn = kb.keyboard[2][0]
        assert zarabotok_btn.text == "💼 Заработок"
        assert zarabotok_btn.text == "💼 Работа и бизнес"

    def test_aiogram_filter_compatibility(self):
        """Verify aiogram MagicFilter behavior for both strings."""
        filter_legacy = F.text == "💼 Работа и бизнес"
        filter_new = F.text == "💼 Заработок"

        msg_new = MagicMock()
        msg_new.text = "💼 Заработок"

        msg_legacy = MagicMock()
        msg_legacy.text = "💼 Работа и бизнес"

        msg_compat = MagicMock()
        msg_compat.text = CompatibleZarabotokButtonText("💼 Заработок")

        assert filter_new.resolve(msg_new) is True
        assert filter_legacy.resolve(msg_legacy) is True

        assert filter_legacy.resolve(msg_compat) is True
        assert filter_new.resolve(msg_compat) is True


class TestOlegAbsenceVerification:
    """Empirical verification that Oleg virtual assistant is 100% absent across the entire repository."""

    def test_no_oleg_in_app_source_files(self):
        app_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "app")
        )
        assert os.path.isdir(app_dir), f"App dir {app_dir} not found"

        violations = []
        for root, _, files in os.walk(app_dir):
            for fname in files:
                if fname.endswith(".py"):
                    fpath = os.path.join(root, fname)
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        for line_idx, line in enumerate(f, 1):
                            if "oleg" in line.lower() or "олег" in line.lower():
                                violations.append(f"{fpath}:{line_idx}: {line.strip()}")

        assert not violations, f"Found Oleg occurrences in app source code:\n" + "\n".join(violations)

    def test_no_oleg_router_in_dispatcher(self):
        dp = create_dispatcher()
        sub_router_names = [r.name for r in dp.sub_routers]

        assert "oleg" not in sub_router_names
        assert "oleg_router" not in sub_router_names
        assert not any("oleg" in str(r.name).lower() for r in dp.sub_routers)
        assert not any("олег" in str(r.name).lower() for r in dp.sub_routers)

    def test_no_oleg_buttons_in_any_keyboard(self):
        # 1. Main menu
        main_kb = get_main_menu_keyboard()
        main_buttons = [btn.text for row in main_kb.keyboard for btn in row]
        assert not any("олег" in str(b).lower() for b in main_buttons)

        # 2. Cancel keyboard
        cancel_kb = get_cancel_reply_keyboard()
        cancel_buttons = [btn.text for row in cancel_kb.keyboard for btn in row]
        assert not any("олег" in str(b).lower() for b in cancel_buttons)

        # 3. Gambling keyboards
        bk_kb = get_bookmaker_menu_keyboard([])
        bk_btns = [btn.text for row in bk_kb.inline_keyboard for btn in row]
        assert not any("олег" in str(b).lower() for b in bk_btns)

        duels_kb = get_duels_menu_keyboard([])
        duels_btns = [btn.text for row in duels_kb.inline_keyboard for btn in row]
        assert not any("олег" in str(b).lower() for b in duels_btns)

        # 4. Jobs keyboards
        jobs_kb = get_jobs_menu_keyboard([])
        jobs_btns = [btn.text for row in jobs_kb.inline_keyboard for btn in row]
        assert not any("олег" in str(b).lower() for b in jobs_btns)
