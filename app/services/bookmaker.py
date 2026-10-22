"""Sports Bookmaker Domain Service: Authentic Teams Catalog, Poisson / xG Simulation, Odds Engine with 5% Margin, Bet Placement & Atomic Settlements."""

import datetime
import json
import math
import random
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Account, LedgerEntry, SportsBet, SportsMatch, Transaction
from app.services.banking import BankingService
from app.services.receipts import ReceiptService
from app.services.transfers import AccountFrozenError, InsufficientFundsError


class BookmakerService:
    """Core domain service for sports betting, realistic match simulations, and atomic settlements."""

    MIN_BET: Decimal = Decimal("100.00")
    MAX_BET: Decimal = Decimal("20000000.00")
    BOOKMAKER_MARGIN: float = 0.05
    GAMBLING_TAX_RATE: Decimal = Decimal("0.13")  # 13% НДФЛ на чистый выигрыш в БК

    FOOTBALL_TEAMS: list[dict[str, Any]] = [
        {
            "name": "Реал Мадрид",
            "rating": 92.5,
            "league": "Champions League",
            "icon": "👑",
            "roster": [
                "Килиан Мбаппе",
                "Винисиус Жуниор",
                "Джуд Беллингем",
                "Лука Модрич",
                "Родриго",
                "Федерико Вальверде",
                "Тибо Куртуа",
            ],
            "goalkeeper": "Тибо Куртуа",
        },
        {
            "name": "Манчестер Сити",
            "rating": 93.0,
            "league": "Champions League",
            "icon": "🩵",
            "roster": [
                "Эрлинг Холанд",
                "Кевин Де Брёйне",
                "Фил Фоден",
                "Родри",
                "Бернарду Силва",
                "Илкай Гюндоган",
                "Эдерсон",
            ],
            "goalkeeper": "Эдерсон",
        },
        {
            "name": "Бавария",
            "rating": 91.0,
            "league": "Champions League",
            "icon": "🔴",
            "roster": [
                "Гарри Кейн",
                "Джамал Мусиала",
                "Лерой Сане",
                "Йозуа Киммих",
                "Серж Гнабри",
                "Томас Мюллер",
                "Мануэль Нойер",
            ],
            "goalkeeper": "Мануэль Нойер",
        },
        {
            "name": "Барселона",
            "rating": 89.5,
            "league": "Champions League",
            "icon": "🔵🔴",
            "roster": [
                "Роберт Левандовски",
                "Ламин Ямаль",
                "Рафинья",
                "Педри",
                "Гави",
                "Френки де Йонг",
                "Марк-Андре тер Штеген",
            ],
            "goalkeeper": "Марк-Андре тер Штеген",
        },
        {
            "name": "Ливерпуль",
            "rating": 90.0,
            "league": "Champions League",
            "icon": "🔴",
            "roster": [
                "Мохамед Салах",
                "Дарвин Нуньес",
                "Луис Диас",
                "Вирджил ван Дейк",
                "Доминик Собослаи",
                "Алексис Мак Аллистер",
                "Алиссон",
            ],
            "goalkeeper": "Алиссон",
        },
        {
            "name": "ПСЖ",
            "rating": 88.0,
            "league": "Champions League",
            "icon": "🗼",
            "roster": [
                "Усман Дембеле",
                "Брэдли Барколя",
                "Марко Асенсио",
                "Витинья",
                "Рандаль Коло Муани",
                "Уоррен Заир-Эмери",
                "Джанлуиджи Доннарумма",
            ],
            "goalkeeper": "Джанлуиджи Доннарумма",
        },
        {
            "name": "Зенит",
            "rating": 83.0,
            "league": "RPL",
            "icon": "🦁",
            "roster": [
                "Матео Кассьерра",
                "Клаудиньо",
                "Вендел",
                "Максим Глушенков",
                "Андрей Мостовой",
                "Вильмар Барриос",
                "Евгений Латышонок",
            ],
            "goalkeeper": "Евгений Латышонок",
        },
        {
            "name": "Краснодар",
            "rating": 81.5,
            "league": "RPL",
            "icon": "🐂",
            "roster": [
                "Джон Кордоба",
                "Эдуард Сперцян",
                "Жуан Батчи",
                "Федор Смолов",
                "Никита Кривцов",
                "Диего Тормена",
                "Станислав Агкацев",
            ],
            "goalkeeper": "Станислав Агкацев",
        },
        {
            "name": "Спартак",
            "rating": 80.5,
            "league": "RPL",
            "icon": "⚪️🔴",
            "roster": [
                "Манфред Угальде",
                "Эсекьель Барко",
                "Тео Бонгонда",
                "Маркиньос",
                "Роман Зобнин",
                "Срджан Бабич",
                "Александр Максименко",
            ],
            "goalkeeper": "Александр Максименко",
        },
        {
            "name": "Динамо",
            "rating": 79.5,
            "league": "RPL",
            "icon": "⚪️🔵",
            "roster": [
                "Константин Тюкавин",
                "Муми Нгамале",
                "Бителло",
                "Хорхе Карраскаль",
                "Даниил Фомин",
                "Фабиан Бальбуэна",
                "Андрей Лунев",
            ],
            "goalkeeper": "Андрей Лунев",
        },
        {
            "name": "ЦСКА",
            "rating": 79.0,
            "league": "RPL",
            "icon": "🔴🔵",
            "roster": [
                "Тамерлан Мусаев",
                "Иван Обляков",
                "Аббосбек Файзуллаев",
                "Секу Койта",
                "Саша Зделар",
                "Виллиан Роша",
                "Игорь Акинфеев",
            ],
            "goalkeeper": "Игорь Акинфеев",
        },
        {
            "name": "Локомотив",
            "rating": 78.0,
            "league": "RPL",
            "icon": "🚂",
            "roster": [
                "Алексей Батраков",
                "Дмитрий Воробьев",
                "Сергей Пиняев",
                "Илья Самошников",
                "Артем Карпукас",
                "Дмитрий Баринов",
                "Илья Лантратов",
            ],
            "goalkeeper": "Илья Лантратов",
        },
    ]

    ESPORTS_TEAMS: list[dict[str, Any]] = [
        {
            "name": "Team Spirit",
            "rating": 93.0,
            "league": "CS2 / Dota 2",
            "icon": "🐉",
            "roster": [
                "donk",
                "sh1ro",
                "chopper",
                "magixx",
                "zont1x",
                "Yatoro",
                "Collapse",
            ],
            "goalkeeper": "chopper",
        },
        {
            "name": "NAVI",
            "rating": 92.0,
            "league": "CS2",
            "icon": "💛🖤",
            "roster": [
                "s1mple",
                "b1t",
                "jL",
                "Aleksib",
                "iM",
                "w0nderful",
            ],
            "goalkeeper": "Aleksib",
        },
        {
            "name": "Team Vitality",
            "rating": 91.0,
            "league": "CS2",
            "icon": "🐝",
            "roster": [
                "ZywOo",
                "apEX",
                "Spinx",
                "flameZ",
                "mezii",
            ],
            "goalkeeper": "apEX",
        },
        {
            "name": "FaZe Clan",
            "rating": 90.0,
            "league": "CS2",
            "icon": "🔴",
            "roster": [
                "karrigan",
                "ropz",
                "broky",
                "frozen",
                "rain",
            ],
            "goalkeeper": "karrigan",
        },
        {
            "name": "G2 Esports",
            "rating": 89.0,
            "league": "CS2",
            "icon": "⚔️",
            "roster": [
                "m0NESY",
                "NiKo",
                "malbsMd",
                "huNter-",
                "Snax",
            ],
            "goalkeeper": "Snax",
        },
        {
            "name": "Virtus.pro",
            "rating": 85.0,
            "league": "CS2 / Dota 2",
            "icon": "🐻",
            "roster": [
                "Jame",
                "FL1T",
                "electroNic",
                "fame",
                "n0rb3r7",
            ],
            "goalkeeper": "Jame",
        },
    ]

    @classmethod
    def get_football_teams(cls) -> list[dict[str, Any]]:
        """Return full catalog of 12 real football clubs."""
        return list(cls.FOOTBALL_TEAMS)

    @classmethod
    def get_esports_teams(cls) -> list[dict[str, Any]]:
        """Return full catalog of 6 elite esports rosters."""
        return list(cls.ESPORTS_TEAMS)

    @classmethod
    def get_team(cls, name: str) -> dict[str, Any] | None:
        """Find team info across football and esports catalogs by name."""
        for team in cls.FOOTBALL_TEAMS + cls.ESPORTS_TEAMS:
            if team["name"].strip().lower() == name.strip().lower():
                return team
        return None

    @classmethod
    def get_team_roster(cls, team_name: str, sport: str = "FOOTBALL") -> list[str]:
        """Fetch active player roster for a team, or provide fallback players."""
        team = cls.get_team(team_name)
        if team and team.get("roster"):
            return list(team["roster"])
        sport_tag = "Футболист" if sport.upper() == "FOOTBALL" else "Киберспортсмен"
        return [f"{sport_tag} #{i} ({team_name})" for i in range(1, 8)]

    @classmethod
    def calculate_odds(
        cls, home_rating: float, away_rating: float, sport: str = "FOOTBALL"
    ) -> dict[str, Decimal]:
        """Calculate decimal odds for 1X2 and Total 2.5 goals using Poisson distribution with a 5% bookmaker margin."""
        is_football = sport.upper() == "FOOTBALL"
        if is_football:
            home_adv = 2.5
            delta_home = (home_rating + home_adv) - away_rating
            delta_away = away_rating - (home_rating + home_adv)
            base_lambda = 1.35
            lambda_home = max(0.40, min(4.50, base_lambda * math.exp(delta_home / 30.0)))
            lambda_away = max(0.40, min(4.50, base_lambda * math.exp(delta_away / 30.0)))
        else:
            delta_home = home_rating - away_rating
            delta_away = away_rating - home_rating
            base_lambda = 1.40
            lambda_home = max(0.40, min(4.50, base_lambda * math.exp(delta_home / 30.0)))
            lambda_away = max(0.40, min(4.50, base_lambda * math.exp(delta_away / 30.0)))

        # Precompute Poisson PMF over scores 0..10
        p_home = [math.exp(-lambda_home) * (lambda_home ** k) / math.factorial(k) for k in range(11)]
        p_away = [math.exp(-lambda_away) * (lambda_away ** m) / math.factorial(m) for m in range(11)]

        prob_h = 0.0
        prob_d = 0.0
        prob_a = 0.0
        prob_over = 0.0
        prob_under = 0.0

        for k in range(11):
            for m in range(11):
                joint = p_home[k] * p_away[m]
                if k > m:
                    prob_h += joint
                elif k == m:
                    prob_d += joint
                else:
                    prob_a += joint

                if (k + m) >= 3:
                    prob_over += joint
                else:
                    prob_under += joint

        # Total bookmaker target margin: sum(1 / Odds_i) = 1.05
        target_margin = 1.0 + cls.BOOKMAKER_MARGIN

        # Scale 1X2 market
        s_1x2 = max(0.001, prob_h + prob_d + prob_a)
        scale_1x2 = target_margin / s_1x2
        p_prime_h = max(0.0001, prob_h * scale_1x2)
        p_prime_d = max(0.0001, prob_d * scale_1x2)
        p_prime_a = max(0.0001, prob_a * scale_1x2)

        odds_h = max(Decimal("1.05"), Decimal(f"{1.0 / p_prime_h:.2f}"))
        odds_d = max(Decimal("1.05"), Decimal(f"{1.0 / p_prime_d:.2f}"))
        odds_a = max(Decimal("1.05"), Decimal(f"{1.0 / p_prime_a:.2f}"))

        # Scale Total 2.5 market (Over / Under)
        s_ou = max(0.001, prob_over + prob_under)
        scale_ou = target_margin / s_ou
        p_prime_over = max(0.0001, prob_over * scale_ou)
        p_prime_under = max(0.0001, prob_under * scale_ou)

        odds_over = max(Decimal("1.05"), Decimal(f"{1.0 / p_prime_over:.2f}"))
        odds_under = max(Decimal("1.05"), Decimal(f"{1.0 / p_prime_under:.2f}"))

        return {
            "HOME": odds_h,
            "DRAW": odds_d,
            "AWAY": odds_a,
            "OVER_2_5": odds_over,
            "UNDER_2_5": odds_under,
            "odds_home": odds_h,
            "odds_draw": odds_d,
            "odds_away": odds_a,
            "odds_over": odds_over,
            "odds_under": odds_under,
        }

    @classmethod
    def simulate_match_events(
        cls,
        team_home: str,
        team_away: str,
        home_rating: float,
        away_rating: float,
        sport: str = "FOOTBALL",
        seed: int | None = None,
    ) -> tuple[int, int, list[dict[str, Any]]]:
        """Generate final scores and a 90-minute chronological event timeline with player rosters."""
        rng = random.Random(seed) if seed is not None else random.Random()
        is_football = sport.upper() == "FOOTBALL"

        if is_football:
            home_adv = 2.5
            delta_home = (home_rating + home_adv) - away_rating
            delta_away = away_rating - (home_rating + home_adv)
            base_lambda = 1.35
            lambda_home = max(0.40, min(4.50, base_lambda * math.exp(delta_home / 30.0)))
            lambda_away = max(0.40, min(4.50, base_lambda * math.exp(delta_away / 30.0)))
        else:
            delta_home = home_rating - away_rating
            delta_away = away_rating - home_rating
            base_lambda = 1.40
            lambda_home = max(0.40, min(4.50, base_lambda * math.exp(delta_home / 30.0)))
            lambda_away = max(0.40, min(4.50, base_lambda * math.exp(delta_away / 30.0)))

        def _sample_poisson(lam: float) -> int:
            l_val = math.exp(-lam)
            k = 0
            p = 1.0
            while p > l_val:
                k += 1
                p *= rng.random()
            return min(k - 1, 8)

        score_h_raw = _sample_poisson(lambda_home)
        score_a_raw = _sample_poisson(lambda_away)

        home_roster = cls.get_team_roster(team_home, sport)
        away_roster = cls.get_team_roster(team_away, sport)

        home_team_info = cls.get_team(team_home)
        away_team_info = cls.get_team(team_away)
        home_gk = home_team_info.get("goalkeeper", home_roster[-1]) if home_team_info else home_roster[-1]
        away_gk = away_team_info.get("goalkeeper", away_roster[-1]) if away_team_info else away_roster[-1]

        used_minutes: set[int] = set()

        def _get_unique_minute(low: int = 3, high: int = 89) -> int:
            for _ in range(50):
                m = rng.randint(low, high)
                if m not in used_minutes:
                    used_minutes.add(m)
                    return m
            m = rng.randint(low, high)
            used_minutes.add(m)
            return m

        raw_events: list[dict[str, Any]] = []

        # 1. Goal events (Home)
        for _ in range(score_h_raw):
            m = _get_unique_minute(4, 88)
            scorer = rng.choice(home_roster[:-1] if len(home_roster) > 1 else home_roster)
            raw_events.append({
                "minute": m,
                "type": "GOAL" if is_football else "ROUND_WIN",
                "team_side": "HOME",
                "team": team_home,
                "player": scorer,
            })

        # 2. Goal events (Away)
        for _ in range(score_a_raw):
            m = _get_unique_minute(4, 88)
            scorer = rng.choice(away_roster[:-1] if len(away_roster) > 1 else away_roster)
            raw_events.append({
                "minute": m,
                "type": "GOAL" if is_football else "ROUND_WIN",
                "team_side": "AWAY",
                "team": team_away,
                "player": scorer,
            })

        # 3. Saves / Clutches
        save_count = rng.randint(2, 4)
        for _ in range(save_count):
            m = _get_unique_minute(6, 88)
            if is_football:
                defending_team = rng.choice([team_home, team_away])
                gk = home_gk if defending_team == team_home else away_gk
                raw_events.append({
                    "minute": m,
                    "type": "SAVE",
                    "team": defending_team,
                    "player": gk,
                    "description": f"🧤 {m}' Сейв! {gk} ({defending_team}) вытаскивает сложнейший мяч!",
                })
            else:
                clutch_team = rng.choice([team_home, team_away])
                player = rng.choice(home_roster if clutch_team == team_home else away_roster)
                raw_events.append({
                    "minute": m,
                    "type": "CLUTCH",
                    "team": clutch_team,
                    "player": player,
                    "description": f"🔥 {m}' Клатч! {player} ({clutch_team}) выигрывает дуэль 1v2 и спасает раунд!",
                })

        # 4. Yellow Cards / Ace / Defuse
        card_count = rng.randint(1, 3)
        for _ in range(card_count):
            m = _get_unique_minute(12, 87)
            if is_football:
                card_team = rng.choice([team_home, team_away])
                player = rng.choice(home_roster if card_team == team_home else away_roster)
                raw_events.append({
                    "minute": m,
                    "type": "YELLOW_CARD",
                    "team": card_team,
                    "player": player,
                    "description": f"🟨 {m}' Предупреждение: {player} ({card_team}) получает желтую карточку за жесткий подкат.",
                })
            else:
                event_type = rng.choice(["ACE", "DEFUSE"])
                action_team = rng.choice([team_home, team_away])
                player = rng.choice(home_roster if action_team == team_home else away_roster)
                if event_type == "ACE":
                    desc = f"💥 {m}' ЭЙС! {player} ({action_team}) уничтожает всю команду соперника в соло!"
                else:
                    desc = f"💣 {m}' {player} ({action_team}) разминирует бомбу за секунду до детонации!"
                raw_events.append({
                    "minute": m,
                    "type": event_type,
                    "team": action_team,
                    "player": player,
                    "description": desc,
                })

        # 5. Occasional Red Card in football (15% chance)
        if is_football and rng.random() < 0.15:
            m = _get_unique_minute(42, 85)
            card_team = rng.choice([team_home, team_away])
            player = rng.choice(home_roster if card_team == team_home else away_roster)
            raw_events.append({
                "minute": m,
                "type": "RED_CARD",
                "team": card_team,
                "player": player,
                "description": f"🟥 {m}' Удаление! {player} ({card_team}) получает прямую красную карточку за опасную игру!",
            })

        # Sort chronologically and build running score
        sorted_events = sorted(raw_events, key=lambda x: x["minute"])
        curr_home = 0
        curr_away = 0
        final_events: list[dict[str, Any]] = []

        for ev in sorted_events:
            if ev["type"] in ("GOAL", "ROUND_WIN"):
                if ev["team_side"] == "HOME":
                    curr_home += 1
                else:
                    curr_away += 1
                ev["score_home"] = curr_home
                ev["score_away"] = curr_away
                if is_football:
                    ev["description"] = (
                        f"⚽️ {ev['minute']}' ГОЛ! {ev['player']} ({ev['team']}) отправляет мяч в сетку! "
                        f"({curr_home}:{curr_away})"
                    )
                else:
                    ev["description"] = (
                        f"🎯 {ev['minute']}' Раунд за {ev['team']}! {ev['player']} точным выстрелом закрывает раунд! "
                        f"({curr_home}:{curr_away})"
                    )
            final_events.append(ev)

        return (curr_home, curr_away, final_events)

    @classmethod
    async def create_match(
        cls,
        session: AsyncSession,
        sport: str,
        team_home: str,
        team_away: str,
        scheduled_at: datetime.datetime | None = None,
        home_rating: float | None = None,
        away_rating: float | None = None,
    ) -> SportsMatch:
        """Create and persist a new sports match with calculated odds."""
        sport_normalized = sport.upper()
        if sport_normalized not in ("FOOTBALL", "ESPORTS"):
            raise ValueError(f"Недопустимый вид спорта: {sport}. Допустимы: FOOTBALL, ESPORTS.")

        if home_rating is None:
            team_info = cls.get_team(team_home)
            home_rating = float(team_info["rating"]) if team_info else 85.0

        if away_rating is None:
            team_info = cls.get_team(team_away)
            away_rating = float(team_info["rating"]) if team_info else 85.0

        odds = cls.calculate_odds(home_rating, away_rating, sport=sport_normalized)

        match = SportsMatch(
            id=str(uuid.uuid4()),
            sport=sport_normalized,
            team_home=team_home,
            team_away=team_away,
            home_rating=float(home_rating),
            away_rating=float(away_rating),
            odds_home=odds["odds_home"],
            odds_draw=odds["odds_draw"],
            odds_away=odds["odds_away"],
            odds_over=odds["odds_over"],
            odds_under=odds["odds_under"],
            status="SCHEDULED",
            score_home=0,
            score_away=0,
            events_log="[]",
            scheduled_at=scheduled_at or datetime.datetime.now(),
        )
        session.add(match)
        await session.flush()
        return match

    @classmethod
    async def get_upcoming_matches(
        cls, session: AsyncSession, sport: str | None = None
    ) -> list[SportsMatch]:
        """Fetch all scheduled and live matches ordered by schedule time."""
        stmt = (
            select(SportsMatch)
            .where(SportsMatch.status.in_(["SCHEDULED", "LIVE"]))
            .order_by(SportsMatch.scheduled_at.asc())
        )
        if sport:
            stmt = stmt.where(SportsMatch.sport == sport.upper())

        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def get_match_by_id(
        cls, session: AsyncSession, match_id: str
    ) -> SportsMatch | None:
        """Retrieve match by primary key UUID."""
        stmt = select(SportsMatch).where(SportsMatch.id == match_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @classmethod
    async def get_user_bets(
        cls, session: AsyncSession, user_id: int
    ) -> list[SportsBet]:
        """Retrieve all sports bets placed by a specific user."""
        stmt = (
            select(SportsBet)
            .where(SportsBet.user_id == user_id)
            .order_by(SportsBet.created_at.desc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def place_bet(
        cls,
        session: AsyncSession,
        user_id: int,
        match_id: str,
        bet_type: str,
        amount: Decimal,
    ) -> SportsBet:
        """Place a wager on an upcoming match with atomic balance deduction and double-entry ledger."""
        # 1. Validate wager amount
        amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        if amount < cls.MIN_BET or amount > cls.MAX_BET:
            raise ValueError(
                f"Сумма ставки должна быть в пределах от {cls.MIN_BET:,.2f} ₽ до {cls.MAX_BET:,.2f} ₽."
            )

        # 2. Validate bet type
        bet_type_norm = bet_type.upper()
        valid_bet_types = ("HOME", "DRAW", "AWAY", "OVER_2_5", "UNDER_2_5")
        if bet_type_norm not in valid_bet_types:
            raise ValueError(
                f"Недопустимый исход ставки: «{bet_type}». Доступны: {', '.join(valid_bet_types)}."
            )

        # 3. Retrieve and validate match status
        match = await cls.get_match_by_id(session, match_id)
        if not match:
            raise ValueError(f"Матч с ID {match_id} не найден.")
        if match.status != "SCHEDULED":
            raise ValueError(
                f"Ставки принимаются только на запланированные матчи (текущий статус: {match.status})."
            )

        # 4. Resolve odds and calculate potential payout
        odds_mapping = {
            "HOME": match.odds_home,
            "DRAW": match.odds_draw,
            "AWAY": match.odds_away,
            "OVER_2_5": match.odds_over,
            "UNDER_2_5": match.odds_under,
        }
        odds = odds_mapping[bet_type_norm]
        potential_payout = (amount * odds).quantize(Decimal("0.01"))

        # 5. Fetch user's main card account
        main_acc = await BankingService.get_user_main_account(session, user_id)
        if not main_acc:
            raise ValueError(f"Основной банковский счет пользователя {user_id} не найден.")
        if main_acc.is_frozen:
            raise AccountFrozenError("Основной счет заморожен. Совершение ставок невозможно.")

        # 6. Atomic debit of balance guarded by balance >= amount
        debit_stmt = (
            update(Account)
            .where(Account.id == main_acc.id)
            .where(Account.balance >= amount)
            .where(Account.is_frozen == False)  # noqa: E712
            .values(balance=Account.balance - amount)
        )
        debit_res = await session.execute(debit_stmt)
        if debit_res.rowcount == 0:
            raise InsufficientFundsError("Недостаточно средств для совершения ставки.")

        # 7. Query authoritative new balance post-debit
        new_balance = (
            await session.execute(select(Account.balance).where(Account.id == main_acc.id))
        ).scalar_one()

        current_time = datetime.datetime.now()

        # 8. Record Transaction and double-entry LedgerEntry
        tx_id = f"bet-{uuid.uuid4().hex[:12]}"
        tx = Transaction(
            id=tx_id,
            sender_account_id=main_acc.id,
            recipient_account_id=None,
            amount=amount,
            tx_type="BET_PLACED",
            category="Ставки и азарт",
            description=(
                f"Ставка {amount:,.2f} ₽ на матч «{match.team_home} — {match.team_away}» "
                f"({bet_type_norm}) с коэффициентом {odds}"
            ),
            status="COMPLETED",
            fiscal_code=ReceiptService.generate_fiscal_code(),
            auth_code=ReceiptService.generate_auth_code(),
            created_at=current_time,
        )
        ledger_entry = LedgerEntry(
            transaction_id=tx_id,
            account_id=main_acc.id,
            direction="DEBIT",
            amount=amount,
            balance_after=new_balance,
            created_at=current_time,
        )
        session.add(tx)
        session.add(ledger_entry)

        # 9. Insert and return SportsBet
        bet = SportsBet(
            id=str(uuid.uuid4()),
            user_id=user_id,
            match_id=match_id,
            bet_type=bet_type_norm,
            odds=odds,
            amount=amount,
            potential_payout=potential_payout,
            status="PENDING",
            created_at=current_time,
        )
        session.add(bet)
        await session.flush()
        return bet

    @classmethod
    async def play_match(
        cls,
        session: AsyncSession,
        match_id: str,
        force_score_home: int | None = None,
        force_score_away: int | None = None,
        force_events: list[dict[str, Any]] | None = None,
    ) -> SportsMatch:
        """Simulate match gameplay, record chronological events timeline, and mark match FINISHED."""
        match = await cls.get_match_by_id(session, match_id)
        if not match:
            raise ValueError(f"Матч с ID {match_id} не найден.")

        if force_score_home is not None and force_score_away is not None:
            score_home = force_score_home
            score_away = force_score_away
            events = force_events or []
        else:
            score_home, score_away, events = cls.simulate_match_events(
                match.team_home,
                match.team_away,
                match.home_rating,
                match.away_rating,
                sport=match.sport,
            )

        match.score_home = score_home
        match.score_away = score_away
        match.events_log = json.dumps(events, ensure_ascii=False)
        match.status = "FINISHED"
        match.finished_at = datetime.datetime.now()

        await session.flush()
        return match

    @classmethod
    async def settle_match(
        cls, session: AsyncSession, match_id: str
    ) -> list[SportsBet]:
        """Settle all pending wagers for a match: credit winnings atomically or refund cancelled matches."""
        match = await cls.get_match_by_id(session, match_id)
        if not match:
            raise ValueError(f"Матч с ID {match_id} не найден.")

        # If match is not finished or cancelled yet, run match simulation first
        if match.status not in ("FINISHED", "CANCELLED"):
            match = await cls.play_match(session, match_id)

        stmt = (
            select(SportsBet)
            .where(SportsBet.match_id == match_id)
            .where(SportsBet.status == "PENDING")
        )
        res = await session.execute(stmt)
        pending_bets = list(res.scalars().all())

        current_time = datetime.datetime.now()

        for bet in pending_bets:
            user_acc = await BankingService.get_user_main_account(session, bet.user_id)

            if match.status == "CANCELLED":
                # Match cancelled: 100% refund of initial bet amount
                if user_acc:
                    await session.execute(
                        update(Account)
                        .where(Account.id == user_acc.id)
                        .values(balance=Account.balance + bet.amount)
                    )
                    new_bal = (
                        await session.execute(
                            select(Account.balance).where(Account.id == user_acc.id)
                        )
                    ).scalar_one()

                    tx_id = f"refund-{uuid.uuid4().hex[:12]}"
                    tx = Transaction(
                        id=tx_id,
                        sender_account_id=None,
                        recipient_account_id=user_acc.id,
                        amount=bet.amount,
                        tx_type="BET_REFUND",
                        category="Ставки и азарт",
                        description=(
                            f"Возврат ставки {bet.amount:,.2f} ₽: матч «{match.team_home} — {match.team_away}» отменен"
                        ),
                        status="COMPLETED",
                        fiscal_code=ReceiptService.generate_fiscal_code(),
                        auth_code=ReceiptService.generate_auth_code(),
                        created_at=current_time,
                    )
                    ledger_entry = LedgerEntry(
                        transaction_id=tx_id,
                        account_id=user_acc.id,
                        direction="CREDIT",
                        amount=bet.amount,
                        balance_after=new_bal,
                        created_at=current_time,
                    )
                    session.add(tx)
                    session.add(ledger_entry)

                bet.status = "REFUNDED"
            else:
                # Evaluate win criteria
                is_win = False
                total_goals = match.score_home + match.score_away

                if bet.bet_type == "HOME" and match.score_home > match.score_away:
                    is_win = True
                elif bet.bet_type == "DRAW" and match.score_home == match.score_away:
                    is_win = True
                elif bet.bet_type == "AWAY" and match.score_away > match.score_home:
                    is_win = True
                elif bet.bet_type == "OVER_2_5" and total_goals >= 3:
                    is_win = True
                elif bet.bet_type == "UNDER_2_5" and total_goals <= 2:
                    is_win = True

                if is_win:
                    gross_payout = bet.potential_payout
                    net_profit = max(Decimal("0.00"), gross_payout - bet.amount)
                    tax = (net_profit * cls.GAMBLING_TAX_RATE).quantize(Decimal("0.01"))
                    payout = gross_payout - tax

                    if user_acc:
                        await session.execute(
                            update(Account)
                            .where(Account.id == user_acc.id)
                            .values(balance=Account.balance + payout)
                        )
                        new_bal = (
                            await session.execute(
                                select(Account.balance).where(Account.id == user_acc.id)
                            )
                        ).scalar_one()

                        tx_id = f"win-{uuid.uuid4().hex[:12]}"
                        tax_note = f" (удержан налог НДФЛ 13%: {tax:,.2f} ₽)" if tax > Decimal("0.00") else ""
                        tx = Transaction(
                            id=tx_id,
                            sender_account_id=None,
                            recipient_account_id=user_acc.id,
                            amount=payout,
                            tx_type="BET_WIN",
                            category="Ставки и азарт",
                            description=(
                                f"Выигрыш по ставке {payout:,.2f} ₽{tax_note}: «{match.team_home} — {match.team_away}» "
                                f"({bet.bet_type}) с кэфом {bet.odds}"
                            ),
                            status="COMPLETED",
                            fiscal_code=ReceiptService.generate_fiscal_code(),
                            auth_code=ReceiptService.generate_auth_code(),
                            created_at=current_time,
                        )
                        ledger_entry = LedgerEntry(
                            transaction_id=tx_id,
                            account_id=user_acc.id,
                            direction="CREDIT",
                            amount=payout,
                            balance_after=new_bal,
                            created_at=current_time,
                        )
                        session.add(tx)
                        session.add(ledger_entry)

                    bet.status = "WON"
                else:
                    bet.status = "LOST"

        await session.flush()
        return pending_bets

    @classmethod
    async def seed_upcoming_matches(
        cls,
        session: AsyncSession,
        count_football: int = 4,
        count_esports: int = 2,
    ) -> list[SportsMatch]:
        """Seed randomized upcoming football and esports matches with dynamic pairings and calculated odds."""
        # 1. Randomize football matchups from catalog
        shuffled_fb = list(cls.FOOTBALL_TEAMS)
        random.shuffle(shuffled_fb)

        # 2. Randomize esports matchups from catalog
        shuffled_es = list(cls.ESPORTS_TEAMS)
        random.shuffle(shuffled_es)

        created: list[SportsMatch] = []
        now = datetime.datetime.now()

        # Generate football matches
        fb_pairs_count = min(count_football, len(shuffled_fb) // 2)
        for i in range(fb_pairs_count):
            t_home = shuffled_fb[2 * i]
            t_away = shuffled_fb[2 * i + 1]

            # Current form / condition fluctuation (+-1.2 rating)
            h_rating = round(float(t_home["rating"]) + random.uniform(-1.2, 1.2), 1)
            a_rating = round(float(t_away["rating"]) + random.uniform(-1.2, 1.2), 1)
            hours_ahead = (i + 1) * 2

            match = await cls.create_match(
                session,
                sport="FOOTBALL",
                team_home=t_home["name"],
                team_away=t_away["name"],
                home_rating=h_rating,
                away_rating=a_rating,
                scheduled_at=now + datetime.timedelta(hours=hours_ahead),
            )
            created.append(match)

        # Generate esports matches
        es_pairs_count = min(count_esports, len(shuffled_es) // 2)
        for j in range(es_pairs_count):
            t_home = shuffled_es[2 * j]
            t_away = shuffled_es[2 * j + 1]

            h_rating = round(float(t_home["rating"]) + random.uniform(-1.2, 1.2), 1)
            a_rating = round(float(t_away["rating"]) + random.uniform(-1.2, 1.2), 1)
            hours_ahead = (j + 1) * 2 + 1

            match = await cls.create_match(
                session,
                sport="ESPORTS",
                team_home=t_home["name"],
                team_away=t_away["name"],
                home_rating=h_rating,
                away_rating=a_rating,
                scheduled_at=now + datetime.timedelta(hours=hours_ahead),
            )
            created.append(match)

        return created
