# Project: T-Property & Fortune Wheel for T-Bank Telegram Neobank

## Architecture
- **Data Layer** (`app/database/`):
  - `UserProperty` model in `models.py` with unique constraint `(user_id, item_id)` and check constraint `purchase_price >= 0.00`.
  - `User` model updated with `last_daily_spin_at` and `properties` relationship.
  - `session.py` with non-destructive SQLite column migration for `last_daily_spin_at`.
  - Financial transaction types: `PROPERTY_BUY`, `RENTAL_INCOME`, `WHEEL_PRIZE`, `WHEEL_TAX`.
- **Domain Services** (`app/services/`):
  - `PropertyService` in `app/services/property.py`: Static item catalog (Cars, Real Estate, Luxury), purchase with atomic check and debit, 24h rental income collection, car buff calculations.
  - `WheelService` in `app/services/wheel.py`: 24h free spin cooldown via CAS, 1000 cashback point extra spin, 8 sectors, dynamic "USPEKH" stock creation without mutating `DEFAULT_STOCKS`, energy drink shift reset.
  - `JobService` in `app/services/jobs.py`: Integration of car cooldown reduction in `start_shift` and car salary bonus % in `claim_salary`.
- **Bot UI & Interaction** (`app/bot/`):
  - 2x4 reply keyboard in `keyboards/reply.py`.
  - Inline keyboards in `keyboards/property.py` and `keyboards/wheel.py` (strict <=64 byte callback limit).
  - Formatters in `formatters/property.py` and `formatters/wheel.py` with mandatory `html.escape(..., quote=False)`.
  - Routers in `routers/property.py` and `routers/wheel.py`, registered in `routers/__init__.py` and `bot.py`.
- **Testing & Verification** (`tests/`):
  - Full suite in `tests/test_property_wheel.py` verifying all 4 tiers of test cases.
  - Guarantee 100% pass on all 712 existing baseline tests + new tests.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | UserProperty Model | Persistence for owned cars, real estate, and luxury collectibles | M1 | ORIGINAL_REQUEST §R1 |
| 2 | User Spin Timestamp | `last_daily_spin_at` column on User with session migration | M1 | ORIGINAL_REQUEST §R1 |
| 3 | Transaction Types | `PROPERTY_BUY`, `RENTAL_INCOME`, `WHEEL_PRIZE`, `WHEEL_TAX` | M1 | ORIGINAL_REQUEST §R1 |
| 4 | Property Catalog | Static catalog of 4 cars, 5 real estate, 3 luxury items | M2 | ORIGINAL_REQUEST §R2 |
| 5 | Buy Property | Atomic purchase with unique ownership per item and debit | M2 | ORIGINAL_REQUEST §R2 |
| 6 | Rental Income | 24h cooldown passive income collection from real estate | M2 | ORIGINAL_REQUEST §R2 |
| 7 | Car Buffs in Jobs | Cooldown reduction in start_shift and salary % in claim_salary | M2 | ORIGINAL_REQUEST §R2 |
| 8 | Wheel Free Spin | 24h cooldown daily free spin | M2 | ORIGINAL_REQUEST §R3 |
| 9 | Wheel Extra Spin | Spin purchased for 1000 cashback rubles | M2 | ORIGINAL_REQUEST §R3 |
| 10 | Wheel 8 Sectors | Cash (5k, 25k, 100k), Stocks (SHAW, USPEKH), Energy Drink, Cashback, Luxury Tax | M2 | ORIGINAL_REQUEST §R3 |
| 11 | Reply Keyboard 2x4 | Updated reply keyboard layout preserving analytics | M3 | ORIGINAL_REQUEST §R4 |
| 12 | Property Bot UI | Formatters, keyboards, and router for `/property` and inline callbacks | M3 | ORIGINAL_REQUEST §R4 |
| 13 | Wheel Bot UI | Formatters, keyboards, and router for `/wheel` and inline callbacks | M3 | ORIGINAL_REQUEST §R4 |
| 14 | E2E Automated Tests | 100% automated test coverage in tests/test_property_wheel.py + 0 regressions | M4 | ORIGINAL_REQUEST §R5 |
| 15 | Documentation | Update README.md documenting the new features | M4 | ORIGINAL_REQUEST §R5 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Persistence & Schema | `UserProperty` model, `User` updates, `_migrate_columns`, Transaction types | none | DONE |
| M2 | Domain Services & Integration | `PropertyService`, `WheelService`, `JobService` car buffs integration | M1 | DONE |
| M3 | Bot UI & Routers | Keyboards, formatters, routers for property & wheel, router registration | M2 | DONE |
| M4 | E2E Testing & Verification | Comprehensive test suite (`test_property_wheel.py`), README.md, full test run | M3 | DONE |

## Interface Contracts
### PropertyService ↔ JobService
- `PropertyService.get_user_car_buffs(session: AsyncSession, user_id: int) -> tuple[int, int]`:
  - Returns `(salary_bonus_pct: int, cooldown_reduction_minutes: int)`.
  - Used in `JobService.start_shift`: adjusts `shift_started_at = now - timedelta(minutes=cooldown_reduction_minutes)`.
  - Used in `JobService.claim_salary`: computes `car_bonus = (base_salary * Decimal(salary_bonus_pct) / 100).quantize(Decimal("0.01"))`, adds to payout and description.

### WheelService ↔ JobService & Portfolio & Cashback
- `WheelService.spin_wheel(session: AsyncSession, user_id: int, is_extra: bool = False) -> WheelResult`:
  - If sector is `energy_drink`: updates `UserJob.shift_started_at = now - timedelta(seconds=7200)` for active jobs on shift.
  - If sector is `stocks_uspekh`: checks/creates `Stock(ticker="USPEKH")` in DB without mutating `DEFAULT_STOCKS`.
  - If sector is `stocks_shaurma`: uses existing ticker `SHAW`.
  - If sector is `cashback_2k`: increments `User.cashback_balance += Decimal("2000.00")`.

### Telegram Callbacks Contract (strictly <= 64 bytes)
- `prop:menu`
- `prop:cat:<cat>` (e.g. `prop:cat:CAR`, `prop:cat:REAL_ESTATE`, `prop:cat:LUXURY`)
- `prop:buy:<item_id>` (e.g. `prop:buy:vaz2107`)
- `prop:rent:claim`
- `prop:my`
- `wheel:menu`
- `wheel:spin`
- `wheel:spin_extra`

## Code Layout
- `app/database/models.py`: add `UserProperty`, update `User`.
- `app/database/session.py`: add `last_daily_spin_at` check in `_migrate_columns`.
- `app/services/property.py`: new property domain service.
- `app/services/wheel.py`: new fortune wheel domain service.
- `app/services/jobs.py`: car buffs integration.
- `app/bot/keyboards/reply.py`: 2x4 grid + analytics.
- `app/bot/keyboards/property.py`: property inline keyboards.
- `app/bot/keyboards/wheel.py`: wheel inline keyboards.
- `app/bot/formatters/property.py`: property text formatter (HTML escaped).
- `app/bot/formatters/wheel.py`: wheel text formatter (HTML escaped).
- `app/bot/routers/property.py`: property router.
- `app/bot/routers/wheel.py`: wheel router.
- `app/bot/routers/__init__.py`: exports new routers.
- `app/bot/bot.py`: registers new routers with dispatcher.
- `tests/test_property_wheel.py`: automated test suite.
- `README.md`: feature documentation.
