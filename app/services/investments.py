"""Parody Stock Market Service (Т-Инвестиции): Discrete Mean-Reverting GBM and Portfolio Trading."""

import math
import random
import uuid
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Account, InvestmentPortfolio, Stock, Transaction
from app.services.transfers import InsufficientFundsError


DEFAULT_STOCKS = [
    {
        "ticker": "SHAW",
        "name": 'ПАО "Шаурма на вокзале"',
        "base_price": Decimal("150.00"),
        "current_price": Decimal("150.00"),
        "volatility": 0.08,
        "mean_reversion_rate": 0.05,
        "sector": "Потребительский",
        "description": "Флагман уличного фастфуда. Котировки зависят от внезапных проверок Роспотребнадзора.",
    },
    {
        "ticker": "MTCH",
        "name": 'АО "Завод спичек им. Прометея"',
        "base_price": Decimal("1.50"),
        "current_price": Decimal("1.50"),
        "volatility": 0.02,
        "mean_reversion_rate": 0.05,
        "sector": "Промышленность",
        "description": "Сверхдешевый защитный актив. Стабильнее швейцарских часов и надежнее золота.",
    },
    {
        "ticker": "BBRS",
        "name": 'ПАО "Бобруйский бобр"',
        "base_price": Decimal("420.00"),
        "current_price": Decimal("420.00"),
        "volatility": 0.05,
        "mean_reversion_rate": 0.05,
        "sector": "Энергетика",
        "description": "Лидер плотиностроения и зеленой энергетики. Экологичные бревна высшей пробы.",
    },
    {
        "ticker": "DVAN",
        "name": 'ПАО "НИИ Диванных Войск"',
        "base_price": Decimal("1337.00"),
        "current_price": Decimal("1337.00"),
        "volatility": 0.12,
        "mean_reversion_rate": 0.05,
        "sector": "IT & Аналитика",
        "description": "Высокотехнологичная аналитика геополитики прямо из комментариев в Telegram.",
    },
    {
        "ticker": "GRECH",
        "name": 'АО "Стратегический запас гречки"',
        "base_price": Decimal("95.00"),
        "current_price": Decimal("95.00"),
        "volatility": 0.04,
        "mean_reversion_rate": 0.05,
        "sector": "Ритейл",
        "description": "Главная резервная валюта любого кризиса. Растет при любых новостях.",
    },
    {
        "ticker": "MYK",
        "name": 'ООО "Свет в конце тоннеля"',
        "base_price": Decimal("500.00"),
        "current_price": Decimal("500.00"),
        "volatility": 0.15,
        "mean_reversion_rate": 0.05,
        "sector": "Венчур",
        "description": "Стартап на стыке квантовой физики, эзотерики и нанотехнологий. Высочайший риск.",
    },
]


class InsufficientSharesError(Exception):
    """Raised when user attempts to sell more shares than owned."""
    pass


class InvestmentService:
    """Trading engine, discrete mean-reverting GBM pricing, and portfolio valuation."""

    @classmethod
    async def init_stocks(cls, session: AsyncSession) -> None:
        """Seed default parody stocks into the database if the table is empty."""
        res = await session.execute(select(Stock))
        if res.scalars().first() is None:
            for stock_data in DEFAULT_STOCKS:
                stock = Stock(**stock_data)
                session.add(stock)
            await session.flush()

    @classmethod
    async def list_stocks(cls, session: AsyncSession) -> list[Stock]:
        """Fetch all parody stocks, initializing catalog if empty."""
        await cls.init_stocks(session)
        res = await session.execute(select(Stock).order_by(Stock.id))
        return list(res.scalars().all())

    @classmethod
    async def get_stock(cls, session: AsyncSession, ticker: str) -> Stock | None:
        """Find a stock by ticker symbol."""
        await cls.init_stocks(session)
        res = await session.execute(
            select(Stock).where(Stock.ticker == ticker.upper().strip())
        )
        return res.scalars().first()

    @staticmethod
    def calculate_next_price(
        current_price: Decimal,
        base_price: Decimal,
        volatility: float,
        mean_reversion_rate: float = 0.05,
        delta_t: float = 1.0,
        epsilon: float | None = None,
    ) -> Decimal:
        """
        Calculate next price using Discrete Mean-Reverting Geometric Brownian Motion (GBM):
        P_{t+1} = max(0.01, P_t * (1 + mu*dt + sigma*sqrt(dt)*eps + kappa*(P_base - P_t)/P_base))
        """
        if epsilon is None:
            epsilon = random.gauss(0, 1)

        p_t = float(current_price)
        p_base = float(base_price)
        sigma = volatility
        kappa = mean_reversion_rate

        # Mean reversion pull towards base price
        mean_reversion_pull = kappa * ((p_base - p_t) / p_base) if p_base > 0 else 0.0
        random_shock = sigma * math.sqrt(delta_t) * epsilon

        price_multiplier = 1.0 + mean_reversion_pull + random_shock
        next_p = max(0.01, p_t * price_multiplier)

        return Decimal(str(next_p)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @classmethod
    async def tick_stock_prices(
        cls, session: AsyncSession, delta_t: float = 1.0
    ) -> list[Stock]:
        """Fluctuate all stock prices by 1 time step using the mean-reverting GBM engine."""
        stocks = await cls.list_stocks(session)
        for stock in stocks:
            next_price = cls.calculate_next_price(
                current_price=stock.current_price,
                base_price=stock.base_price,
                volatility=stock.volatility,
                mean_reversion_rate=stock.mean_reversion_rate,
                delta_t=delta_t,
            )
            stock.current_price = next_price
        await session.flush()
        return stocks

    @classmethod
    async def buy_stock(
        cls, session: AsyncSession, user_id: int, ticker: str, shares: int
    ) -> dict:
        """Buy integer shares of a stock with 0.3% broker fee and balance deduction."""
        if shares <= 0:
            raise ValueError(f"Количество акций должно быть положительным: {shares}")

        stock = await cls.get_stock(session, ticker)
        if not stock:
            raise ValueError(f"Акция с тикером {ticker} не найдена")

        # 1. Calculate costs
        subtotal = Decimal(str(shares)) * stock.current_price
        fee = (subtotal * Decimal(str(settings.BROKER_FEE_PERCENT))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        total_cost = subtotal + fee

        # 2. Check and deduct main account balance
        main_res = await session.execute(
            select(Account)
            .where(Account.user_id == user_id)
            .where(Account.account_type == "MAIN")
        )
        main_acc = main_res.scalars().first()
        if not main_acc:
            raise ValueError(f"Счет пользователя {user_id} не найден")

        debit_stmt = (
            update(Account)
            .where(Account.id == main_acc.id)
            .where(Account.balance >= total_cost)
            .where(Account.is_frozen == False)  # noqa: E712
            .values(balance=Account.balance - total_cost)
        )
        debit_res = await session.execute(debit_stmt)
        if debit_res.rowcount == 0:
            raise InsufficientFundsError(
                f"Недостаточно средств для покупки акций. Доступно: {main_acc.balance} ₽, требуется: {total_cost} ₽."
            )

        # 3. Update or create portfolio holding
        port_res = await session.execute(
            select(InvestmentPortfolio)
            .where(InvestmentPortfolio.user_id == user_id)
            .where(InvestmentPortfolio.stock_id == stock.id)
        )
        portfolio = port_res.scalars().first()

        if portfolio:
            old_shares = portfolio.shares
            old_avg = portfolio.average_buy_price
            new_shares = old_shares + shares
            # Weighted average price formula
            new_avg = (
                (Decimal(str(old_shares)) * old_avg + Decimal(str(shares)) * stock.current_price)
                / Decimal(str(new_shares))
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            portfolio.shares = new_shares
            portfolio.average_buy_price = new_avg
        else:
            new_shares = shares
            new_avg = stock.current_price
            portfolio = InvestmentPortfolio(
                user_id=user_id,
                stock_id=stock.id,
                shares=new_shares,
                average_buy_price=new_avg,
            )
            session.add(portfolio)

        # 4. Record transaction
        tx_id = str(uuid.uuid4())
        tx = Transaction(
            id=tx_id,
            tx_type="STOCK_BUY",
            amount=total_cost,
            fee=fee,
            status="COMPLETED",
            category="Инвестиции",
            sender_account_id=main_acc.id,
            description=f"Покупка {shares} шт. {stock.ticker} ({stock.name})",
            fiscal_code=f"ФП-{random.randint(1000000000, 9999999999)}",
            auth_code=f"AUTH-{random.randint(100000, 999999)}",
        )
        session.add(tx)
        await session.flush()

        return {
            "ticker": stock.ticker,
            "name": stock.name,
            "shares": shares,
            "price": stock.current_price,
            "fee": fee,
            "total_cost": total_cost,
            "total_shares": portfolio.shares,
            "average_buy_price": portfolio.average_buy_price,
        }

    @classmethod
    async def sell_stock(
        cls, session: AsyncSession, user_id: int, ticker: str, shares: int
    ) -> dict:
        """Sell integer shares of a stock with 0.3% broker fee and balance credit."""
        if shares <= 0:
            raise ValueError(f"Количество акций должно быть положительным: {shares}")

        stock = await cls.get_stock(session, ticker)
        if not stock:
            raise ValueError(f"Акция с тикером {ticker} не найдена")

        # 1. Check portfolio holdings
        port_res = await session.execute(
            select(InvestmentPortfolio)
            .where(InvestmentPortfolio.user_id == user_id)
            .where(InvestmentPortfolio.stock_id == stock.id)
        )
        portfolio = port_res.scalars().first()
        if not portfolio or portfolio.shares < shares:
            available = portfolio.shares if portfolio else 0
            raise InsufficientSharesError(
                f"Недостаточно акций для продажи. Доступно: {available} шт., запрошено: {shares} шт."
            )

        # 2. Calculate proceeds, fee, and P&L
        subtotal = Decimal(str(shares)) * stock.current_price
        fee = (subtotal * Decimal(str(settings.BROKER_FEE_PERCENT))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        net_proceeds = subtotal - fee
        cost_basis = Decimal(str(shares)) * portfolio.average_buy_price
        realized_pnl = net_proceeds - cost_basis

        # 3. Credit net proceeds to main account
        main_res = await session.execute(
            select(Account)
            .where(Account.user_id == user_id)
            .where(Account.account_type == "MAIN")
        )
        main_acc = main_res.scalars().first()
        if not main_acc:
            raise ValueError(f"Счет пользователя {user_id} не найден")

        await session.execute(
            update(Account)
            .where(Account.id == main_acc.id)
            .values(balance=Account.balance + net_proceeds)
        )

        # 4. Update portfolio
        portfolio.shares -= shares
        if portfolio.shares == 0:
            portfolio.average_buy_price = Decimal("0.00")

        # 5. Record transaction
        tx_id = str(uuid.uuid4())
        tx = Transaction(
            id=tx_id,
            tx_type="STOCK_SELL",
            amount=net_proceeds,
            fee=fee,
            status="COMPLETED",
            category="Инвестиции",
            recipient_account_id=main_acc.id,
            description=f"Продажа {shares} шт. {stock.ticker} ({stock.name})",
            fiscal_code=f"ФП-{random.randint(1000000000, 9999999999)}",
            auth_code=f"AUTH-{random.randint(100000, 999999)}",
        )
        session.add(tx)
        await session.flush()

        return {
            "ticker": stock.ticker,
            "name": stock.name,
            "shares": shares,
            "price": stock.current_price,
            "fee": fee,
            "net_proceeds": net_proceeds,
            "realized_pnl": realized_pnl,
            "remaining_shares": portfolio.shares,
        }

    @classmethod
    async def get_portfolio(cls, session: AsyncSession, user_id: int) -> dict:
        """Fetch user portfolio summary with real-time valuation and unrealized P&L."""
        await cls.init_stocks(session)
        port_res = await session.execute(
            select(InvestmentPortfolio, Stock)
            .join(Stock, InvestmentPortfolio.stock_id == Stock.id)
            .where(InvestmentPortfolio.user_id == user_id)
            .where(InvestmentPortfolio.shares > 0)
        )
        rows = port_res.all()

        holdings = []
        total_value = Decimal("0.00")
        total_invested = Decimal("0.00")

        for port, stock in rows:
            pos_val = Decimal(str(port.shares)) * stock.current_price
            pos_cost = Decimal(str(port.shares)) * port.average_buy_price
            pos_pnl = pos_val - pos_cost
            pos_pnl_pct = (
                float((pos_pnl / pos_cost) * 100) if pos_cost > Decimal("0.00") else 0.0
            )

            total_value += pos_val
            total_invested += pos_cost

            holdings.append({
                "ticker": stock.ticker,
                "name": stock.name,
                "shares": port.shares,
                "average_buy_price": port.average_buy_price,
                "current_price": stock.current_price,
                "current_value": pos_val,
                "unrealized_pnl": pos_pnl,
                "yield_percent": pos_pnl_pct,
            })

        total_pnl = total_value - total_invested
        total_pnl_pct = (
            float((total_pnl / total_invested) * 100) if total_invested > Decimal("0.00") else 0.0
        )

        return {
            "holdings": holdings,
            "total_value": total_value,
            "total_invested": total_invested,
            "total_pnl": total_pnl,
            "total_pnl_percent": total_pnl_pct,
        }
