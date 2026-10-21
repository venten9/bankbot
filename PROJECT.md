# Project: T-Bank Telegram Neobank Bot

## Architecture
Modern, asynchronous Telegram neobank bot built with:
- **Framework**: Python 3.14 + `aiogram 3.31.0` (asynchronous Telegram Bot framework with Routers, FSM, and Middlewares).
- **Persistence**: `SQLAlchemy 2.1.5` (Async ORM) + `aiosqlite 0.22.1` with SQLite WAL mode (`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;`).
- **Configuration**: `pydantic-settings 2.16.0` reading environment variables from `.env`.
- **Testing**: `pytest 9.1.1` + `pytest-asyncio 1.4.0` with offline mock bot harness (`AsyncMock` on `Bot` and `.as_(mock_bot)`).
- **Concurrency & Accounting Model**: Double-entry bookkeeping ledger with relational `CHECK (balance >= 0.00)` constraints and atomic conditional SQL updates (`WHERE id = :id AND balance >= :amount AND is_frozen = 0`) to mathematically eliminate overdrafts and race conditions.

### Architectural Layers
1. **Config Layer** (`app/config.py`): Pydantic Settings model with validation for `BOT_TOKEN`, `ADMIN_IDS` (comma-separated, JSON array, or list of ints), `DATABASE_URL`, and financial parameters.
2. **Database Layer** (`app/database/`): Engine, session factory (`async_sessionmaker`), metadata Base, and relational ORM models.
3. **Domain & Services Layer** (`app/services/`):
   - `banking.py`: User registration, account creation, ledger entries, balance inquiries.
   - `card.py`: Luhn 16-digit generator/validator (MIR BIN 2200 70), CVV, expiry date, freeze/unfreeze state machine.
   - `transfer.py`: Atomic P2P/SBP transfers with username, phone, card, or ID resolution, overdraft protection, recipient push notification.
   - `savings.py`: Savings accounts, deposit/withdraw, 18% APR daily accrual math with atomic payout.
   - `investments.py`: Parody stock market simulation with 6 fictional stocks, discrete mean-reverting GBM dynamic pricing, buy/sell execution, portfolio valuation.
   - `cashback.py`: 4-of-8 humorous category selection, purchase cashback calculation, 1-click bonus payout.
   - `oleg.py`: Witty, sarcastic, yet helpful virtual assistant rule/regex engine with bank banter.
   - `receipts.py`: Digital receipts formatting (fiscal code, auth hash) and 10-block Unicode visual expense progress bars.
   - `admin.py`: Aggregation metrics, user lookup, balance adjustments with reason logging, broadcast engine.
4. **Presentation & Bot Layer** (`app/bot/`):
   - `middlewares/`: `DbSessionMiddleware` (injects `AsyncSession`), `CurrentUserMiddleware` (ensures registered User).
   - `filters/`: `IsAdminFilter` for RBAC protection.
   - `keyboards/`: Inline and reply keyboards for banking menus, investments, cashback, and admin panel.
   - `states/`: FSM state groups for transfers, balance adjustments, broadcasts, card management.
   - `routers/`: Domain routers (`common`, `card`, `transfer`, `savings`, `invest`, `cashback`, `oleg`, `admin`).
5. **Entry Point** (`main.py`): Async application startup, database initialization, dispatcher setup, and polling.

---

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | Luhn 16-Digit Card Issuance | Virtual card generation using MIR BIN 2200 70 and Luhn MOD-10 checksum | M1 | Survey R1 |
| F2 | Card CVV & Expiry Date | 3-digit CVV generation, 3-year expiry date, masked/unmasked toggle | M1 | Survey R1 |
| F3 | Card Freeze / Unfreeze | Account freeze toggle blocking all outgoing debit transactions | M1 | Survey R1 |
| F4 | T-Bank Black Styling | Visual formatting of card details, masked PAN, and balance in T-Bank style | M2 | Survey R1 |
| F5 | Multi-Account Overview | Balance aggregation across Main, Savings, Investments, and Cashback | M1 | Survey R1 |
| F6 | P2P Transfer by @username | Transfer resolution by Telegram @username with atomic settlement | M1 | Survey R1 |
| F7 | SBP Transfer by Phone | Transfer resolution by Russian phone number format (+7...) | M1 | Survey R1 |
| F8 | Card-to-Card Transfer | Transfer resolution by 16-digit Luhn card number | M1 | Survey R1 |
| F9 | Transfer by User ID | Transfer resolution by Telegram numeric user ID | M1 | Survey R1 |
| F10 | Recipient Real-time Notification | Instant push alert to recipient Telegram chat upon incoming transfer | M2 | Survey R1 |
| F11 | Double-Entry Atomic Ledger | Double-entry debit/credit ledger posting in SQLite immediate transaction | M1 | Survey R1 |
| F12 | Overdraft & Race Rejection | Atomic SQL conditional update preventing negative balances under concurrency | M1 | Survey R1 |
| F13 | Self-Transfer Prevention | Validation rejecting transfers where sender equals recipient | M1 | Survey R1 |
| F14 | Frozen Account Rejection | Immediate rejection of transfers if sender card/account is frozen | M1 | Survey R1 |
| F15 | Savings Account Creation | Creation and display of 18% APR savings safe (Накопительный счет) | M1 | Survey R1 |
| F16 | Savings Deposit | Atomic transfer of funds from Main card account to Savings account | M1 | Survey R1 |
| F17 | Savings Withdrawal | Atomic transfer of funds from Savings account to Main card account | M1 | Survey R1 |
| F18 | 18% APR Interest Accrual Math | Actual/365 daily interest accumulation formula without duplicate payouts | M1 | Survey R1 |
| F19 | Interest Payout Execution | 1-click on-demand or periodic payout of accrued interest to balance | M1 | Survey R1 |
| F20 | Parody Stock Market Catalog | 6 fictional humorous Russian stocks (ПАО Шаурма на вокзале, АО Завод спичек, etc.) | M1 | Survey R1 |
| F21 | Dynamic Stock Price Engine | Discrete Mean-Reverting Geometric Brownian Motion pricing with price floor | M1 | Survey R1 |
| F22 | Stock Buy Execution | Purchase shares with balance deduction, 0.3% broker fee, and portfolio update | M1 | Survey R1 |
| F23 | Stock Sell Execution | Liquidate shares with portfolio update, fee deduction, and balance credit | M1 | Survey R1 |
| F24 | Portfolio Valuation & P&L | Real-time portfolio valuation and profit/loss calculation | M1 | Survey R1 |
| F25 | Cashback Category Selection | Selection of 4 humorous categories from 8 available each month | M1 | Survey R1 |
| F26 | Cashback Accrual on Purchases | Bonus ruble accrual based on selected category percentages (5%-15%) | M1 | Survey R1 |
| F27 | Cashback Bonus Rubles Payout | 1-click payout transfer from bonus cashback account to Main card balance | M1 | Survey R1 |
| F28 | "Олег" Banking Intent Triggers | Pattern/regex recognition providing witty, sarcastic, yet helpful banking answers | M2 | Survey R1 |
| F29 | "Олег" Fallback Sarcastic Banter | Humorous responses to off-topic user messages mimicking T-Bank assistant | M2 | Survey R1 |
| F30 | Formatted Digital Receipts | Transaction receipt with ID, timestamp, amounts, auth hash, fiscal sign | M2 | Survey R1 |
| F31 | Digital Receipt Category Tagging | Association of transactions with humorous and banking expense categories | M1 | Survey R1 |
| F32 | Category Expense Analytics | Expense aggregation by category for the current accounting period | M1 | Survey R1 |
| F33 | Visual Expense Progress Bars | 10-block Unicode visual bar representation of expenses (`████░░░░░░ 40%`) | M2 | Survey R1 |
| F34 | /admin Access Control | Restriction of /admin command strictly to user IDs defined in ADMIN_IDS | M3 | Survey R2 |
| F35 | Dual-Layer Admin RBAC | IsAdminFilter router guard plus callback interception returning 403 alert | M3 | Survey R2 |
| F36 | Real-Time System Metrics | Display total registered users, active/frozen accounts, total liquidity, tx volume | M3 | Survey R2 |
| F37 | Admin User Search by @username | Lookup and retrieve user details by Telegram username | M3 | Survey R2 |
| F38 | Admin User Search by User ID | Lookup and retrieve user details by numeric Telegram user ID | M3 | Survey R2 |
| F39 | Admin User Search by Card | Lookup and retrieve user details by 16-digit Luhn card number | M3 | Survey R2 |
| F40 | Admin User Inspection View | Display user profile, balances, card details, freeze state, and ledger link | M3 | Survey R2 |
| F41 | Admin View User Ledger | Display recent debit/credit transactions for the inspected user | M3 | Survey R2 |
| F42 | Admin Account Freeze | Admin action to freeze user account with audit log | M3 | Survey R2 |
| F43 | Admin Account Unfreeze | Admin action to unfreeze user account with audit log | M3 | Survey R2 |
| F44 | Admin Balance Credit | Credit funds with mandatory reason (5-255 chars) and audit logging | M3 | Survey R2 |
| F45 | Admin Balance Debit | Debit funds with overdraft rejection, mandatory reason, and audit logging | M3 | Survey R2 |
| F46 | Admin Action Audit Logging | Record all admin operations into persistent admin_logs table | M3 | Survey R2 |
| F47 | Admin Adjustment Notification | Push notification to target user notifying of admin balance adjustment | M3 | Survey R2 |
| F48 | Broadcast Composition Flow | Multi-step FSM for drafting and confirming broadcast announcement | M3 | Survey R2 |
| F49 | Broadcast Rate-Limited Dispatch | Asynchronous broadcast loop (~25 msg/s) with exception handling | M3 | Survey R2 |
| F50 | Broadcast Delivery Summary | Post-broadcast delivery report (sent, failed, blocked counts) | M3 | Survey R2 |
| F51 | Asynchronous Database Engine | SQLAlchemy 2.0 Async + aiosqlite WAL mode engine configuration | M1 | Survey R4 |
| F52 | Pydantic Settings Configuration | Type-safe settings loading from .env with flexible ADMIN_IDS parser | M1 | Survey R4 |
| F53 | Packaging & Dependencies | requirements.txt with pinned dependencies and .env.example | M4 | Survey R4 |
| F54 | Project Documentation | Comprehensive README.md with setup, admin guide, and usage | M4 | Survey R4 |
| F55 | Automated Pytest Suite | Comprehensive 4-tier test suite in tests/ passing with 0 errors | E2E | Survey R3 |
| F56 | UserJob Model & Schema Persistence | UserJob table with user_id, branch, level, shifts, timers, unique (user_id, branch) | M5 | Survey Jobs R1 |
| F57 | User last_hustle_at Tracking | User relationship to jobs and last_hustle_at timestamp | M5 | Survey Jobs R1 |
| F58 | 4 Branches & 20 Professions Catalog | IT, SERVICE, FINANCE, SHADOW matrix across 5 tiers with titles, salaries, shift reqs | M5 | Survey Jobs R2 |
| F59 | Multi-Employment Cap | Maximum 2 active jobs simultaneously, 3rd job application rejected | M5 | Survey Jobs R2 |
| F60 | Career Resignation & Progress Retention | Quitting job deactivates record while preserving level and completed shifts | M5 | Survey Jobs R2 |
| F61 | Timed Shift Lifecycle | 2-hour shift duration/cooldown, premature claim rejection | M5 | Survey Jobs R2 |
| F62 | Workplace Events Roll | Probabilistic events (Bonus, Reprimand, Shadow Police Raid) modifying salary | M5 | Survey Jobs R2 |
| F63 | Atomic Salary Settlement | Atomic credit to Main account and Transaction(tx_type="SALARY") with LedgerEntry | M5 | Survey Jobs R2 |
| F64 | Career Advancement Progression | Promotion check and tier upgrade when completed shifts meet requirement | M5 | Survey Jobs R2 |
| F65 | Interactive Quick Hustles Engine | 15-minute cooldown, situational decision dilemmas with multiple options | M5 | Survey Jobs R2 |
| F66 | Non-Negative Balance Clamping | Probabilistic rewards or penalties guaranteed not to cause negative balances | M5 | Survey Jobs R2 |
| F67 | Persistent Reply Button | "💼 Работа и бизнес" keyboard button in main menu | M6 | Survey Jobs R3 |
| F68 | Career Dashboard Keyboards | Dynamic dashboard showing active jobs, shift timers, claim/promotion buttons | M6 | Survey Jobs R3 |
| F69 | Job Market Keyboards | Branch catalog and hiring/resignation buttons | M6 | Survey Jobs R3 |
| F70 | Hustle Decision Keyboards | Inline buttons for dilemma options and cancellation | M6 | Survey Jobs R3 |
| F71 | Rich HTML Formatters & Progress Bars | Formatters for career card, shift countdowns, Unicode progress bars | M6 | Survey Jobs R3 |
| F72 | Telegram Router Registration | jobs_router registered in bot.py and routers/__init__.py with full handler suite | M6 | Survey Jobs R3 |
| F73 | Automated Career Pytest Suite | Comprehensive pytest suite in tests/test_jobs_career.py covering R1-R4 | M7 | Survey Jobs R4 |
| F74 | Zero Regressions Full Verification | 100% test pass across all tests with 0 failures and 0 errors | M7 | Survey Jobs R4 |

---

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Core Database, Config & Banking Engine | Database models, schemas, config.py, and all services (banking, card Luhn, atomic transfers, savings 18% APR, investments GBM, cashback, receipts logic) | none | DONE |
| M2 | T-Bank Telegram Bot Client & User Experience | aiogram 3 bot setup, middlewares, state machines, keyboards, formatters, routers (common, card, transfer, savings, invest, cashback, oleg) | M1 | DONE |
| M3 | Administrative Management Panel (/admin) | /admin RBAC IsAdminFilter, metrics, user search, inspection, freeze/unfreeze, balance adjustment with reason, audit log, broadcast engine | M1, M2 | DONE |
| M4 | Packaging, Documentation & Full Integration | requirements.txt, .env.example, README.md, full E2E test verification (100% pass) and adversarial hardening | M1, M2, M3, E2E | DONE |
| E2E | E2E Testing Track | Independent opaque-box test harness and test suite covering Tiers 1-4 | none | DONE |
| M5 | Database Models & Core Career Domain Service | UserJob model, User.last_hustle_at, check constraints, app/database/__init__.py, app/services/jobs.py with atomic ledger and clamping | none | DONE |
| M6 | Telegram Bot UI, Keyboards, Formatters & Handlers | reply.py, app/bot/keyboards/jobs.py, app/bot/formatters/jobs.py, app/bot/routers/jobs.py, bot.py registration | M5 | DONE |
| M7 | Automated Pytest Suite, Regression Verification & Audit | tests/test_jobs_career.py, README.md, adversarial verification, forensic audit, 0 regressions | M5, M6 | DONE |

---

## Interface Contracts

### 1. Config Interface (`app/config.py`)
- `Settings`:
  - `BOT_TOKEN: str` (default dummy for tests if unset)
  - `ADMIN_IDS: list[int]` (validated from comma-separated string, JSON, or list)
  - `DATABASE_URL: str` (default `sqlite+aiosqlite:///tbank.db`)
  - `SAVINGS_APR: float = 0.18`
  - `BROKER_FEE_PERCENT: float = 0.003`

### 2. Database Models (`app/database/models.py`)
- `User`: `id (int PK)`, `telegram_id (int unique index)`, `username (str nullable index)`, `full_name (str)`, `created_at`
- `Account`: `id (int PK)`, `user_id (FK)`, `account_type (str: MAIN, SAVINGS, CASHBACK, INVEST)`, `balance (Decimal, CHECK >= 0)`, `is_frozen (bool)`
- `Card`: `id (int PK)`, `account_id (FK)`, `card_number (str 16-digit unique, Luhn valid)`, `cvv (str 3-digit)`, `expiry_date (str MM/YY)`, `is_frozen (bool)`
- `Transaction`: `id (str UUID PK)`, `sender_account_id (FK nullable)`, `recipient_account_id (FK nullable)`, `amount (Decimal > 0)`, `category (str)`, `description (str)`, `fiscal_code (str)`, `auth_code (str)`, `created_at`
- `LedgerEntry`: `id (int PK)`, `transaction_id (FK)`, `account_id (FK)`, `direction (DEBIT/CREDIT)`, `amount (Decimal)`, `balance_after (Decimal)`
- `SavingsAccount`: `id (int PK)`, `user_id (FK)`, `balance (Decimal >= 0)`, `last_interest_accrual (datetime)`, `accumulated_interest (Decimal >= 0)`
- `Stock`: `id (int PK)`, `ticker (str unique)`, `name (str)`, `base_price (Decimal)`, `current_price (Decimal)`, `volatility (float)`, `mean_reversion_rate (float)`
- `InvestmentPortfolio`: `id (int PK)`, `user_id (FK)`, `stock_id (FK)`, `shares (int >= 0)`, `average_buy_price (Decimal)`
- `CashbackCategory`: `id (int PK)`, `name (str)`, `code (str unique)`, `percent (int: 5-15%)`, `description (str)`
- `UserCashbackSelection`: `id (int PK)`, `user_id (FK)`, `category_id (FK)`, `month_year (str YYYY-MM)`
- `AdminLog`: `id (int PK)`, `admin_telegram_id (int)`, `target_telegram_id (int nullable)`, `action (str)`, `amount (Decimal nullable)`, `reason (str)`, `created_at`

### 3. Banking Services Interface (`app/services/`)
- `BankingService`:
  - `get_or_create_user(session, telegram_id, username, full_name) -> tuple[User, Account, Card]`
  - `get_user_overview(session, user_id) -> dict`
- `CardService`:
  - `generate_card_number() -> str` (MIR BIN 2200 70 + 9 random + Luhn check digit)
  - `validate_luhn(card_number: str) -> bool`
  - `freeze_card(session, card_id, user_id) -> bool`
  - `unfreeze_card(session, card_id, user_id) -> bool`
- `TransferService`:
  - `resolve_recipient(session, query: str) -> Account | None` (checks @username, phone, card 16-digit, numeric ID)
  - `execute_transfer(session, sender_user_id, recipient_account_id, amount: Decimal, category: str = "Перевод") -> Transaction`
    - Raises `InsufficientFundsError`, `AccountFrozenError`, `SelfTransferError`, `RecipientNotFoundError`
- `SavingsService`:
  - `get_savings(session, user_id) -> SavingsAccount`
  - `deposit_to_savings(session, user_id, amount: Decimal) -> None`
  - `withdraw_from_savings(session, user_id, amount: Decimal) -> None`
  - `calculate_accrued_interest(savings_account, as_of: datetime) -> Decimal`
  - `payout_interest(session, user_id) -> Decimal`
- `InvestmentService`:
  - `list_stocks(session) -> list[Stock]`
  - `tick_stock_prices(session) -> None` (discrete mean-reverting GBM)
  - `buy_stock(session, user_id, ticker: str, shares: int) -> dict`
  - `sell_stock(session, user_id, ticker: str, shares: int) -> dict`
  - `get_portfolio(session, user_id) -> dict`
- `CashbackService`:
  - `get_available_categories(session) -> list[CashbackCategory]`
  - `select_categories(session, user_id, category_ids: list[int]) -> None`
  - `payout_cashback(session, user_id) -> Decimal`
- `AdminService`:
  - `get_metrics(session) -> dict` (user_count, active_accounts, total_liquidity, tx_volume)
  - `search_user(session, query: str) -> User | None`
  - `adjust_balance(session, admin_id: int, user_id: int, delta: Decimal, reason: str) -> Transaction`
  - `freeze_user_accounts(session, admin_id: int, user_id: int, freeze: bool) -> None`

---

## Code Layout
```
d:\andrey\antigravityprojects\project6\
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── session.py
│   │   └── models.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── banking.py
│   │   ├── card.py
│   │   ├── transfers.py
│   │   ├── savings.py
│   │   ├── investments.py
│   │   ├── cashback.py
│   │   ├── oleg.py
│   │   ├── receipts.py
│   │   └── admin.py
│   └── bot/
│       ├── __init__.py
│       ├── bot.py
│       ├── middlewares/
│       │   ├── __init__.py
│       │   ├── db.py
│       │   └── user.py
│       ├── filters/
│       │   ├── __init__.py
│       │   └── admin.py
│       ├── states/
│       │   ├── __init__.py
│       │   └── states.py
│       ├── keyboards/
│       │   ├── __init__.py
│       │   ├── inline.py
│       │   └── reply.py
│       ├── formatters/
│       │   ├── __init__.py
│       │   ├── cards.py
│       │   ├── receipts.py
│       │   └── analytics.py
│       └── routers/
│           ├── __init__.py
│           ├── common.py
│           ├── card.py
│           ├── transfer.py
│           ├── savings.py
│           ├── invest.py
│           ├── cashback.py
│           ├── oleg.py
│           └── admin.py
├── main.py
├── requirements.txt
├── .env.example
├── README.md
├── pytest.ini
└── tests/
    ├── conftest.py
    ├── test_math_unit.py         (Tier 1: Luhn, 18% APR, GBM ticks, cashback)
    ├── test_banking_concurrency.py (Tier 2: Atomic double-entry, concurrent race overdraft rejection)
    ├── test_admin_operations.py  (Tier 3: RBAC 403, user search, balance credit/debit with reason, broadcast)
    └── test_bot_e2e_handlers.py  (Tier 4: Offline mock bot, FSM flows, callbacks, notifications)
```
