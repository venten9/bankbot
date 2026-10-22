"""Admin Panel Visual Formatters: Metrics Dashboard, User Inspection Cards, Ledger, and Audit."""

import html
from decimal import Decimal
from app.database.models import AdminLog, Transaction, User


def format_admin_dashboard(metrics: dict) -> str:
    """Format live banking metrics dashboard."""
    total_users = metrics.get("total_users", 0)
    active_accounts = metrics.get("active_accounts", 0)
    frozen_accounts = metrics.get("frozen_accounts", 0)
    total_liquidity = Decimal(str(metrics.get("total_liquidity", "0.00")))
    total_txs = metrics.get("total_transactions", 0)
    total_vol = Decimal(str(metrics.get("total_volume", "0.00")))
    avg_check = (total_vol / total_txs) if total_txs > 0 else Decimal("0.00")

    lines = [
        "🏢 <b>Т-Банк: Панель Управления (Администратор)</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "Добро пожаловать в центр управления полетами Т-Банка!",
        "",
        f"👥 <b>Всего пользователей:</b>      <code>{total_users:,}</code>",
        f"🟢 <b>Активных аккаунтов:</b>       <code>{active_accounts:,}</code>",
        f"❄️ <b>Замороженных аккаунтов:</b>    <code>{frozen_accounts:,}</code>",
        "",
        "💰 <b>Агрегированная ликвидность:</b>",
        f" • <b>Всего денег в банке:</b>     <code>{total_liquidity:,.2f} ₽</code>",
        "",
        "📊 <b>Статистика транзакций:</b>",
        f" • <b>Количество операций:</b>      <code>{total_txs:,}</code>",
        f" • <b>Общий оборот:</b>             <code>{total_vol:,.2f} ₽</code>",
        f" • <b>Средний чек операции:</b>     <code>{avg_check:,.2f} ₽</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def format_admin_user_card(
    user: User,
    overview: dict,
    recent_txs: list[Transaction],
    account_ids: list[str] | list[int] | set[str] | set[int],
) -> str:
    """Format comprehensive user inspection profile with card and balances."""
    full_name = html.escape(user.full_name or "Клиент", quote=False)
    username = f"@{html.escape(user.username, quote=False)}" if user.username else "<i>нет username</i>"
    reg_date = user.created_at.strftime("%d.%m.%Y %H:%M") if user.created_at else "—"
    status_emoji = "❄️ Заморожен" if user.is_blocked else "🟢 Активен (Не заморожен)"

    # Card info
    card_info = overview.get("card")
    if card_info:
        card_num = card_info.get("formatted_full") or card_info.get("card_number") or "2200 •••• •••• ••••"
        expiry = card_info.get("expiry_date", "—")
        cvv = card_info.get("cvv", "—")
        card_status = "❄️ Заморожена" if card_info.get("is_frozen") else "🟢 Активна"
    else:
        card_num = "Нет карты"
        expiry = "—"
        cvv = "—"
        card_status = "Не выпущена"

    # Balances
    main_bal = Decimal(str(overview.get("main_balance", "0.00")))
    sav_bal = Decimal(str(overview.get("savings_balance", "0.00")))
    sav_accrued = Decimal(str(overview.get("savings_accrued_interest", "0.00")))
    cb_bal = Decimal(str(overview.get("cashback_balance", "0.00")))
    port_val = Decimal(str(overview.get("portfolio_value", "0.00")))

    # Recent operations
    ops_lines = []
    if recent_txs:
        for tx in recent_txs:
            is_debit = tx.sender_account_id in account_ids
            sign = "-" if is_debit else "+"
            tx_time = tx.created_at.strftime("%d.%m %H:%M") if tx.created_at else "—"
            cat = html.escape(tx.category or "Операция", quote=False)
            desc = f" ({html.escape(tx.description[:25], quote=False)})" if tx.description else ""
            ops_lines.append(
                f" • <code>#TX-{tx.id[:8]}</code> | {tx_time} | {cat} | <b>{sign}{tx.amount:,.2f} ₽</b>{desc}"
            )
    else:
        ops_lines.append(" • <i>Операций пока нет</i>")

    lines = [
        f"👤 <b>Карточка клиента:</b> {full_name} ({username})",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🆔 <b>Telegram ID:</b> <code>{user.telegram_id}</code>",
        f"📅 <b>Дата регистрации:</b> {reg_date}",
        f"<b>Статус аккаунта:</b> {status_emoji}",
        "",
        "💳 <b>Карта Т-Банк Black:</b>",
        f" • <b>Номер карты:</b>   <code>{card_num}</code>",
        f" • <b>Срок действия:</b> <code>{expiry}</code> | <b>CVC/CVV:</b> <code>{cvv}</code>",
        f" • <b>Статус карты:</b>  {card_status}",
        "",
        "💰 <b>Балансы клиента:</b>",
        f" • <b>Основной счет:</b>  <b>{main_bal:,.2f} ₽</b>",
        f" • <b>Накопительный:</b>   <b>{sav_bal:,.2f} ₽</b> (+{sav_accrued:.2f} ₽ %)",
        f" • <b>Кэшбэк бонусы:</b>   <b>{cb_bal:,.2f} ₽</b>",
        f" • <b>Инвестпортфель:</b>  <b>{port_val:,.2f} ₽</b>",
        "",
        "📜 <b>Последние операции:</b>",
        *ops_lines,
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def format_user_ledger_statement(
    user: User,
    txs: list[Transaction],
    account_ids: list[str] | list[int] | set[str] | set[int],
) -> str:
    """Format full ledger statement for the inspected user."""
    full_name = html.escape(user.full_name or "Клиент", quote=False)
    username = f"@{html.escape(user.username, quote=False)}" if user.username else "—"

    lines = [
        f"📜 <b>Выписка операций клиента:</b> {full_name} ({username})",
        f"🆔 <b>Telegram ID:</b> <code>{user.telegram_id}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if not txs:
        lines.append("<i>История операций пуста.</i>")
    else:
        for tx in txs:
            is_debit = tx.sender_account_id in account_ids
            sign = "➖" if is_debit else "➕"
            sign_prefix = "-" if is_debit else "+"
            tx_time = tx.created_at.strftime("%d.%m.%Y %H:%M") if tx.created_at else "—"
            cat = html.escape(tx.category or "Операция", quote=False)
            desc = html.escape(tx.description or "Без описания", quote=False)

            lines.extend([
                f"{sign} <b>{cat}</b>: <b>{sign_prefix}{tx.amount:,.2f} ₽</b>",
                f"   📅 {tx_time} | Чек: <code>{tx.fiscal_code}</code>",
                f"   📝 {desc}",
                "   ─────────────────────────",
            ])

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_admin_audit_logs(logs: list[AdminLog]) -> str:
    """Format recent administrative audit log entries."""
    lines = [
        "📜 <b>Журнал аудита действий администрации</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not logs:
        lines.append("<i>Записей аудита пока нет.</i>")
    else:
        for log in logs:
            log_time = log.created_at.strftime("%d.%m %H:%M") if log.created_at else "—"
            amt_str = f" ({log.amount:+,.2f} ₽)" if log.amount is not None else ""
            reason = html.escape(log.reason or "—", quote=False)
            target = str(log.target_telegram_id or log.target_user_id or "—")
            lines.extend([
                f"• <b>{log_time}</b> | Admin: <code>{log.admin_telegram_id}</code> -> Target: <code>{target}</code>",
                f"  Действие: <b>{log.action}</b>{amt_str}",
                f"  Причина: <i>\"{reason}\"</i>",
                "  ─────────────────────────",
            ])
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_adjustment_confirmation(
    user: User, delta: Decimal, reason: str, before_balance: Decimal
) -> str:
    """Format confirmation dialogue before executing balance adjustment."""
    full_name = html.escape(user.full_name or "Клиент", quote=False)
    username = f"@{html.escape(user.username, quote=False)}" if user.username else "—"
    escaped_reason = html.escape(reason, quote=False)
    new_balance = before_balance + delta
    op_title = "➕ Начисление (Credit)" if delta > 0 else "➖ Списание (Debit)"

    lines = [
        "⚠️ <b>Подтверждение корректировки баланса</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Клиент: <b>{full_name}</b> ({username}, ID: <code>{user.telegram_id}</code>)",
        f"Операция: <b>{op_title}</b>",
        f"Сумма: <b>{abs(delta):.2f} ₽</b>",
        f"Текущий баланс: <b>{before_balance:.2f} ₽</b>",
        f"Баланс после операции: <b>{new_balance:.2f} ₽</b>",
        f"Причина: <i>\"{escaped_reason}\"</i>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "Вы уверены, что хотите выполнить операцию?",
    ]
    return "\n".join(lines)


def format_broadcast_preview(text: str, recipient_count: int) -> tuple[str, str]:
    """Format preview card and confirmation text for announcement broadcast."""
    preview_box = (
        "👀 <b>Предпросмотр сообщения:</b>\n"
        "────────────────────────────────────\n"
        f"{text}\n"
        "────────────────────────────────────"
    )
    est_seconds = max(1, int(recipient_count * 0.04))
    confirm_text = (
        "⚠️ <b>Подтверждение массовой рассылки</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Получателей: <b>{recipient_count:,}</b>\n"
        f"⏱ Примерное время отправки: ~<b>{est_seconds}</b> сек. (~25 сообщ./сек)\n\n"
        "<b>Внимание:</b> после запуска рассылку нельзя отозвать!\n\n"
        "Вы подтверждаете отправку?"
    )
    return preview_box, confirm_text


def format_cheat_menu(target_user: User, main_balance: Decimal | float | None = None) -> str:
    """Format cheat panel dashboard for target user."""
    full_name = html.escape(target_user.full_name or "Клиент", quote=False)
    username = f"@{html.escape(target_user.username, quote=False)}" if target_user.username else "—"
    bal_str = f"{Decimal(str(main_balance)):,.2f} ₽" if main_balance is not None else "—"
    cb_val = getattr(target_user, "cashback_balance", Decimal("0.00"))
    cb_str = f"{Decimal(str(cb_val)):,.2f} ₽"

    lines = [
        "⚡️ <b>Т-Банк: Чит-панель / Накрутка ресурсов</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Клиент: <b>{full_name}</b> ({username})",
        f"🆔 Telegram ID: <code>{target_user.telegram_id}</code>",
        f"💰 Основной счет: <b>{bal_str}</b>",
        f"🎁 Кэшбэк бонусы: <b>{cb_str}</b>",
        "",
        "Выберите желаемую операцию (начисление происходит моментально):",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def format_cooldown_reset_summary(target_user: User, summary: dict) -> str:
    """Format summary report of reset cooldown timers."""
    full_name = html.escape(target_user.full_name or "Клиент", quote=False)
    username = f"@{html.escape(target_user.username, quote=False)}" if target_user.username else "—"
    jobs_cnt = summary.get("jobs_reset", 0)
    rentals_cnt = summary.get("rentals_reset", 0)

    lines = [
        "⏳ <b>Все таймеры и кулдауны успешно сброшены!</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Клиент: <b>{full_name}</b> ({username}, ID: <code>{target_user.telegram_id}</code>)",
        "",
        "🎰 <b>Колесо Фортуны:</b> бесплатный спин доступен сразу",
        f"💼 <b>Смены на работе:</b> перемотаны на 3 часа ({jobs_cnt} смен готово к выплате)",
        f"🏛 <b>Аренда недвижимости:</b> перемотана на 25 часов ({rentals_cnt} объектов готово к выплате)",
        "⚡️ <b>Шабашки (Hustles):</b> кулдаун сброшен, можно выполнять заказ",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def format_grant_menu(target_user: User) -> str:
    """Format asset and stock grant catalog menu."""
    full_name = html.escape(target_user.full_name or "Клиент", quote=False)
    username = f"@{html.escape(target_user.username, quote=False)}" if target_user.username else "—"

    lines = [
        "🎁 <b>Выдача активов и акций (God Mode)</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Получатель: <b>{full_name}</b> ({username}, ID: <code>{target_user.telegram_id}</code>)",
        "",
        "Выберите элитный актив или пакет акций для мгновенного начисления без списания средств:",
        " • <b>Золотой Майбах</b> (+50% зарплата, -30 мин смены)",
        " • <b>BMW M5</b> (+25% зарплата, -15 мин смены)",
        " • <b>Особняк на Рублевке</b> (1 000 000 ₽ / сутки пассивный доход)",
        " • <b>Пентхаус в Сити</b> (300 000 ₽ / сутки пассивный доход)",
        " • <b>Яхта «Непотопляемый кэшбэк»</b> (коллекционный статус)",
        " • <b>+1 000 акций</b> выбранной компании в инвестпортфель",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def format_grant_property_result(target_user: User, item_name: str, category: str, icon: str = "🎁") -> str:
    """Format confirmation of granted property."""
    full_name = html.escape(target_user.full_name or "Клиент", quote=False)
    escaped_item = html.escape(item_name, quote=False)
    cat_names = {
        "CAR": "Автомобиль",
        "REAL_ESTATE": "Недвижимость",
        "LUXURY": "Коллекционный предмет",
    }
    cat_desc = cat_names.get(category, category)

    lines = [
        "🎉 <b>Актив успешно выдан!</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Клиент: <b>{full_name}</b> (ID: <code>{target_user.telegram_id}</code>)",
        f"Категория: <b>{cat_desc}</b>",
        f"Предмет: {icon} <b>{escaped_item}</b>",
        "Стоимость списания: <b>0.00 ₽ (подарок администрации)</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def format_grant_stock_result(
    target_user: User, ticker: str, shares: int, total_shares: int
) -> str:
    """Format confirmation of granted stocks."""
    full_name = html.escape(target_user.full_name or "Клиент", quote=False)
    escaped_ticker = html.escape(ticker, quote=False)

    lines = [
        "📈 <b>Акции успешно начислены!</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Клиент: <b>{full_name}</b> (ID: <code>{target_user.telegram_id}</code>)",
        f"Тикер: <b>{escaped_ticker}</b>",
        f"Начислено: <b>+{shares:,} шт.</b>",
        f"Всего в портфеле: <b>{total_shares:,} шт.</b>",
        "Стоимость покупки: <b>0.00 ₽ (грант Т-Банка)</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def format_market_regulator(stocks: list[Any]) -> str:
    """Format stock exchange regulator overview."""
    lines = [
        "📈 <b>Т-Банк: Регулятор Фондового Рынка (God Mode)</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "Инструменты прямого вмешательства в рыночные котировки:",
        "",
    ]
    for s in stocks:
        ticker = html.escape(getattr(s, "ticker", str(s)), quote=False)
        name = html.escape(getattr(s, "name", ticker), quote=False)
        curr_price = Decimal(str(getattr(s, "current_price", 0)))
        base_price = Decimal(str(getattr(s, "base_price", curr_price)))
        delta = curr_price - base_price
        pct = (delta / base_price * 100) if base_price > 0 else Decimal("0")
        sign = "+" if delta >= 0 else ""
        lines.append(f" • <b>{ticker}</b> ({name}): <b>{curr_price:,.2f} ₽</b> (<i>{sign}{pct:.1f}%</i>)")

    lines.extend([
        "",
        "🚀 <b>Памп рынка (+50%):</b> моментально разгоняет котировки всех акций.",
        "📉 <b>Дамп рынка (-50%):</b> снижает цены на 50% (минимум 1.00 ₽).",
        "💥 <b>Крах биржи (-80%):</b> глубокая коррекция для выгодной закупки.",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ])
    return "\n".join(lines)


def format_market_manipulation_result(action: str, stocks: list[Any]) -> str:
    """Format summary of market-wide price intervention."""
    titles = {
        "PUMP": "🚀 <b>Рыночный Памп (+50%) успешно проведен!</b>",
        "DUMP": "📉 <b>Рыночный Дамп (-50%) успешно проведен!</b>",
        "CRASH": "💥 <b>Крах биржи (-80%) успешно проведен!</b>",
    }
    header = titles.get(action.upper(), f"⚡️ <b>Манипуляция рынком ({html.escape(action, quote=False)}) проведена!</b>")

    lines = [
        header,
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "Обновленные котировки акций Т-Инвестиций:",
        "",
    ]
    for s in stocks:
        ticker = html.escape(getattr(s, "ticker", str(s)), quote=False)
        price = Decimal(str(getattr(s, "current_price", 0)))
        lines.append(f" • <b>{ticker}:</b> <code>{price:,.2f} ₽</code>")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_stock_manipulation_result(stock: Any, action_or_price: str) -> str:
    """Format target stock price modification status."""
    ticker = html.escape(getattr(stock, "ticker", str(stock)), quote=False)
    name = html.escape(getattr(stock, "name", ticker), quote=False)
    price = Decimal(str(getattr(stock, "current_price", 0)))

    lines = [
        f"🎯 <b>Управление акцией {ticker}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Компания: <b>{name}</b>",
        f"Текущая цена: <b>{price:,.2f} ₽</b>",
        f"Статус операции: <b>{html.escape(action_or_price, quote=False)}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "Выберите действие или целевую цену:",
    ]
    return "\n".join(lines)


def format_cleanup_confirmation() -> str:
    """Format test user cleanup confirmation prompt."""
    lines = [
        "🧹 <b>Очистка тестовых пользователей и мок-данных</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <b>Внимание!</b> Будут безвозвратно удалены:",
        " • Пользователи с Telegram ID: <code>123</code>, <code>8881001</code>, <code>8881002</code>, <code>999888777</code>",
        " • Пользователи с именами <code>test_*</code> и <code>mock_*</code>",
        " • Связанные счета, карты, транзакции, выписки, работы и имущество",
        "",
        "🛡 <b>Защита администратора:</b>",
        " • Аккаунт владельца (ID: <code>1302123809</code>) и реальные пользователи защищены от удаления.",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "Вы подтверждаете удаление тестовых данных?",
    ]
    return "\n".join(lines)


def format_cleanup_result(summary: dict[str, int]) -> str:
    """Format execution summary after cleaning test users."""
    lines = [
        "✅ <b>Очистка тестовых данных успешно завершена!</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"👥 Удалено пользователей: <code>{summary.get('users', 0)}</code>",
        f"💳 Удалено карт: <code>{summary.get('cards', 0)}</code>",
        f"💰 Удалено счетов: <code>{summary.get('accounts', 0)}</code>",
        f"📜 Удалено транзакций: <code>{summary.get('transactions', 0)}</code>",
        f"💼 Удалено работ: <code>{summary.get('jobs', 0)}</code>",
        f"🏛 Удалено имущества: <code>{summary.get('properties', 0)}</code>",
        f"📈 Удалено инвестпортфелей: <code>{summary.get('portfolios', 0)}</code>",
        f"📑 Удалено записей аудита: <code>{summary.get('admin_logs', 0)}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "🛡 <i>Все данные реальных клиентов сохранены.</i>",
    ]
    return "\n".join(lines)

