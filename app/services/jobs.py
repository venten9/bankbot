"""Job and Career Service: 4 Branches, 20 Professions, Multi-employment, Shifts and Hustles."""

import datetime
import html
import random
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Account, LedgerEntry, Transaction, User, UserJob
from app.services.banking import BankingService
from app.services.card import CardService
from app.services.receipts import ReceiptService


class JobBranch:
    IT = "IT"
    SERVICE = "SERVICE"
    FINANCE = "FINANCE"
    SHADOW = "SHADOW"

    ALL = [IT, SERVICE, FINANCE, SHADOW]
    TITLES = {
        IT: "💻 IT / Офис",
        SERVICE: "🍔 Общепит и Услуги",
        FINANCE: "📊 Финансы и Банкинг",
        SHADOW: "🕶 Теневой сектор",
    }

    @classmethod
    def get_title(cls, branch: str) -> str:
        """Return formatted branch title with emoji or raw branch code as fallback."""
        return cls.TITLES.get(branch, branch)


# Catalog of 20 professions across 4 branches (5 levels each)
# Promotion requirements (shifts_to_promote): Level 1 -> 3, Level 2 -> 5, Level 3 -> 8, Level 4 -> 15, Level 5 -> 0 (Max)
JOB_CATALOG: dict[str, dict[int, dict[str, Any]]] = {
    JobBranch.IT: {
        1: {
            "title": "Стажер техподдержки",
            "salary": Decimal("1500.00"),
            "shifts_to_promote": 3,
            "desc": "Перезагружает роутеры и советует выключить и включить снова.",
        },
        2: {
            "title": "Тестировщик / Эникейщик",
            "salary": Decimal("4000.00"),
            "shifts_to_promote": 5,
            "desc": "Заходит в бар, заказывает 0 пива, 999999 пива и ящерицу.",
        },
        3: {
            "title": "Джуниор-программист",
            "salary": Decimal("10000.00"),
            "shifts_to_promote": 8,
            "desc": "Копирует код со StackOverflow и молится перед каждым коммитом.",
        },
        4: {
            "title": "Тимлид на созвонах",
            "salary": Decimal("25000.00"),
            "shifts_to_promote": 15,
            "desc": "Проводит 8 часов в Zoom в день и спрашивает: 'Какой статус по задаче?'.",
        },
        5: {
            "title": "CTO / Технический директор",
            "salary": Decimal("60000.00"),
            "shifts_to_promote": 0,  # Max level
            "desc": "Пишет посты на Хабр о микросервисах и получает опционы.",
        },
    },
    JobBranch.SERVICE: {
        1: {
            "title": "Промоутер в костюме сосиски",
            "salary": Decimal("1200.00"),
            "shifts_to_promote": 3,
            "desc": "Раздает листовки у метро и греется в лучах осеннего солнца.",
        },
        2: {
            "title": "Курьер на электросамокате",
            "salary": Decimal("3500.00"),
            "shifts_to_promote": 5,
            "desc": "Мчит сквозь пробки со скоростью звука с желтым рюкзаком за спиной.",
        },
        3: {
            "title": "Мастер шаурмы на вокзале",
            "salary": Decimal("8500.00"),
            "shifts_to_promote": 8,
            "desc": "Владеет секретом фирменного чесночного соуса. Клиенты боготворят.",
        },
        4: {
            "title": "Бариста спешелти-кофе",
            "salary": Decimal("20000.00"),
            "shifts_to_promote": 15,
            "desc": "Различает 48 оттенков кислотности в эфиопской арабике.",
        },
        5: {
            "title": "Владелец сети шаурмичных",
            "salary": Decimal("50000.00"),
            "shifts_to_promote": 0,  # Max level
            "desc": "Король городского стритфуда. Оборот в миллионах, точка у каждого вокзала.",
        },
    },
    JobBranch.FINANCE: {
        1: {
            "title": "Кредитный агент у метро",
            "salary": Decimal("2000.00"),
            "shifts_to_promote": 3,
            "desc": "Предлагает оформить карту всем прохожим с улыбкой акулы.",
        },
        2: {
            "title": "Операционист Т-Банка",
            "salary": Decimal("5000.00"),
            "shifts_to_promote": 5,
            "desc": "Открывает вклады под 18% годовых и мастерски гасит конфликты.",
        },
        3: {
            "title": "Аналитик крипто-инвестиций",
            "salary": Decimal("12000.00"),
            "shifts_to_promote": 8,
            "desc": "Чертит треугольники на графиках и верит в туземун мемкоинов.",
        },
        4: {
            "title": "Инвестиционный банкир",
            "salary": Decimal("30000.00"),
            "shifts_to_promote": 15,
            "desc": "Обедает в ресторанах с депозитом и структурирует сделки M&A.",
        },
        5: {
            "title": "Топ-менеджер Т-Банка",
            "salary": Decimal("75000.00"),
            "shifts_to_promote": 0,  # Max level
            "desc": "Управляет экосистемой банка, решает судьбу кэшбэка и ставок по вкладам.",
        },
    },
    JobBranch.SHADOW: {
        1: {
            "title": "Продавец люкс-копий кроссовок",
            "salary": Decimal("3000.00"),
            "shifts_to_promote": 3,
            "desc": "'Оригинал с фабрики Гуччи, брат, честно!'. Торгует на рынке.",
        },
        2: {
            "title": "Карманник в метро",
            "salary": Decimal("7500.00"),
            "shifts_to_promote": 5,
            "desc": "Ловкость рук и никакого мошенничества. Главное — не попасть на камеры.",
        },
        3: {
            "title": "Дроповод / Обнальщик",
            "salary": Decimal("18000.00"),
            "shifts_to_promote": 8,
            "desc": "Снимает наличку в темных банкоматах в капюшоне и очках.",
        },
        4: {
            "title": "Владелец подпольного казино",
            "salary": Decimal("45000.00"),
            "shifts_to_promote": 15,
            "desc": "Покерные столы в замаскированном гараже. Охрана и секретный пароль.",
        },
        5: {
            "title": "Теневой барон города",
            "salary": Decimal("100000.00"),
            "shifts_to_promote": 0,  # Max level
            "desc": "Контролирует все серые схемы города. Огромный куш, но облавы повсюду.",
        },
    },
}

# Catalog of interactive quick hustles (situations with 2 options)
HUSTLE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "hustle_server_crash",
        "branch": JobBranch.IT,
        "title": "Пятничный деплой",
        "text": "В пятницу в 18:00 сервер упал после обновления. Ваши действия?",
        "options": [
            {
                "text": "Быстро накатить бэкап и свалить на джуна",
                "success_rate": 0.85,
                "reward_min": 1500,
                "reward_max": 3000,
                "penalty_min": 500,
                "penalty_max": 1000,
                "success_msg": "Бэкап встал идеально! Вас похвалили за оперативность: +{amt} ₽.",
                "fail_msg": "Джун показал логи тимлиду, вас оштрафовали: -{amt} ₽.",
            },
            {
                "text": "Чинить базу данных всю ночь самому",
                "success_rate": 0.60,
                "reward_min": 4000,
                "reward_max": 7000,
                "penalty_min": 1500,
                "penalty_max": 2500,
                "success_msg": "Вы героически спасли прод! Щедрая премия за переработку: +{amt} ₽.",
                "fail_msg": "От усталости вы случайно удалили базу пользователей: штраф -{amt} ₽.",
            },
        ],
    },
    {
        "id": "hustle_secret_sauce",
        "branch": JobBranch.SERVICE,
        "title": "Секретный заказ",
        "text": "Клиент просит шаурму 'по-царски' с двойным мясом и готов доплатить наличкой мимо кассы.",
        "options": [
            {
                "text": "Взять кэш и приготовить шедевр",
                "success_rate": 0.75,
                "reward_min": 1200,
                "reward_max": 2500,
                "penalty_min": 800,
                "penalty_max": 1500,
                "success_msg": "Клиент остался в восторге и оставил щедрые чаевые: +{amt} ₽.",
                "fail_msg": "Это была тайная проверка менеджера! Штраф из зарплаты: -{amt} ₽.",
            },
            {
                "text": "Пробить честно через кассу Т-Банка",
                "success_rate": 0.95,
                "reward_min": 800,
                "reward_max": 1200,
                "penalty_min": 0,
                "penalty_max": 0,
                "success_msg": "Честный труд окупается: начислен законный процент от продажи: +{amt} ₽.",
                "fail_msg": "Клиент ушел к конкурентам без шаурмы.",
            },
        ],
    },
    {
        "id": "hustle_crypto_signal",
        "branch": JobBranch.FINANCE,
        "title": "Инсайд от крипто-кита",
        "text": "Знакомый аналитик скинул инсайд о пампе мемкоина $SHAURMA. Что делаем?",
        "options": [
            {
                "text": "Залететь на все свободные средства с плечом",
                "success_rate": 0.50,
                "reward_min": 5000,
                "reward_max": 12000,
                "penalty_min": 3000,
                "penalty_max": 6000,
                "success_msg": "Ракета улетела на Луну! Зафиксирована прибыль на споте: +{amt} ₽.",
                "fail_msg": "Классический памп и дамп. Сделка ликвидирована: убыток -{amt} ₽.",
            },
            {
                "text": "Продать этот инсайд в платном Telegram-канале",
                "success_rate": 0.80,
                "reward_min": 2000,
                "reward_max": 4000,
                "penalty_min": 500,
                "penalty_max": 1000,
                "success_msg": "Подписчики раскупили доступ к сигналам: заработано +{amt} ₽.",
                "fail_msg": "Подписчики пожаловались на скам, вывод средств заблокирован: -{amt} ₽.",
            },
        ],
    },
    {
        "id": "hustle_shadow_raid",
        "branch": JobBranch.SHADOW,
        "title": "Внезапная облава",
        "text": "К подпольному складу кроссовок подъехали черные фургоны налоговой и ОБЭП!",
        "options": [
            {
                "text": "Попытаться дать взятку начальнику проверки",
                "success_rate": 0.65,
                "reward_min": 3000,
                "reward_max": 8000,
                "penalty_min": 4000,
                "penalty_max": 9000,
                "success_msg": "Вопрос успешно решен, склад спасен, товар распродан: +{amt} ₽.",
                "fail_msg": "Взятку не взяли, составлен протокол: конфискация и штраф -{amt} ₽.",
            },
            {
                "text": "Прыгать через забор с сумкой денег",
                "success_rate": 0.55,
                "reward_min": 6000,
                "reward_max": 15000,
                "penalty_min": 5000,
                "penalty_max": 12000,
                "success_msg": "Вы перемахнули забор и скрылись во дворах с кушем: +{amt} ₽!",
                "fail_msg": "Вас догнали на углу: отобрали сумку и выписали административный штраф: -{amt} ₽.",
            },
        ],
    },
]


class JobService:
    """Core domain service managing professions, multi-employment, shifts, and hustles."""

    DEFAULT_SHIFT_DURATION_SECONDS = 7200  # 2 hours
    DEFAULT_HUSTLE_COOLDOWN_SECONDS = 900  # 15 minutes
    MAX_ACTIVE_JOBS = 2

    @classmethod
    def get_job_info(cls, branch: str, level: int) -> dict[str, Any]:
        """Get static definition of a profession by branch and level."""
        branch_jobs = JOB_CATALOG.get(branch)
        if not branch_jobs:
            raise ValueError(f"Неизвестная ветка профессий: {branch}")
        job = branch_jobs.get(level)
        if not job:
            raise ValueError(f"Неизвестный уровень {level} для ветки {branch}")
        return job

    @classmethod
    def get_job_catalog(cls) -> dict[str, dict[int, dict[str, Any]]]:
        """Get the full 20-profession matrix catalog."""
        return JOB_CATALOG

    @classmethod
    def get_hustle_catalog(cls) -> list[dict[str, Any]]:
        """Get the full list of interactive hustle dilemmas."""
        return HUSTLE_CATALOG

    @classmethod
    async def get_user_active_jobs(cls, session: AsyncSession, user_id: int) -> list[UserJob]:
        """Get all currently active jobs for a user (at most 2)."""
        res = await session.execute(
            select(UserJob)
            .where(UserJob.user_id == user_id, UserJob.is_active == True)  # noqa: E712
            .order_by(UserJob.id.asc())
        )
        return list(res.scalars().all())

    @classmethod
    async def get_user_all_jobs(cls, session: AsyncSession, user_id: int) -> list[UserJob]:
        """Get all job records for a user (both active and inactive)."""
        res = await session.execute(
            select(UserJob)
            .where(UserJob.user_id == user_id)
            .order_by(UserJob.id.asc())
        )
        return list(res.scalars().all())

    @classmethod
    async def get_user_job_by_branch(
        cls, session: AsyncSession, user_id: int, branch: str
    ) -> UserJob | None:
        """Get job record for a user in a specific branch (active or inactive)."""
        res = await session.execute(
            select(UserJob).where(UserJob.user_id == user_id, UserJob.branch == branch)
        )
        return res.scalars().first()

    @classmethod
    async def apply_for_job(cls, session: AsyncSession, user_id: int, branch: str) -> UserJob:
        """Hire a user for a branch. Allows multi-employment up to MAX_ACTIVE_JOBS (2)."""
        if branch not in JobBranch.ALL:
            raise ValueError(f"Неизвестная ветка профессий: {branch}")

        active_jobs = await cls.get_user_active_jobs(session, user_id)
        for j in active_jobs:
            if j.branch == branch:
                raise ValueError(f"Вы уже устроены в ветке '{JobBranch.get_title(branch)}'!")

        if len(active_jobs) >= cls.MAX_ACTIVE_JOBS:
            raise ValueError(
                f"Вы уже совмещаете {cls.MAX_ACTIVE_JOBS} работы! Увольтесь с одной, чтобы устроиться на новую."
            )

        existing = await cls.get_user_job_by_branch(session, user_id, branch)
        if existing:
            existing.is_active = True
            existing.is_on_shift = False
            existing.shift_started_at = None
            await session.flush()
            return existing

        new_job = UserJob(
            user_id=user_id,
            branch=branch,
            level=1,
            shifts_completed=0,
            is_active=True,
            is_on_shift=False,
            shift_started_at=None,
            last_shift_completed_at=None,
        )
        session.add(new_job)
        await session.flush()
        return new_job

    @classmethod
    async def quit_job(cls, session: AsyncSession, user_id: int, branch: str) -> UserJob:
        """Quit an active job. Preserves completed shifts and level for future re-employment."""
        job = await cls.get_user_job_by_branch(session, user_id, branch)
        if not job or not job.is_active:
            raise ValueError("Вы не устроены на этой работе!")

        job.is_active = False
        job.is_on_shift = False
        job.shift_started_at = None
        await session.flush()
        return job

    @classmethod
    async def start_shift(
        cls, session: AsyncSession, user_id: int, branch: str, now: datetime.datetime | None = None
    ) -> UserJob:
        """Begin a work shift for an active job."""
        job = await cls.get_user_job_by_branch(session, user_id, branch)
        if not job or not job.is_active:
            raise ValueError("Сначала устройтесь на эту работу на бирже труда!")

        if job.is_on_shift:
            raise ValueError("Вы уже на смене! Дождитесь окончания и заберите зарплату.")

        current_time = now or datetime.datetime.now()

        # Car buff: cooldown reduction
        from app.services.property import PropertyService
        _, cooldown_reduction_minutes = await PropertyService.get_user_car_buffs(session, user_id)
        if cooldown_reduction_minutes > 0:
            shift_start = current_time - datetime.timedelta(minutes=cooldown_reduction_minutes)
        else:
            shift_start = current_time

        job.is_on_shift = True
        job.shift_started_at = shift_start
        await session.flush()
        return job

    @classmethod
    async def claim_salary(
        cls,
        session: AsyncSession,
        user_id: int,
        branch: str,
        now: datetime.datetime | None = None,
        duration_seconds: int | None = None,
        event_roll: float | None = None,
        event_pct: int | None = None,
    ) -> tuple[Decimal, str]:
        """Collect salary after shift completion with random workplace events, atomic balance update, and LedgerEntry."""
        job = await cls.get_user_job_by_branch(session, user_id, branch)
        if not job or not job.is_active:
            raise ValueError("Вы не устроены на этой работе!")

        if not job.is_on_shift or not job.shift_started_at:
            raise ValueError("Вы сейчас не на смене. Нажмите 'Начать смену'!")

        current_time = now or datetime.datetime.now()
        required_duration = (
            duration_seconds
            if duration_seconds is not None
            else cls.DEFAULT_SHIFT_DURATION_SECONDS
        )
        elapsed = (current_time - job.shift_started_at).total_seconds()

        if elapsed < required_duration:
            remaining_mins = max(1, int((required_duration - elapsed) // 60))
            raise ValueError(
                f"Смена еще не окончена! Осталось работать еще примерно {remaining_mins} мин."
            )

        job_info = cls.get_job_info(job.branch, job.level)
        base_salary = job_info["salary"]

        # Random workplace event roll:
        # Non-Shadow: 15% Bonus (+20-50%), 10% Reprimand (-10-20%), 75% Normal
        # Shadow: 20% Police Raid (-20-50%), 15% Bonus (+20-50%), 10% Reprimand (-10-20%), 55% Normal
        roll = event_roll if event_roll is not None else random.random()
        event_text = ""
        payout = base_salary

        if branch == JobBranch.SHADOW:
            if roll < 0.20:
                # 20% chance of police raid in shadow sector
                pct_val = event_pct if event_pct is not None else random.randint(20, 50)
                loss = (base_salary * Decimal(str(pct_val)) / Decimal("100")).quantize(Decimal("0.01"))
                payout = max(Decimal("0.00"), base_salary - loss)
                event_text = (
                    f"⚠️ <b>Внимание: Облава на точке!</b> Пришлось скинуть часть товара и откупиться: "
                    f"удержано {loss} ₽. К выплате: {payout} ₽."
                )
            elif roll < 0.35:
                # 15% chance of bonus in shadow sector (0.35 - 0.20 = 0.15)
                pct_val = event_pct if event_pct is not None else random.randint(20, 50)
                bonus = (base_salary * Decimal(str(pct_val)) / Decimal("100")).quantize(Decimal("0.01"))
                payout = base_salary + bonus
                event_text = (
                    f"🎉 <b>Удачный куш!</b> Серые схемы сработали на ура: +{bonus} ₽ к выручке!"
                )
            elif roll < 0.45:
                # 10% chance of reprimand / shakedown (0.45 - 0.35 = 0.10)
                pct_val = event_pct if event_pct is not None else random.randint(10, 20)
                penalty = (base_salary * Decimal(str(pct_val)) / Decimal("100")).quantize(Decimal("0.01"))
                payout = max(Decimal("0.00"), base_salary - penalty)
                event_text = (
                    f"📋 <b>Штраф:</b> Недостача в общаке, удержано {penalty} ₽."
                )
            else:
                event_text = "✅ Смена отработана без происшествий! Куш забран."
        else:
            if roll < 0.15:
                # 15% bonus event
                pct_val = event_pct if event_pct is not None else random.randint(20, 50)
                bonus = (base_salary * Decimal(str(pct_val)) / Decimal("100")).quantize(Decimal("0.01"))
                payout = base_salary + bonus
                event_text = (
                    f"🎉 <b>Премия за усердие!</b> Руководство отметило ваш вклад: +{bonus} ₽ к зарплате!"
                )
            elif roll < 0.25:
                # 10% reprimand event (0.25 - 0.15 = 0.10)
                pct_val = event_pct if event_pct is not None else random.randint(10, 20)
                penalty = (base_salary * Decimal(str(pct_val)) / Decimal("100")).quantize(Decimal("0.01"))
                payout = max(Decimal("0.00"), base_salary - penalty)
                event_text = (
                    f"📋 <b>Штраф:</b> Небольшой проступок на рабочем месте, удержано {penalty} ₽."
                )
            else:
                event_text = "✅ Смена отработана на отлично! Базовая зарплата начислена."

        # Car buff: salary bonus %
        from app.services.property import PropertyService
        salary_bonus_pct, _ = await PropertyService.get_user_car_buffs(session, user_id)
        car_bonus = Decimal("0.00")
        if salary_bonus_pct > 0:
            car_bonus = (base_salary * Decimal(str(salary_bonus_pct)) / Decimal("100")).quantize(Decimal("0.01"))
            payout += car_bonus
            if event_text:
                event_text += f"\n🚗 <b>Бонус за авто (+{salary_bonus_pct}%):</b> +{car_bonus:,.2f} ₽"
            else:
                event_text = f"🚗 <b>Бонус за авто (+{salary_bonus_pct}%):</b> +{car_bonus:,.2f} ₽"

        # 1. Atomic conditional update on UserJob to conclude shift before crediting balance.
        # Enforces compare-and-swap (CAS) semantics on is_on_shift to prevent double payout race condition.
        claim_stmt = (
            update(UserJob)
            .where(UserJob.id == job.id)
            .where(UserJob.is_on_shift == True)  # noqa: E712
            .values(
                is_on_shift=False,
                shift_started_at=None,
                shifts_completed=UserJob.shifts_completed + 1,
                last_shift_completed_at=current_time,
            )
        )
        claim_res = await session.execute(claim_stmt)
        if claim_res.rowcount == 0:
            raise ValueError("Смена уже завершена или не начата!")

        # Synchronize in-memory ORM model with persisted state
        await session.refresh(job)

        # 2. Double-entry ledger integration: Credit salary to main bank account
        main_acc = await BankingService.get_user_main_account(session, user_id)
        if not main_acc:
            raise ValueError(f"Основной счет пользователя {user_id} не найден")

        # Atomic SQL update of Account.balance
        await session.execute(
            update(Account)
            .where(Account.id == main_acc.id)
            .values(balance=Account.balance + payout)
        )

        # Query authoritative new balance post-update
        new_balance = (
            await session.execute(
                select(Account.balance).where(Account.id == main_acc.id)
            )
        ).scalar_one()

        await session.refresh(main_acc)

        # 3. Create Transaction and LedgerEntry records
        tx_id = f"sal-{uuid.uuid4().hex[:12]}"
        tx_desc = f"Зарплата: {job_info['title']} ({JobBranch.get_title(branch)})"
        if car_bonus > Decimal("0.00"):
            tx_desc += f" + Авто-бонус {salary_bonus_pct}%: {car_bonus:,.2f} ₽"

        tx = Transaction(
            id=tx_id,
            sender_account_id=None,
            recipient_account_id=main_acc.id,
            amount=payout,
            tx_type="SALARY",
            category="Зарплата и карьера",
            description=tx_desc,
            status="COMPLETED",
            fiscal_code=ReceiptService.generate_fiscal_code(),
            auth_code=ReceiptService.generate_auth_code(),
            created_at=current_time,
        )
        ledger_entry = LedgerEntry(
            transaction_id=tx_id,
            account_id=main_acc.id,
            direction="CREDIT",
            amount=payout,
            balance_after=new_balance,
            created_at=current_time,
        )
        tx.ledger_entries = [ledger_entry]
        session.add(tx)
        session.add(ledger_entry)

        await session.flush()

        return payout, event_text

    @classmethod
    async def promote_job(cls, session: AsyncSession, user_id: int, branch: str) -> tuple[bool, str]:
        """Promote user to the next career level if they meet required shift count."""
        job = await cls.get_user_job_by_branch(session, user_id, branch)
        if not job or not job.is_active:
            raise ValueError("Вы не устроены на этой работе!")

        if job.level >= 5:
            return False, "Вы уже достигли максимальной должности в этой профессии! 🏆"

        current_info = cls.get_job_info(job.branch, job.level)
        required = current_info["shifts_to_promote"]

        if job.shifts_completed < required:
            needed = required - job.shifts_completed
            return False, f"Для повышения нужно отработать еще {needed} смен(ы)! (Отработано: {job.shifts_completed}/{required})"

        job.level += 1
        new_info = cls.get_job_info(job.branch, job.level)
        await session.flush()
        return (
            True,
            f"🎉 <b>ПОЗДРАВЛЯЕМ С ПОВЫШЕНИЕМ!</b>\n\n"
            f"Ваша новая должность: <b>{new_info['title']}</b>\n"
            f"Новая зарплата за смену: <b>{new_info['salary']} ₽</b>\n"
            f"<i>{new_info['desc']}</i>",
        )

    @classmethod
    def get_hustle_scenario(cls, hustle_id: str | None = None) -> dict[str, Any]:
        """Get specific hustle scenario by ID or return random scenario."""
        if hustle_id:
            for h in HUSTLE_CATALOG:
                if h["id"] == hustle_id:
                    return h
            raise ValueError(f"Шабашка с ID '{hustle_id}' не найдена")
        return random.choice(HUSTLE_CATALOG)

    @classmethod
    async def execute_hustle(
        cls,
        session: AsyncSession,
        user_id: int,
        hustle_id: str,
        choice_idx: int,
        now: datetime.datetime | None = None,
        cooldown_seconds: int | None = None,
        success_roll: float | None = None,
        reward_override: Decimal | None = None,
        penalty_override: Decimal | None = None,
    ) -> tuple[Decimal, str]:
        """Execute a quick hustle decision and credit/debit account atomically with non-negative clamping."""
        user = await session.get(User, user_id)
        if not user:
            raise ValueError("Пользователь не найден")

        current_time = now or datetime.datetime.now()
        cd = (
            cooldown_seconds
            if cooldown_seconds is not None
            else cls.DEFAULT_HUSTLE_COOLDOWN_SECONDS
        )

        if user.last_hustle_at:
            elapsed = (current_time - user.last_hustle_at).total_seconds()
            if elapsed < cd:
                rem_mins = max(1, int((cd - elapsed) // 60))
                raise ValueError(
                    f"Шабашкины руки должны отдохнуть! До следующей подработки: ~{rem_mins} мин."
                )

        hustle = cls.get_hustle_scenario(hustle_id)
        if choice_idx < 0 or choice_idx >= len(hustle["options"]):
            raise ValueError("Некорректный выбор варианта в шабашке")

        # Atomic conditional update on User.last_hustle_at with rowcount verification
        # to mathematically eliminate hustle cooldown bypass race conditions
        cd_cutoff = current_time - datetime.timedelta(seconds=cd)
        hustle_stmt = (
            update(User)
            .where(User.id == user_id)
            .where((User.last_hustle_at.is_(None)) | (User.last_hustle_at <= cd_cutoff))
            .values(last_hustle_at=current_time)
        )
        hustle_res = await session.execute(hustle_stmt)
        if hustle_res.rowcount == 0:
            raise ValueError(
                "Шабашкины руки должны отдохнуть! До следующей подработки еще есть время."
            )
        user.last_hustle_at = current_time

        opt = hustle["options"][choice_idx]
        roll = success_roll if success_roll is not None else random.random()
        is_success = roll <= opt["success_rate"]

        main_acc = await BankingService.get_user_main_account(session, user_id)
        if not main_acc:
            raise ValueError("Основной счет пользователя не найден")

        delta = Decimal("0.00")
        msg = ""

        if is_success:
            amt = (
                reward_override
                if reward_override is not None
                else Decimal(str(random.randint(opt["reward_min"], opt["reward_max"])))
            )
            delta = amt
            # Atomic SQL credit
            await session.execute(
                update(Account)
                .where(Account.id == main_acc.id)
                .values(balance=Account.balance + amt)
            )
            new_balance = (
                await session.execute(
                    select(Account.balance).where(Account.id == main_acc.id)
                )
            ).scalar_one()
            await session.refresh(main_acc)

            msg = opt["success_msg"].format(amt=amt)
            tx_type = "HUSTLE_REWARD"

            tx_id = f"hst-{uuid.uuid4().hex[:12]}"
            tx = Transaction(
                id=tx_id,
                sender_account_id=None,
                recipient_account_id=main_acc.id,
                amount=amt,
                tx_type=tx_type,
                category="Подработки и шабашки",
                description=f"Шабашка: {hustle['title']} ({opt['text'][:40]})",
                status="COMPLETED",
                fiscal_code=ReceiptService.generate_fiscal_code(),
                auth_code=ReceiptService.generate_auth_code(),
                created_at=current_time,
            )
            ledger_entry = LedgerEntry(
                transaction_id=tx_id,
                account_id=main_acc.id,
                direction="CREDIT",
                amount=amt,
                balance_after=new_balance,
                created_at=current_time,
            )
            tx.ledger_entries = [ledger_entry]
            session.add(tx)
            session.add(ledger_entry)
        else:
            raw_penalty = (
                penalty_override
                if penalty_override is not None
                else Decimal(str(random.randint(opt["penalty_min"], opt["penalty_max"])))
            )
            # Retrieve latest balance before clamping
            curr_balance = (
                await session.execute(
                    select(Account.balance).where(Account.id == main_acc.id)
                )
            ).scalar_one()

            # Non-negative balance clamping: cannot reduce balance below 0.00
            actual_penalty = min(curr_balance, raw_penalty)
            delta = -actual_penalty
            msg = opt["fail_msg"].format(amt=actual_penalty)
            tx_type = "HUSTLE_PENALTY"

            if actual_penalty > Decimal("0.00"):
                # Atomic SQL debit with balance check guard
                await session.execute(
                    update(Account)
                    .where(Account.id == main_acc.id)
                    .where(Account.balance >= actual_penalty)
                    .values(balance=Account.balance - actual_penalty)
                )
                new_balance = (
                    await session.execute(
                        select(Account.balance).where(Account.id == main_acc.id)
                    )
                ).scalar_one()
                await session.refresh(main_acc)

                tx_id = f"hst-{uuid.uuid4().hex[:12]}"
                tx = Transaction(
                    id=tx_id,
                    sender_account_id=main_acc.id,
                    recipient_account_id=None,
                    amount=actual_penalty,
                    tx_type=tx_type,
                    category="Подработки и шабашки",
                    description=f"Шабашка (штраф): {hustle['title']} ({opt['text'][:40]})",
                    status="COMPLETED",
                    fiscal_code=ReceiptService.generate_fiscal_code(),
                    auth_code=ReceiptService.generate_auth_code(),
                    created_at=current_time,
                )
                ledger_entry = LedgerEntry(
                    transaction_id=tx_id,
                    account_id=main_acc.id,
                    direction="DEBIT",
                    amount=actual_penalty,
                    balance_after=new_balance,
                    created_at=current_time,
                )
                tx.ledger_entries = [ledger_entry]
                session.add(tx)
                session.add(ledger_entry)
            else:
                # Penalty clamped to 0.00 (user had 0 balance or penalty was 0)
                tx_id = f"hst-{uuid.uuid4().hex[:12]}"
                tx = Transaction(
                    id=tx_id,
                    sender_account_id=main_acc.id,
                    recipient_account_id=None,
                    amount=Decimal("0.00"),
                    tx_type=tx_type,
                    category="Подработки и шабашки",
                    description=f"Шабашка (штраф 0.00 ₽): {hustle['title']}",
                    status="COMPLETED",
                    fiscal_code=ReceiptService.generate_fiscal_code(),
                    auth_code=ReceiptService.generate_auth_code(),
                    created_at=current_time,
                )
                session.add(tx)

        # Cooldown was atomically updated and verified prior to evaluating dilemma options
        await session.flush()
        return delta, msg
