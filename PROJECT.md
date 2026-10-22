# Project: Custom 16-Digit Card Numbers & Admin Superpowers / Cheat Engine for T-Bank

## Architecture
- **Data Layer** (`app/database/`):
  - `Card` model in `app/database/models.py` with explicit named constraint `UniqueConstraint("card_number", name="uq_cards_card_number")`.
  - Transaction types: `ADMIN_CHEAT`, `ADMIN_ADJUSTMENT`.
  - `AdminLog` records for all cheat actions, cooldown resets, asset/stock grants, and market manipulations.
- **Domain Services** (`app/services/`):
  - `CardService.change_card_number` in `app/services/card.py`: Strips whitespace/hyphens, validates 16 digits, verifies uniqueness across database (excluding user's own card), updates `Card.card_number`.
  - `TransferService.resolve_recipient` in `app/services/transfers.py`: Updated to resolve 16-digit card numbers directly from database even if vanity card does not satisfy Luhn.
  - `AdminService.cleanup_test_users` in `app/services/admin.py`: Safe cascade purge in strict reverse dependency order (AdminLog -> LedgerEntry -> Transaction -> Card/Savings/Portfolio/Cashback/Job/Property -> Account -> User) protecting real admin ID `1302123809` and `settings.ADMIN_IDS`.
  - `AdminService.cheat_balance` & `cheat_cashback`: 1-click resource top-ups with atomic balance updates, double-entry ledger, receipt codes, and `AdminLog`.
  - `AdminService.reset_cooldowns`: Fast-forward Wheel (`None`), Job shifts (rewind 3h), Rental income (rewind 25h), Hustles (`None`).
  - `AdminService.grant_property` & `grant_stocks`: 0 ₽ cost grants with duplicate protection and `AdminLog` audit.
  - `AdminService.manipulate_market` & `set_stock_price`: Pump (+50%), Dump (-50%), Crash (-80%), and per-stock target price, strictly floored at `1.00 ₽`.
- **Bot UI & Interaction** (`app/bot/`):
  - `CardState` in `app/bot/states/states.py` (`waiting_for_custom_number`).
  - Inline keyboards in `keyboards/card.py` (and `keyboards/inline.py`) with `[✏️ Изменить номер карты]` and `[❌ Отмена]`.
  - Admin inline keyboards and formatters in `keyboards/admin.py` and `formatters/admin.py`: Cheat panel, fast-forward cooldowns, market regulator, asset/stock grants, test user cleanup with confirmation dialog.
  - All callbacks strictly <= 64 bytes.
  - Strict RBAC via `IsAdminFilter()` with catch-all denial.
  - HTML escaping via `html.escape(..., quote=False)`.
- **Testing & Verification** (`tests/`):
  - Automated test suite in `tests/test_card_custom_and_admin_superpowers.py` covering Tiers 1-4.
  - 100% test pass on existing 784+ tests with 0 regressions.
  - Updated `README.md`.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Custom 16-Digit Card Number Service | `CardService.change_card_number`: validate 16 digits, strip spaces/hyphens, DB uniqueness check, update card | M1 | ORIGINAL_REQUEST §R1 |
| 2 | Card Number Unique Constraint | Explicit `uq_cards_card_number` on Card model | M1 | ORIGINAL_REQUEST §R1 |
| 3 | Transfer Resolution Compatibility | Update `transfers.py` to allow transfers to vanity 16-digit cards without Luhn rejection | M1 | Survey 1, Survey 3 |
| 4 | Safe Test Data Purge | `AdminService.cleanup_test_users`: cascade purge of mock accounts preserving admin 1302123809 | M2 | ORIGINAL_REQUEST §R2 |
| 5 | 1-Click Resource Cheats | `AdminService.cheat_balance` (+100k, +1M, +100M ₽) and `cheat_cashback` (+50k points) | M2 | ORIGINAL_REQUEST §R3 |
| 6 | Cooldown Fast-Forward | `AdminService.reset_cooldowns`: Wheel, Job Shifts (-3h), Rentals (-25h), Hustle | M2 | ORIGINAL_REQUEST §R4 |
| 7 | 1-Click Asset & Stock Grants | `AdminService.grant_property` (cars, real estate, luxury) and `grant_stocks` (+1000 shares) | M2 | ORIGINAL_REQUEST §R5 |
| 8 | Stock Market Manipulation | `AdminService.manipulate_market` (Pump +50%, Dump -50%, Crash -80%) and `set_stock_price` (1.00 ₽ floor) | M2 | ORIGINAL_REQUEST §R6 |
| 9 | Custom Card FSM & Handlers | `CardState.waiting_for_custom_number`, `[✏️ Изменить номер карты]`, cancel handling, error alerts | M3 | ORIGINAL_REQUEST §R1, §R7 |
| 10 | Admin Cheat Panel UI | Submenu for resource top-ups and fast-forward cooldowns with compact callbacks (<64 bytes) | M3 | ORIGINAL_REQUEST §R3, §R4, §R7 |
| 11 | Admin Asset/Stock Grants UI | Submenu for granting high-tier cars, real estate, luxury, stocks (<64 bytes) | M3 | ORIGINAL_REQUEST §R5, §R7 |
| 12 | Admin Market Regulator UI | Submenu for pump, dump, crash, and stock price targets (<64 bytes) | M3 | ORIGINAL_REQUEST §R6, §R7 |
| 13 | Admin Test Data Cleanup UI | Confirmation dialog and button `[🧹 Очистить тестовых юзеров]` (<64 bytes) | M3 | ORIGINAL_REQUEST §R2, §R7 |
| 14 | Strict RBAC & Security | `IsAdminFilter()` on all admin commands and callbacks; catch-all denial; Oleg permanently absent | M3 | Invariants, ORIGINAL_REQUEST §R7 |
| 15 | Automated Test Suite (R8) | Comprehensive Tier 1-4 tests in `tests/test_card_custom_and_admin_superpowers.py` | M4 | ORIGINAL_REQUEST §R8 |
| 16 | Zero Regression Verification | Pass 100% of existing 784+ tests + all new tests with 0 failures, 0 errors | M4 | ORIGINAL_REQUEST §R8 |
| 17 | Documentation Update | Update `README.md` with Custom Card Numbers and Admin Superpowers | M4 | ORIGINAL_REQUEST §R8 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Custom Card Domain & Service | `app/database/models.py`, `app/services/card.py`, `app/services/transfers.py` | none | PLANNED |
| M2 | Admin Superpowers Core Domain | `app/services/admin.py` (Purge, Cheats, Cooldowns, Grants, Market) | none | PLANNED |
| M3 | Telegram Keyboards, FSM & Routers | `app/bot/states/`, `app/bot/keyboards/`, `app/bot/formatters/`, `app/bot/routers/` | M1, M2 | PLANNED |
| M4 | Automated Test Suite & Full Verification | `tests/test_card_custom_and_admin_superpowers.py`, `README.md`, full test run | M3 | PLANNED |

## Interface Contracts
### CardService.change_card_number
- Signature: `async def change_card_number(cls, session: AsyncSession, user_id: int, new_number: str) -> Card`
- Input: `new_number` (16 digits, with optional spaces or hyphens).
- Validation: Strips ` ` and `-`. Must satisfy `len(clean) == 16 and clean.isdigit()`. Otherwise raises `ValueError("Номер карты должен состоять ровно из 16 цифр.")`.
- Duplicate Check: Queries `SELECT 1 FROM cards WHERE card_number == clean AND user_id != user_id`. If exists, raises `ValueError("Этот номер карты уже занят другим клиентом. Пожалуйста, выберите другой.")`.
- Output: Updates `card.card_number` to `clean` and returns `Card`.

### AdminService.cleanup_test_users
- Signature: `async def cleanup_test_users(cls, session: AsyncSession) -> dict[str, int]`
- Targets: Users with `telegram_id` in `{123, 8881001, 8881002, 999888777}` or matching test username patterns, EXCLUDING `1302123809` and all IDs in `settings.ADMIN_IDS`.
- Order: Prunes `AdminLog` -> `LedgerEntry` -> `Transaction` -> `Card`/`SavingsAccount`/`InvestmentPortfolio`/`UserCashbackSelection`/`UserJob`/`UserProperty` -> `Account` -> `User`.
- Returns: Count dictionary of deleted records.

### AdminService.cheat_balance & cheat_cashback
- Signatures:
  `async def cheat_balance(cls, session: AsyncSession, admin_telegram_id: int, target_user_id: int, amount: Decimal) -> Transaction`
  `async def cheat_cashback(cls, session: AsyncSession, admin_telegram_id: int, target_user_id: int, amount: Decimal) -> Decimal`
- Balance: Atomically increments `Account.balance`, creates `Transaction(tx_type="ADMIN_CHEAT")`, `LedgerEntry(direction="CREDIT")`, and `AdminLog`.
- Cashback: Atomically increments `User.cashback_balance` and records `AdminLog`.

### AdminService.reset_cooldowns
- Signature: `async def reset_cooldowns(cls, session: AsyncSession, admin_telegram_id: int, target_user_id: int) -> dict[str, Any]`
- Actions:
  - Wheel: `user.last_daily_spin_at = None`
  - Job shifts: `job.shift_started_at = now - timedelta(hours=3)` for all active shifts on `is_on_shift=True`
  - Rentals: `prop.last_rent_collected_at = now - timedelta(hours=25)` for all `REAL_ESTATE` properties
  - Hustle: `user.last_hustle_at = None`
  - Records `AdminLog(action="RESET_COOLDOWNS")`.

### AdminService.grant_property & grant_stocks
- Signatures:
  `async def grant_property(cls, session: AsyncSession, admin_telegram_id: int, target_user_id: int, item_id: str) -> UserProperty`
  `async def grant_stocks(cls, session: AsyncSession, admin_telegram_id: int, target_user_id: int, ticker: str, shares: int = 1000) -> InvestmentPortfolio`
- Property: Checks existing `UserProperty(user_id, item_id)`. If exists, returns existing; if not, inserts with `purchase_price=Decimal("0.00")`. Records `AdminLog`.
- Stocks: Ensures stock exists, increments `InvestmentPortfolio.shares += shares` (or creates portfolio with `shares`), records `AdminLog`.

### AdminService.manipulate_market & set_stock_price
- Signatures:
  `async def manipulate_market(cls, session: AsyncSession, admin_telegram_id: int, action: str) -> list[Stock]`
  `async def set_stock_price(cls, session: AsyncSession, admin_telegram_id: int, ticker: str, target_price: Decimal) -> Stock`
- Multipliers: `PUMP` (* 1.50), `DUMP` (* 0.50), `CRASH` (* 0.20).
- Price floor: Every price is clamped to `max(Decimal("1.00"), price.quantize(Decimal("0.01")))`.
- Records `AdminLog`.

### Telegram Callbacks Contract (strictly <= 64 bytes)
- `card:change_number` (18 bytes)
- `card:change:cancel` (18 bytes)
- `admin:cheat:menu` (16 bytes)
- `admin:cheat:rub:<uid>:<amt>` (max 37 bytes)
- `admin:cheat:cb:<uid>:<amt>` (max 31 bytes)
- `admin:cheat:ffwd:<uid>` (max 27 bytes)
- `admin:grant:menu` (16 bytes)
- `admin:grant:prop:<uid>:<item>` (max 44 bytes)
- `admin:grant:stock:<uid>:<ticker>` (max 33 bytes)
- `admin:market:menu` (17 bytes)
- `admin:market:pump` (17 bytes), `admin:market:dump` (17 bytes), `admin:market:crash` (18 bytes)
- `admin:cleanup:test_users` (24 bytes), `admin:cleanup:confirm` (21 bytes)

## Code Layout
- `app/database/models.py`: Card named unique constraint.
- `app/services/card.py`: `change_card_number` method.
- `app/services/transfers.py`: 16-digit card lookup without forced Luhn rejection.
- `app/services/admin.py`: `cleanup_test_users`, cheats, cooldowns, grants, market manipulation.
- `app/bot/states/states.py`: `CardState` group.
- `app/bot/keyboards/card.py`: card inline keyboards with change number and cancel.
- `app/bot/keyboards/admin.py`: cheat submenus, grant menus, market menus, cleanup confirmation.
- `app/bot/formatters/admin.py`: formatters for cheat, market, and grant status.
- `app/bot/routers/card.py`: FSM flow for card customization.
- `app/bot/routers/admin.py`: handlers for all admin superpower callbacks.
- `tests/test_card_custom_and_admin_superpowers.py`: automated test suite.
- `README.md`: feature documentation.
