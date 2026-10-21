"""Banking Service: User Onboarding, Multi-Account Management, and Financial Overview."""

import datetime
from decimal import Decimal
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Account, Card, InvestmentPortfolio, SavingsAccount, Stock, User
from app.services.card import CardService


class BankingService:
    """Core neobank service for client onboarding, accounts, and portfolio aggregation."""

    @classmethod
    async def get_or_create_user(
        cls,
        session: AsyncSession,
        telegram_id: int,
        username: str | None = None,
        full_name: str = "Т-Банк Клиент",
        phone_number: str | None = None,
    ) -> tuple[User, Account, Card]:
        """Retrieve existing user or register a new one with a Main account, Luhn card, and Savings."""
        # 1. Check if user already exists
        query = (
            select(User)
            .where(User.telegram_id == telegram_id)
            .options(
                selectinload(User.accounts),
                selectinload(User.cards),
                selectinload(User.savings_account),
            )
        )
        result = await session.execute(query)
        user = result.scalars().first()

        if user:
            # Update mutable metadata if changed
            updated = False
            if username and user.username != username:
                user.username = username
                updated = True
            if full_name and user.full_name != full_name and full_name != "Т-Банк Клиент":
                user.full_name = full_name
                updated = True
            if phone_number and user.phone_number != phone_number:
                user.phone_number = phone_number
                updated = True

            # Ensure main account exists
            main_acc = next((a for a in user.accounts if a.account_type == "MAIN"), None)
            if not main_acc:
                main_acc = Account(
                    user_id=user.id,
                    account_type="MAIN",
                    currency="RUB",
                    balance=Decimal("0.00"),
                )
                session.add(main_acc)
                await session.flush()
                user.accounts.append(main_acc)

            # Ensure card exists
            card = user.cards[0] if user.cards else None
            if not card:
                card = await cls._issue_card(session, user.id, main_acc.id, user.full_name)
                user.cards.append(card)

            # Ensure savings account exists
            if not user.savings_account:
                savings = SavingsAccount(
                    user_id=user.id,
                    balance=Decimal("0.00"),
                    annual_rate=Decimal("0.1800"),
                    accrued_interest=Decimal("0.000000"),
                    last_interest_calc=datetime.datetime.now(),
                )
                session.add(savings)
                await session.flush()
                user.savings_account = savings

            if updated:
                await session.flush()

            return user, main_acc, card

        # 2. Register brand new user
        new_user = User(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name or "Т-Банк Клиент",
            phone_number=phone_number,
            cashback_balance=Decimal("0.00"),
        )
        session.add(new_user)
        await session.flush()

        # Create Main Card Account
        main_account = Account(
            user_id=new_user.id,
            account_type="MAIN",
            currency="RUB",
            balance=Decimal("0.00"),
        )
        session.add(main_account)
        await session.flush()

        # Issue virtual Luhn MIR card
        card = await cls._issue_card(session, new_user.id, main_account.id, new_user.full_name)

        # Create 18% APR Savings safe
        savings_account = SavingsAccount(
            user_id=new_user.id,
            balance=Decimal("0.00"),
            annual_rate=Decimal("0.1800"),
            accrued_interest=Decimal("0.000000"),
            last_interest_calc=datetime.datetime.now(),
        )
        session.add(savings_account)
        await session.flush()

        return new_user, main_account, card

    @classmethod
    async def _issue_card(
        cls, session: AsyncSession, user_id: int, account_id: str, cardholder_name: str
    ) -> Card:
        """Issue a unique 16-digit Luhn card with collision retry logic."""
        for _ in range(10):
            card_num = CardService.generate_card_number()
            # Verify uniqueness
            res = await session.execute(select(Card).where(Card.card_number == card_num))
            if res.scalars().first() is None:
                card = Card(
                    user_id=user_id,
                    account_id=account_id,
                    card_number=card_num,
                    cardholder_name=cardholder_name.upper() if cardholder_name else "TBANK CLIENT",
                    cvv=CardService.generate_cvv(),
                    expiry_date=CardService.generate_expiry(years_ahead=5),
                    is_frozen=False,
                    status="ACTIVE",
                )
                session.add(card)
                await session.flush()
                return card
        raise RuntimeError("Failed to generate a unique card number after multiple attempts")

    @classmethod
    async def get_user_overview(cls, session: AsyncSession, user_id: int) -> dict:
        """Aggregate balance overview across Main, Savings, Cashback, and Investments."""
        # Query user with accounts and savings
        user_res = await session.execute(
            select(User)
            .where(User.id == user_id)
            .options(
                selectinload(User.accounts),
                selectinload(User.cards),
                selectinload(User.savings_account),
            )
        )
        user = user_res.scalars().first()
        if not user:
            raise ValueError(f"User {user_id} not found")

        # Main account balance
        main_acc = next((a for a in user.accounts if a.account_type == "MAIN"), None)
        main_balance = main_acc.balance if main_acc else Decimal("0.00")

        # Savings balance & accrued
        savings_balance = user.savings_account.balance if user.savings_account else Decimal("0.00")
        savings_accrued = (
            user.savings_account.accrued_interest if user.savings_account else Decimal("0.00")
        )

        # Cashback balance
        cashback_balance = user.cashback_balance

        # Portfolio valuation
        port_query = (
            select(InvestmentPortfolio, Stock)
            .join(Stock, InvestmentPortfolio.stock_id == Stock.id)
            .where(InvestmentPortfolio.user_id == user_id)
            .where(InvestmentPortfolio.shares > 0)
        )
        port_res = await session.execute(port_query)
        portfolio_rows = port_res.all()

        portfolio_value = Decimal("0.00")
        for port, stock in portfolio_rows:
            portfolio_value += Decimal(str(port.shares)) * stock.current_price

        # Primary card details
        primary_card = user.cards[0] if user.cards else None
        card_info = None
        if primary_card:
            card_info = {
                "id": primary_card.id,
                "card_number": primary_card.card_number,
                "formatted_masked": CardService.format_card_number(
                    primary_card.card_number, masked=True
                ),
                "formatted_full": CardService.format_card_number(
                    primary_card.card_number, masked=False
                ),
                "cvv": primary_card.cvv,
                "expiry_date": primary_card.expiry_date,
                "is_frozen": primary_card.is_frozen,
                "status": primary_card.status,
            }

        total_net_worth = main_balance + savings_balance + cashback_balance + portfolio_value

        return {
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "full_name": user.full_name,
            "main_balance": main_balance,
            "savings_balance": savings_balance,
            "savings_accrued_interest": savings_accrued,
            "cashback_balance": cashback_balance,
            "portfolio_value": portfolio_value,
            "total_net_worth": total_net_worth,
            "card": card_info,
        }

    @classmethod
    async def get_user_main_account(cls, session: AsyncSession, user_id: int) -> Account | None:
        """Fetch the primary card account for a user."""
        res = await session.execute(
            select(Account)
            .where(Account.user_id == user_id)
            .where(Account.account_type == "MAIN")
        )
        return res.scalars().first()
