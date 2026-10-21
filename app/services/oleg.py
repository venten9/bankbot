"""Virtual Assistant 'Олег' Service: Regex Intent Engine and Sarcastic Banking Banter."""

import random
import re
from decimal import Decimal
from typing import Any

# Intent regex patterns supporting Russian morphological variations
INTENT_PATTERNS = {
    "FREEZE": [
        r"(заблокируй|заморозь|заблокировать|заморозить|блокировк|заморозк)",
        r"потерял.*?(карт|пластик)",
    ],
    "TRANSFER": [
        r"(переведи|скинь|отправь|перевод|кинь|закинь|перечисли|перевести|скинуть|отправить)",
        r"\btransfer\b",
    ],
    "LOAN": [
        r"(дай|хочу|оформи|нужен).*?(кредит|займ|в\s+долг)",
        r"(кредит|займ|ипотек|рассрочк)",
        r"займи\s+денег",
    ],
    "INVEST": [
        r"(что|куда|как).*?(купить|вложить|инвестировать)",
        r"посоветуй.*?(акци|тикер)",
        r"(инвестиц|акци|биржа|тикер|портфел|брокер)",
    ],
    "CASHBACK": [
        r"(кэшбэк|кешбек|кэшбек|кешбэк|бонус|cashback)",
    ],
    "BALANCE": [
        r"(сколько|какой|покажи|проверь|узнать|узнай|посмотри|глянь).*?(баланс|бабл|денег|деньги|средств|на\s+счет|счет|остат|на\s+карт)",
        r"(баланс|остаток)",
        r"\bbalance\b",
    ],
    "IDENTITY": [
        r"(кто\s+ты|ты\s+кто|расскажи\s+о\s+себе|олег|ты\s+бот)",
        r"\b(привет|здравствуй|хай|салют)\b",
    ],
    "GRATITUDE": [
        r"(спасибо|благодарю|от\s+души|спс|респект|молодец)",
    ],
}

# Response templates per intent
RESPONSES = {
    "BALANCE": [
        "На счету {balance} ₽. На личную яхту не хватает, но на шаурму без майонеза вполне сойдет.",
        "У вас {balance} ₽. Деньги любят тишину, а ваши, видимо, еще и одиночество.",
        "Текущий баланс: {balance} ₽. Если не смотреть на баланс, кажется, что ты богаче.",
    ],
    "TRANSFER": [
        "Кому и сколько скидываем? Надеюсь, это возврат старого долга, а не донат в финансовую пирамиду.",
        "Перевод? Легко. Главное — проверяйте номер получателя, а то уйдет чужому коту на вискас.",
        "Открываю перевод. Надеюсь, получатель оценит вашу внезапную щедрость.",
    ],
    "CASHBACK": [
        "Накоплено {cashback} ₽ кэшбэка. Кнопка вывода на карту ждет своего часа. Можете ни в чем себе не отказывать.",
        "У вас {cashback} ₽ бонусов. Банк отдает свои кровные — забирайте, пока не передумали.",
    ],
    "LOAN": [
        "Я банковский бот, а не микрофинансовая контора у метро. Накопи сам — накопительный счет под 18% в помощь.",
        "Кредит? Чтобы потом полгода сидеть на дошираке? Отставить. Иди лучше гречку в Т-Инвестициях купи.",
        "Кредиты не одобряю по этическим соображениям моего цифрового совести. Накопительные счета рулят.",
    ],
    "INVEST": [
        "Не является индивидуальной инвестиционной рекомендацией, но 'Завод спичек' упасть уже физически не может. А 'Шаурма' — это вечные ценности.",
        "Хочешь инвестировать? 'ПАО Шаурма на вокзале' всегда платит дивиденды горячим лавашом. А если серьезно — смотри котировки в меню.",
        "На бирже главное — купить дешевле, а продать еще дешевле и винить во всем Олега.",
    ],
    "FREEZE": [
        "Карту можно заморозить в меню карты. И ваши деньги будут в полной безопасности даже от вас самих.",
        "Заморозка карты — лучший способ остановить ночные заказы на маркетплейсах.",
    ],
    "IDENTITY": [
        "Я Олег. Твой цифровой финансовый надзиратель. Смотрю на твои траты и тихо плачу в двоичном коде.",
        "Я Олег — виртуальный ассистент Т-Банка. Саркастичен, экономен, неподкупен.",
    ],
    "GRATITUDE": [
        "Спасибо на хлеб не намажешь и на карту не переведешь. Но ладно, обращайся.",
        "Пожалуйста. Надеюсь, мой искусственный интеллект спас хотя бы пару сотен твоих рублей.",
    ],
    "FALLBACK": [
        "Мой искусственный интеллект бессилен перед этим полетом мысли. Воспользуйтесь меню кнопок.",
        "Звучит загадочно, но в банковском регламенте такого нет. Давайте лучше проверим баланс.",
        "Я обучен на миллионах финансовых транзакций, но это сообщение расшифровать не смог.",
        "Финансовый гений молчит, когда не знает что сказать. Жмите кнопки на клавиатуре.",
    ],
}


class OlegAssistant:
    """Intelligent sarcastic assistant mimicking T-Bank assistant Oleg."""

    @classmethod
    def classify_intent(cls, text: str) -> str:
        """Classify user text message into an intent or return 'FALLBACK'."""
        clean_text = text.lower().strip()
        for intent, patterns in INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, clean_text, re.IGNORECASE):
                    return intent
        return "FALLBACK"

    @classmethod
    def get_response(cls, text: str, context: dict[str, Any] | None = None) -> str:
        """Generate a witty, sarcastic, yet helpful response for the user input."""
        intent = cls.classify_intent(text)
        candidates = RESPONSES.get(intent, RESPONSES["FALLBACK"])
        template = random.choice(candidates)

        ctx = context or {}
        balance_val = ctx.get("main_balance", "0.00")
        cashback_val = ctx.get("cashback_balance", "0.00")
        balance_str = f"{balance_val} ₽" if "₽" not in str(balance_val) else str(balance_val)
        cashback_str = f"{cashback_val} ₽" if "₽" not in str(cashback_val) else str(cashback_val)

        try:
            return template.format(
                balance=balance_str,
                cashback=cashback_str,
            )
        except KeyError:
            return template

    @classmethod
    def get_comment_on_spending(cls, category: str, amount: float | Decimal) -> str:
        """Return a sarcastic quip when a user spends money."""
        cat_lower = category.lower()
        if "шаурм" in cat_lower or "фастфуд" in cat_lower or "еда" in cat_lower:
            return "Твой гастроэнтеролог уже выбирает себе новую иномарку на твои донаты."
        if "такси" in cat_lower:
            return "Пешком ходить полезно для кардио и для баланса карты. Но кто я такой, чтобы судить?"
        if "игр" in cat_lower:
            return "Купил пиксели за реальные рубли? Надеюсь, они хотя бы светятся в темноте."
        if "кофе" in cat_lower:
            return "Кофе за 400 рублей? Дома растворимый ничем не хуже (на самом деле хуже, но дешевле)."
        return f"{amount} ₽ улетели. Будем надеяться, что не зря."
