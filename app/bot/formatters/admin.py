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
