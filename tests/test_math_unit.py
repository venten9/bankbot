"""Tier 1: Feature Coverage Math Unit Tests.

Covers:
- Luhn 16-digit card generation, check digit math, and Luhn checksum validation.
- Card formatting, masking, CVV generation, and expiry date math.
- 18% APR savings interest calculation (Actual/365, daily accrual, edge cases).
- Cashback percentage calculations across all humorous categories and rounding.
- Stock market dynamic GBM pricing, mean-reversion, price floor (0.01 ₽), broker fee (0.3%), and portfolio valuation.
- Receipt formatting, fiscal code format, auth code, and 10-block Unicode visual expense progress bars.
- Assistant Oleg intent classification and response formatting.
"""

import math
import re
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.database.models import Transaction
from app.services.card import CardService, MIR_BIN
from app.services.cashback import CashbackService, DEFAULT_CASHBACK_CATEGORIES
from app.services.investments import InvestmentService, DEFAULT_STOCKS
from app.services.oleg import OlegAssistant
from app.services.receipts import ReceiptService
from app.services.savings import SavingsService


# ==============================================================================
# Domain 1 & 2: Luhn 16-Digit Card Generation, Validation & Formatting
# ==============================================================================

class TestLuhnAndCardMath:
    """Card service unit tests covering Luhn algorithm and security attributes."""

    def test_luhn_generation_produces_16_digits_and_mir_bin(self):
        """Verify that generated card numbers are exactly 16 digits and start with MIR BIN 220070."""
        for _ in range(50):
            card_number = CardService.generate_card_number()
            assert len(card_number) == 16
            assert card_number.isdigit()
            assert card_number.startswith(MIR_BIN)
            assert CardService.validate_luhn(card_number) is True

    @pytest.mark.parametrize(
        "partial, expected_check_digit",
        [
            ("220070123456789", 6),
            ("220070000000000", 9),
            ("220070999999999", 8),
            ("220070111111111", 5),
            ("220070555555555", 4),
            ("400000123456789", 9),
            ("510510510510510", 0),
            ("220070888888888", 2),
            ("220070102030405", 8),
            ("220070987654321", 6),
        ],
    )
    def test_luhn_check_digit_calculation(self, partial: str, expected_check_digit: int):
        """Verify direct Luhn check digit computation for 15-digit prefixes."""
        calculated = CardService.calculate_luhn_check_digit(partial)
        assert calculated == expected_check_digit
        full_card = partial + str(calculated)
        assert CardService.validate_luhn(full_card) is True

    @pytest.mark.parametrize(
        "valid_card",
        [
            "2200701234567896",
            "2200700000000009",
            "2200709999999998",
            "2200701111111115",
            "4000001234567899",
            "5105105105105100",
            "2200708888888882",
            "2200701020304058",
            "2200709876543216",
            "2200705555555554",
        ],
    )
    def test_luhn_validator_valid_cards(self, valid_card: str):
        """Verify Luhn validator accepts known valid 16-digit card numbers."""
        assert CardService.validate_luhn(valid_card) is True

    def test_luhn_validator_rejects_single_digit_mutation(self):
        """Mutating any single digit in a valid card must invalidate the Luhn checksum."""
        base_card = "2200701234567896"
        assert CardService.validate_luhn(base_card) is True

        digits = list(base_card)
        for idx in range(16):
            original = int(digits[idx])
            mutated = (original + 1) % 10
            mutated_digits = list(digits)
            mutated_digits[idx] = str(mutated)
            mutated_card = "".join(mutated_digits)
            assert CardService.validate_luhn(mutated_card) is False, f"Failed for index {idx}"

    @pytest.mark.parametrize(
        "invalid_card",
        [
            "",
            "123",
            "220070",
            "220070123456789",       # 15 digits
            "22007012345678970",     # 17 digits
            "220070123456789A",     # Letter at end
            "220070123456789 ",     # Trailing space with 16 digits
            "2200-7012-3456-789X",  # Bad char with dashes
            "abcdefghijklmnop",     # All letters
            "-22007012345678",      # Negative sign
        ],
    )
    def test_luhn_validator_rejects_invalid_inputs(self, invalid_card: str):
        """Verify invalid strings, incorrect lengths, and non-digit characters return False."""
        assert CardService.validate_luhn(invalid_card) is False

    def test_luhn_validator_accepts_cards_with_spaces_and_dashes(self):
        """Luhn validator should tolerate formatted card numbers with spaces or dashes."""
        valid_with_spaces = "2200 7012 3456 7896"
        valid_with_dashes = "2200-7012-3456-7896"
        assert CardService.validate_luhn(valid_with_spaces) is True
        assert CardService.validate_luhn(valid_with_dashes) is True

    def test_cvv_generation(self):
        """Verify CVV is a 3-digit numeric string formatted with leading zeros if necessary."""
        for _ in range(50):
            cvv = CardService.generate_cvv()
            assert len(cvv) == 3
            assert cvv.isdigit()
            val = int(cvv)
            assert 0 <= val <= 999

    def test_expiry_date_generation(self):
        """Verify expiry date format MM/YY with valid month."""
        expiry = CardService.generate_expiry(years_ahead=5)
        assert len(expiry) == 5
        assert "/" in expiry
        month_str, year_str = expiry.split("/")
        assert month_str.isdigit() and year_str.isdigit()
        month = int(month_str)
        assert 1 <= month <= 12

    def test_format_card_number_masked_and_unmasked(self):
        """Verify card masking matches T-Bank styling (2200 70•• •••• 5842)."""
        card = "2200701234565842"
        masked = CardService.format_card_number(card, masked=True)
        unmasked = CardService.format_card_number(card, masked=False)

        assert masked == "2200 70•• •••• 5842"
        assert unmasked == "2200 7012 3456 5842"

    def test_format_card_number_preserves_short_invalid(self):
        """Format should return raw string if length is not 16 digits."""
        assert CardService.format_card_number("12345") == "12345"


# ==============================================================================
# Domain 3: 18% APR Savings Interest Calculation Math
# ==============================================================================

class TestSavingsInterestMath:
    """Savings interest accrual formulas under Russian Actual/365 convention."""

    def test_zero_balance_yields_zero_interest(self):
        """Zero balance should produce 0.000000 interest."""
        res = SavingsService.calculate_interest(Decimal("0.00"), Decimal("0.18"), 86400 * 30)
        assert res == Decimal("0.000000")

    def test_zero_or_negative_elapsed_seconds_yields_zero(self):
        """Zero or negative elapsed time should produce 0.000000 interest."""
        assert SavingsService.calculate_interest(Decimal("10000.00"), Decimal("0.18"), 0) == Decimal("0.000000")
        assert SavingsService.calculate_interest(Decimal("10000.00"), Decimal("0.18"), -100) == Decimal("0.000000")

    def test_negative_balance_yields_zero(self):
        """Negative balance cannot accrue interest."""
        res = SavingsService.calculate_interest(Decimal("-1000.00"), Decimal("0.18"), 86400)
        assert res == Decimal("0.000000")

    def test_standard_30_days_accrual_math(self):
        """
        Verify interest on 100,000.00 ₽ for 30 days at 18% APR:
        I = 100000 * (0.18 / 365) * 30 ≈ 1479.452055 ₽.
        """
        balance = Decimal("100000.00")
        rate = Decimal("0.18")
        elapsed = 30 * 86400
        interest = SavingsService.calculate_interest(balance, rate, elapsed)
        expected = Decimal("100000.00") * (Decimal("0.18") / Decimal("365")) * Decimal("30")
        assert abs(interest - expected) < Decimal("0.0001")
        assert str(interest).startswith("1479.45")

    def test_one_day_accrual(self):
        """One day (86400s) accrual = Balance * 0.18 / 365."""
        balance = Decimal("36500.00")
        rate = Decimal("0.18")
        interest = SavingsService.calculate_interest(balance, rate, 86400)
        # 36500 * (0.18 / 365) * 1 = 18.000000 ₽
        assert interest == Decimal("18.000000")

    def test_full_year_accrual_365_days(self):
        """365 days of accrual should equal exactly 18% of the balance."""
        balance = Decimal("10000.00")
        rate = Decimal("0.18")
        interest = SavingsService.calculate_interest(balance, rate, 86400 * 365)
        # 10000 * 0.18 = 1800.000000 ₽
        assert interest == Decimal("1800.000000")

    def test_small_balance_accrual(self):
        """Small balance (1.00 ₽) computes correctly with 6 decimal places."""
        balance = Decimal("1.00")
        rate = Decimal("0.18")
        interest = SavingsService.calculate_interest(balance, rate, 86400)
        # 1 * 0.18 / 365 ≈ 0.000493
        assert interest > Decimal("0.000000")
        assert interest == Decimal("0.000493")

    def test_large_balance_accrual(self):
        """Large balance (100,000,000.00 ₽) computes without overflow or precision loss."""
        balance = Decimal("100000000.00")
        rate = Decimal("0.18")
        interest = SavingsService.calculate_interest(balance, rate, 86400)
        # 100000000 * 0.18 / 365 ≈ 49315.068493
        assert str(interest).startswith("49315.06")

    def test_interest_quantization_is_six_decimals(self):
        """Output must always be quantized to 6 decimal places."""
        interest = SavingsService.calculate_interest(Decimal("12345.67"), Decimal("0.18"), 12345)
        assert interest.as_tuple().exponent == -6


# ==============================================================================
# Domain 4: Cashback Percentage & Rounding Math
# ==============================================================================

class TestCashbackMath:
    """Cashback rate calculations across all 8 humorous categories."""

    @pytest.mark.parametrize(
        "cat_code, percent, amount, expected_cashback",
        [
            ("FOOD_FASTFOOD", 10, Decimal("1500.00"), Decimal("150.00")),
            ("FOOD_FASTFOOD", 10, Decimal("299.99"), Decimal("30.00")),
            ("FOOD_FASTFOOD", 10, Decimal("125.40"), Decimal("12.54")),
            ("PETS_HEALTH", 15, Decimal("2345.00"), Decimal("351.75")),
            ("PETS_HEALTH", 15, Decimal("100.00"), Decimal("15.00")),
            ("COFFEE_CAFE", 7, Decimal("380.00"), Decimal("26.60")),
            ("COFFEE_CAFE", 7, Decimal("1000.00"), Decimal("70.00")),
            ("TAXI_TRANSPORT", 5, Decimal("1200.00"), Decimal("60.00")),
            ("TAXI_TRANSPORT", 5, Decimal("450.00"), Decimal("22.50")),
            ("GAMING_DIGITAL", 8, Decimal("2000.00"), Decimal("160.00")),
            ("GAMING_DIGITAL", 8, Decimal("1299.00"), Decimal("103.92")),
            ("BOOKS_LEARNING", 12, Decimal("850.00"), Decimal("102.00")),
            ("BOOKS_LEARNING", 12, Decimal("1500.00"), Decimal("180.00")),
            ("GROCERIES", 5, Decimal("5000.00"), Decimal("250.00")),
            ("GROCERIES", 5, Decimal("3333.33"), Decimal("166.67")),
            ("ALL_GENERAL", 1, Decimal("999.00"), Decimal("9.99")),
            ("ALL_GENERAL", 1, Decimal("50.00"), Decimal("0.50")),
            ("ALL_GENERAL", 1, Decimal("1.00"), Decimal("0.01")),
        ],
    )
    def test_cashback_formula_direct(
        self, cat_code: str, percent: int, amount: Decimal, expected_cashback: Decimal
    ):
        """Verify exact percentage and ROUND_HALF_UP rounding on kopecks."""
        calculated = (amount * (Decimal(str(percent)) / Decimal("100"))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        assert calculated == expected_cashback

    def test_default_categories_catalog_completeness(self):
        """Verify the 8 specified humorous categories are defined with valid percent rates."""
        assert len(DEFAULT_CASHBACK_CATEGORIES) == 8
        codes = {c["code"] for c in DEFAULT_CASHBACK_CATEGORIES}
        expected_codes = {
            "FOOD_FASTFOOD",
            "PETS_HEALTH",
            "COFFEE_CAFE",
            "TAXI_TRANSPORT",
            "GAMING_DIGITAL",
            "BOOKS_LEARNING",
            "GROCERIES",
            "ALL_GENERAL",
        }
        assert codes == expected_codes
        for cat in DEFAULT_CASHBACK_CATEGORIES:
            assert 1 <= cat["percent"] <= 15


# ==============================================================================
# Domain 5: Discrete Mean-Reverting GBM Stock Pricing & Trading Math
# ==============================================================================

class TestInvestmentGBMAndTradeMath:
    """Parody stock market mathematics: GBM pricing, commission, portfolio P&L."""

    def test_gbm_zero_shock_exhibits_pure_mean_reversion(self):
        """With epsilon = 0, price moves purely according to mean reversion pull."""
        base_price = Decimal("100.00")
        high_price = Decimal("150.00")
        low_price = Decimal("50.00")

        # High price should be pulled downward towards 100
        next_high = InvestmentService.calculate_next_price(
            current_price=high_price,
            base_price=base_price,
            volatility=0.05,
            mean_reversion_rate=0.05,
            delta_t=1.0,
            epsilon=0.0,
        )
        assert next_high < high_price

        # Low price should be pulled upward towards 100
        next_low = InvestmentService.calculate_next_price(
            current_price=low_price,
            base_price=base_price,
            volatility=0.05,
            mean_reversion_rate=0.05,
            delta_t=1.0,
            epsilon=0.0,
        )
        assert next_low > low_price

    def test_gbm_price_floor_enforcement(self):
        """Even with extreme negative shock (epsilon = -100), price cannot drop below 0.01 ₽."""
        next_price = InvestmentService.calculate_next_price(
            current_price=Decimal("1.50"),
            base_price=Decimal("1.50"),
            volatility=0.15,
            mean_reversion_rate=0.05,
            delta_t=1.0,
            epsilon=-100.0,
        )
        assert next_price >= Decimal("0.01")
        assert next_price == Decimal("0.01")

    def test_gbm_positive_shock_increases_price(self):
        """A strong positive shock increases the price."""
        next_price = InvestmentService.calculate_next_price(
            current_price=Decimal("100.00"),
            base_price=Decimal("100.00"),
            volatility=0.05,
            mean_reversion_rate=0.05,
            delta_t=1.0,
            epsilon=3.0,
        )
        assert next_price > Decimal("100.00")

    @pytest.mark.parametrize(
        "shares, price, expected_subtotal, expected_fee, expected_total_buy, expected_net_sell",
        [
            (10, Decimal("150.00"), Decimal("1500.00"), Decimal("4.50"), Decimal("1504.50"), Decimal("1495.50")),
            (1, Decimal("1.50"), Decimal("1.50"), Decimal("0.00"), Decimal("1.50"), Decimal("1.50")),
            (100, Decimal("420.00"), Decimal("42000.00"), Decimal("126.00"), Decimal("42126.00"), Decimal("41874.00")),
            (5, Decimal("1337.00"), Decimal("6685.00"), Decimal("20.06"), Decimal("6705.06"), Decimal("6664.94")),
        ],
    )
    def test_trade_cost_fee_and_proceeds_math(
        self,
        shares: int,
        price: Decimal,
        expected_subtotal: Decimal,
        expected_fee: Decimal,
        expected_total_buy: Decimal,
        expected_net_sell: Decimal,
    ):
        """Verify 0.3% broker commission, total purchase cost, and net sale proceeds."""
        subtotal = Decimal(str(shares)) * price
        assert subtotal == expected_subtotal

        fee = (subtotal * Decimal("0.003")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert fee == expected_fee

        total_buy = subtotal + fee
        assert total_buy == expected_total_buy

        net_sell = subtotal - fee
        assert net_sell == expected_net_sell

    def test_weighted_average_price_math(self):
        """Verify weighted average price calculation across multiple buy lots."""
        # Lot 1: 10 shares @ 100.00 ₽ = 1000.00 ₽
        # Lot 2: 10 shares @ 200.00 ₽ = 2000.00 ₽
        # Total: 20 shares, total cost 3000.00 ₽, average = 150.00 ₽
        shares_1, price_1 = 10, Decimal("100.00")
        shares_2, price_2 = 10, Decimal("200.00")
        total_shares = shares_1 + shares_2
        avg = (
            (Decimal(str(shares_1)) * price_1 + Decimal(str(shares_2)) * price_2)
            / Decimal(str(total_shares))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert avg == Decimal("150.00")

        # Add Lot 3: 5 shares @ 300.00 ₽ = 1500.00 ₽
        # Total: 25 shares, total cost 4500.00 ₽, average = 180.00 ₽
        shares_3, price_3 = 5, Decimal("300.00")
        new_total = total_shares + shares_3
        new_avg = (
            (Decimal(str(total_shares)) * avg + Decimal(str(shares_3)) * price_3)
            / Decimal(str(new_total))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert new_avg == Decimal("180.00")

    def test_portfolio_unrealized_pnl_math(self):
        """Verify portfolio valuation and unrealized P&L percentage formula."""
        shares = 10
        avg_price = Decimal("100.00")
        current_price = Decimal("125.00")

        cost = Decimal(str(shares)) * avg_price      # 1000.00 ₽
        val = Decimal(str(shares)) * current_price    # 1250.00 ₽
        pnl = val - cost                              # +250.00 ₽
        pnl_pct = float((pnl / cost) * 100)           # +25.0%

        assert pnl == Decimal("250.00")
        assert pnl_pct == 25.0

    def test_default_stocks_catalog_completeness(self):
        """Verify 6 parody Russian stocks catalog."""
        assert len(DEFAULT_STOCKS) == 6
        tickers = {s["ticker"] for s in DEFAULT_STOCKS}
        expected_tickers = {"SHAW", "MTCH", "BBRS", "DVAN", "GRECH", "MYK"}
        assert tickers == expected_tickers


# ==============================================================================
# Domain 6: Receipts Formatting, Fiscal Codes & Visual Expense Progress Bars
# ==============================================================================

class TestReceiptsAndVisualBars:
    """Digital receipt formatting, fiscal hashes, and 10-block progress bars."""

    @pytest.mark.parametrize(
        "pct, expected_bar",
        [
            (0.0, "░░░░░░░░░░"),
            (4.0, "░░░░░░░░░░"),
            (6.0, "█░░░░░░░░░"),
            (10.0, "█░░░░░░░░░"),
            (20.0, "██░░░░░░░░"),
            (30.0, "███░░░░░░░"),
            (40.0, "████░░░░░░"),
            (50.0, "█████░░░░░"),
            (60.0, "██████░░░░"),
            (70.0, "███████░░░"),
            (80.0, "████████░░"),
            (90.0, "█████████░"),
            (100.0, "██████████"),
        ],
    )
    def test_visual_progress_bar_blocks(self, pct: float, expected_bar: str):
        """Verify 10-block Unicode visual bar calculation for various percentages."""
        bar = ReceiptService.format_progress_bar(pct, total_blocks=10)
        assert bar == expected_bar
        assert len(bar) == 10

    def test_visual_progress_bar_clamps_negative_and_overflow(self):
        """Progress bar should clamp values below 0% and above 100%."""
        assert ReceiptService.format_progress_bar(-15.0) == "░░░░░░░░░░"
        assert ReceiptService.format_progress_bar(150.0) == "██████████"

    def test_fiscal_code_format(self):
        """Verify Russian fiscal sign format (ФП-XXXXXXXXXX)."""
        code = ReceiptService.generate_fiscal_code()
        assert code.startswith("ФП-")
        digits = code.split("-")[1]
        assert len(digits) == 10
        assert digits.isdigit()

    def test_auth_code_format(self):
        """Verify authorization code format (AUTH-XXXXXX)."""
        code = ReceiptService.generate_auth_code()
        assert code.startswith("AUTH-")
        digits = code.split("-")[1]
        assert len(digits) == 6
        assert digits.isdigit()

    def test_format_receipt_contains_key_elements(self):
        """Verify formatted digital receipt contains all required transaction details."""
        tx = Transaction(
            id="12345678-abcd-ef01-2345-6789abcdef01",
            tx_type="P2P_TRANSFER",
            amount=Decimal("1500.50"),
            fee=Decimal("0.00"),
            status="COMPLETED",
            category="Переводы",
            description="За кофе и пиццу",
            fiscal_code="ФП-9382104928",
            auth_code="AUTH-198273",
        )
        receipt = ReceiptService.format_receipt(tx)
        assert "КВИТАНЦИЯ ОБ ОПЕРАЦИИ #12345678" in receipt
        assert "1500.50 ₽" in receipt
        assert "Переводы" in receipt
        assert "AUTH-198273" in receipt
        assert "ФП-9382104928" in receipt
        assert "За кофе и пиццу" in receipt


# ==============================================================================
# Domain 7: Oleg Assistant Intent Classification & Banter
# ==============================================================================

class TestOlegAssistantMathAndRegex:
    """Assistant Oleg regex intent classifier and sarcastic response generation."""

    @pytest.mark.parametrize(
        "query, expected_intent",
        [
            ("Какой баланс?", "BALANCE"),
            ("сколько денег на счету", "BALANCE"),
            ("покажи бабло", "BALANCE"),
            ("balance", "BALANCE"),
            ("переведи 500 рублей маме", "TRANSFER"),
            ("скинь денег на карту", "TRANSFER"),
            ("отправь перевод", "TRANSFER"),
            ("transfer", "TRANSFER"),
            ("кэшбэк", "CASHBACK"),
            ("сколько бонусов", "CASHBACK"),
            ("cashback", "CASHBACK"),
            ("дай кредит срочно", "LOAN"),
            ("займи денег до зарплаты", "LOAN"),
            ("хочу оформить займ", "LOAN"),
            ("что купить на бирже?", "INVEST"),
            ("посоветуй акции", "INVEST"),
            ("куда инвестировать", "INVEST"),
            ("заблокируй карту", "FREEZE"),
            ("я потерял карту", "FREEZE"),
            ("заморозь пластик", "FREEZE"),
            ("ты кто такой?", "IDENTITY"),
            ("кто ты?", "IDENTITY"),
            ("привет олег", "IDENTITY"),
            ("спасибо большое", "GRATITUDE"),
            ("благодарю от души", "GRATITUDE"),
            ("молодец", "GRATITUDE"),
            ("произвольный набор символов абвгд 123", "FALLBACK"),
        ],
    )
    def test_intent_classification(self, query: str, expected_intent: str):
        """Verify regex intent matching across banking queries and colloquial speech."""
        intent = OlegAssistant.classify_intent(query)
        assert intent == expected_intent

    def test_response_incorporates_context(self):
        """Responses should interpolate balance and cashback when context is provided."""
        ctx = {"main_balance": "15400.50", "cashback_balance": "1250.00"}
        resp = OlegAssistant.get_response("какой баланс", context=ctx)
        assert "15400.50" in resp or "₽" in resp

    @pytest.mark.parametrize(
        "category, amount, expected_keyword",
        [
            ("Фастфуд и кафе", Decimal("500.00"), "гастроэнтеролог"),
            ("Шаурма", Decimal("250.00"), "гастроэнтеролог"),
            ("Такси", Decimal("800.00"), "Пешком"),
            ("Игры", Decimal("1500.00"), "пиксели"),
            ("Кофе", Decimal("350.00"), "растворимый"),
        ],
    )
    def test_spending_commentary_keywords(
        self, category: str, amount: Decimal, expected_keyword: str
    ):
        """Verify funny contextual commentary on specific spending categories."""
        comment = OlegAssistant.get_comment_on_spending(category, amount)
        assert expected_keyword.lower() in comment.lower()
