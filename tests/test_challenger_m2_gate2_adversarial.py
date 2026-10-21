"""Adversarial Empirical Verification Suite for Milestone 2 Gate 2.

Tests router idempotency under high churn, HTML escaping edge cases across all formatters,
receipts, analytics, push notifications, and FSM handlers.
"""

import html
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.bot.bot import create_dispatcher, create_bot
from app.bot.formatters.cards import format_card_view
from app.services.receipts import ReceiptService
from app.database.models import Transaction, User, Account, Card
from app.bot.routers.transfer import handle_confirm_transfer
from app.bot.states.states import TransferStates
from app.services.banking import BankingService


class TestChallengerGate2DispatcherStress:
    """Stress-test dispatcher creation and router detachment idempotency."""

    def test_dispatcher_creation_stress_loop(self):
        """Repeatedly create dispatchers 25 times to guarantee zero state pollution or attachment crashes."""
        for i in range(25):
            dp = create_dispatcher()
            assert dp is not None
            sub_router_names = [r.name for r in dp.sub_routers]
            assert "common_router" in sub_router_names
            assert "card_router" in sub_router_names
            assert "transfer_router" in sub_router_names
            assert "savings_router" in sub_router_names
            assert "invest_router" in sub_router_names
            assert "cashback_router" in sub_router_names
            assert "oleg_router" in sub_router_names
            assert len(sub_router_names) >= 7


class TestChallengerGate2HTMLEscapingStress:
    """Empirically test HTML injection vectors across all UI formatters."""

    ADVERSARIAL_NAMES = [
        "Ivan <VIP> & Co",
        "<b>Bold</b> & <i>Italic</i>",
        "\"Quotes\" & 'Single'",
        "<script>alert('XSS')</script>",
        "<a href='https://bank.tbank.ru'>link</a>",
        "Emoji 🔥 🟡 & <tag>",
        "A" * 100,  # Extreme length string
        "<tg-spoiler>Secret Account</tg-spoiler>",
        "Slash/Backslash\\&Amp;And<Tag>",
        "Special chars: & < > \" ' ` $ % # @ !",
    ]

    def test_card_formatter_html_escaping_and_layout(self):
        """Verify card formatter escapes all adversarial strings and maintains box borders."""
        for name in self.ADVERSARIAL_NAMES:
            card_data = {
                "card_number": "2200701234567890",
                "cvv": "123",
                "expiry_date": "12/28",
                "full_name": name,
                "is_frozen": False,
            }
            formatted = format_card_view(card_data, balance=Decimal("12345.67"))

            # Must contain pre tags
            assert "<pre>" in formatted
            assert "</pre>" in formatted

            pre_content = formatted.split("<pre>")[1].split("</pre>")[0]
            # None of the dangerous raw HTML tags should be present unescaped
            for tag in ["<VIP>", "<script>", "<b>", "<i>", "<a ", "<tg-spoiler>", "<tag>"]:
                assert tag not in pre_content, f"Unescaped tag {tag} found in card view!"
                assert tag.lower() not in pre_content, f"Unescaped lowercase tag {tag} found!"

            # Check that '&' is escaped as '&amp;'
            if "&" in name:
                assert "&amp;" in pre_content

            # Check box lines structure
            lines = pre_content.strip().split("\n")
            assert len(lines) == 7
            assert lines[0].startswith("┌") and lines[0].endswith("┐")
            assert lines[-1].startswith("└") and lines[-1].endswith("┘")

    def test_receipt_formatter_html_escaping(self):
        """Verify ReceiptService.format_receipt properly escapes all transaction fields."""
        for name in self.ADVERSARIAL_NAMES:
            tx = Transaction(
                id="00000000-1111-2222-3333-444444444444",
                amount=Decimal("999.99"),
                fee=Decimal("1.50"),
                category=name,
                description=name,
                fiscal_code=f"ФП-{name}",
                auth_code=f"AUTH-{name}",
            )
            sender_u = User(full_name=name, username=name.replace(" ", "_"))
            recip_u = User(full_name=name, username=name.replace(" ", "_"))

            receipt = ReceiptService.format_receipt(
                tx, sender_user=sender_u, recipient_user=recip_u, cashback_earned=Decimal("50.00")
            )

            for tag in ["<VIP>", "<script>", "<b>Bold</b>", "<i>", "<a ", "<tg-spoiler>", "<tag>"]:
                assert tag not in receipt, f"Unescaped tag {tag} found in receipt!"

            # Valid expected markup must remain valid
            assert "<b>КВИТАНЦИЯ ОБ ОПЕРАЦИИ" in receipt
            assert "<code>" in receipt
            assert "</code>" in receipt

    @pytest.mark.asyncio
    async def test_analytics_formatter_html_escaping(self, async_session, sample_user):
        """Verify ReceiptService.get_expense_analytics escapes category names and Oleg remarks."""
        main_acc = await BankingService.get_user_main_account(async_session, sample_user.id)
        assert main_acc is not None

        # Add transaction with malicious category name
        tx = Transaction(
            sender_account_id=main_acc.id,
            recipient_account_id=None,
            amount=Decimal("777.00"),
            category="Рестораны & <VIP-Клуб>",
            description="Обед <Суши>",
            tx_type="TRANSFER_OUT",
            fiscal_code="ФП-1234567890",
            auth_code="AUTH-123456",
        )
        async_session.add(tx)
        await async_session.commit()

        analytics = await ReceiptService.get_expense_analytics(async_session, sample_user.id)
        report = analytics["formatted_report"]

        assert "<VIP-Клуб>" not in report
        assert "&lt;VIP-Клуб&gt;" in report
        assert "Рестораны &amp;" in report
        assert "<b>Аналитика трат" in report

    @pytest.mark.asyncio
    async def test_transfer_push_alert_html_escaping(
        self,
        mock_bot,
        callback_factory,
        fsm_context_factory,
        async_session,
        sample_user,
        sample_recipient,
    ):
        """Verify transfer push alert and UI confirmation handles special characters."""
        sample_user.full_name = "Super <Admin> & Co"
        sample_user.username = "super<adm>"
        sample_recipient.full_name = "Lucky <Winner> & Friend"
        sample_recipient.username = "winner<best>"
        await async_session.commit()

        rec_acc = await BankingService.get_user_main_account(async_session, sample_recipient.id)
        state = fsm_context_factory(sample_user.telegram_id)
        await state.set_state(TransferStates.confirm)
        await state.update_data(
            recipient_account_id=rec_acc.id,
            recipient_user_id=sample_recipient.id,
            recipient_telegram_id=sample_recipient.telegram_id,
            recipient_full_name=sample_recipient.full_name,
            recipient_username=f"@{sample_recipient.username}",
            amount="120.00",
        )

        cb = callback_factory("trf:confirm", user_id=sample_user.telegram_id)
        await handle_confirm_transfer(cb, session=async_session, user=sample_user, state=state)

        # Check mock_bot send_message call
        last_call = mock_bot.send_message.call_args_list[-1]
        push_msg = last_call.kwargs["text"]

        assert "<Admin>" not in push_msg
        assert "&lt;Admin&gt;" in push_msg
        assert "&amp;" in push_msg
        assert "super&lt;adm&gt;" in push_msg
        assert "💸 <b>Входящий перевод через СБП!</b>" in push_msg

    def test_telegram_html_strict_parser_conformance(self):
        """Parse all formatted strings through strict HTML parser enforcing Telegram HTML grammar."""
        from html.parser import HTMLParser

        ALLOWED_TELEGRAM_TAGS = {
            "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "span",
            "tg-spoiler", "a", "code", "pre", "tg-emoji"
        }

        class TelegramHTMLValidator(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=False)
                self.tag_stack = []
                self.illegal_tags = []

            def handle_starttag(self, tag, attrs):
                if tag.lower() not in ALLOWED_TELEGRAM_TAGS:
                    self.illegal_tags.append(tag)
                self.tag_stack.append(tag.lower())

            def handle_endtag(self, tag):
                if self.tag_stack and self.tag_stack[-1] == tag.lower():
                    self.tag_stack.pop()
                elif tag.lower() in self.tag_stack:
                    while self.tag_stack and self.tag_stack[-1] != tag.lower():
                        self.tag_stack.pop()
                    if self.tag_stack:
                        self.tag_stack.pop()

        # Test against formatted card view
        card_out = format_card_view(
            {"full_name": "Ivan <VIP> & Sons <script>", "card_number": "2200701234567890"},
            balance=Decimal("500.00")
        )
        validator = TelegramHTMLValidator()
        validator.feed(card_out)
        assert not validator.illegal_tags, f"Illegal tags detected: {validator.illegal_tags}"
        assert not validator.tag_stack, f"Unclosed tags detected: {validator.tag_stack}"

        # Test against formatted receipt
        tx = Transaction(
            id="12345678-0000-0000-0000-000000000000",
            amount=Decimal("120.00"),
            fee=Decimal("0.00"),
            category="Кафе & <Рестораны>",
            description="Кофе & <Круассан>",
            fiscal_code="ФП-123456",
            auth_code="AUTH-7890",
        )
        u1 = User(full_name="Boss <VIP>", username="vip<boss>")
        u2 = User(full_name="Worker <Hero>", username="hero<job>")
        receipt_out = ReceiptService.format_receipt(tx, sender_user=u1, recipient_user=u2)
        validator2 = TelegramHTMLValidator()
        validator2.feed(receipt_out)
        assert not validator2.illegal_tags, f"Illegal tags detected in receipt: {validator2.illegal_tags}"
        assert not validator2.tag_stack, f"Unclosed tags detected in receipt: {validator2.tag_stack}"

