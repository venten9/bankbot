# Project: T-Bets & High-Stakes Arena in «Заработок» Hub

## Architecture
- **Data Layer** (`app/database/`):
  - `SportsMatch` model in `app/database/models.py`: UUID PK, sport (`FOOTBALL`, `ESPORTS`), `team_home`, `team_away`, ratings, odds (`odds_home`, `odds_draw`, `odds_away`, `odds_over`, `odds_under`), status (`SCHEDULED`, `LIVE`, `FINISHED`, `CANCELLED`), scores, JSON `events_log`, timestamps.
  - `SportsBet` model in `app/database/models.py`: UUID PK, `user_id` (FK `users.id` cascade), `match_id` (FK `sports_matches.id` cascade), `bet_type` (`HOME`, `DRAW`, `AWAY`, `OVER_2_5`, `UNDER_2_5`), `odds`, `amount` (100.00 to 20,000,000.00 ₽), `potential_payout`, `status` (`PENDING`, `WON`, `LOST`, `REFUNDED`), `created_at`.
  - `PvPDuel` model in `app/database/models.py`: UUID PK, `game_type` (`DICE`, `COINFLIP`, `ROULETTE`), `creator_user_id` (FK `users.id` cascade), `opponent_user_id` (FK `users.id` set null, nullable), `stake` (100.00 to 20,000,000.00 ₽), `status` (`OPEN`, `ACCEPTED`, `FINISHED`, `CANCELLED`), `creator_roll`, `opponent_roll`, `winner_user_id`, timestamps.
  - Disambiguated relationships on `User`: `sports_bets` and `created_duels` (`foreign_keys="[PvPDuel.creator_user_id]"`).
  - Cascade update in `AdminService.cleanup_test_users` (`app/services/admin.py`) to prune bets and duels.
- **Domain Services** (`app/services/`):
  - `BookmakerService` (`app/services/bookmaker.py`):
    * Real teams catalog: 12 Football (Champions League & RPL) + 6 Esports (CS2 & Dota 2) with realistic rosters and ratings.
    * Poisson / xG match simulation engine with home advantage and 5% bookmaker margin odds calculation.
    * 90-minute chronological event log generator (goals, saves, cards, clutch rounds).
    * Bet placement with 100 ₽ - 20,000,000 ₽ limits, atomic balance debits (`tx_type="BET_PLACED"`), double-entry ledger.
    * Match settlement: evaluation of winning bets, atomic balance payouts (`amount * odds`, `tx_type="BET_WIN"`), fiscal receipts.
  - `DuelsService` (`app/services/duels.py`):
    * Dice (`🎲`): 1d6 roll; higher roll wins pot minus 3% bank commission (`tx_type="DUEL_WIN"`), draw refunds both players (`tx_type="DUEL_REFUND"`).
    * Coinflip (`🪙`): 50/50 heads or tails; winner takes pot minus 3% commission.
    * Russian Roulette (`🔫`):
      - Solo survival mode ladder: 5 steps (x2, x4, x8, x16, x32), cash out or pull trigger.
      - 2-player Russian Roulette duel: turn-based cylinder pulling, survivor wins pot minus 3% commission.
    * Escrow staking: creator stake debited upfront; atomic CAS acceptance (`OPEN` -> `ACCEPTED`), opponent stake debited; winner paid pot minus 3%.
    * Cancel open duel: atomic CAS (`OPEN` -> `CANCELLED`), creator stake refunded.
- **Bot UI & Hub Integration** (`app/bot/`):
  - Reply keyboard (`app/bot/keyboards/reply.py`): Rename button to `[💼 Заработок]` using `CompatibleZarabotokButtonText` for 100% backward compatibility with legacy tests.
  - Hub dashboard (`app/bot/keyboards/jobs.py` & `app/bot/formatters/jobs.py`): integrate portal buttons `[💼 Мои работы и смены]`, `[⚡️ Быстрая шабашка]`, `[⚽️ Букмекер (Ставки до 20M ₽)]`, `[🎲 PvP Дуэли и Азарт]`.
  - Gambling keyboards (`app/bot/keyboards/gambling.py`): match list, odds grid, bet presets (10k, 100k, 1M, 5M, 20M, custom), duels lobby, stake presets, roulette actions. Strictly <= 51 bytes callback data.
  - Gambling formatters (`app/bot/formatters/gambling.py`): match cards, 90-min commentary, betting tickets, duels lobby, results, roulette survival with `html.escape(..., quote=False)`.
  - Gambling router (`app/bot/routers/gambling.py`): registered in `app/bot/bot.py` and exported in `app/bot/routers/__init__.py`.
- **Testing & Verification** (`tests/`):
  - Automated test suite in `tests/test_bookmaker_and_duels.py` covering Tiers 1-4.
  - 100% test pass on existing 825+ tests with 0 regressions.
  - Updated `README.md`.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | SportsMatch & SportsBet Models | `SportsMatch` and `SportsBet` in `app/database/models.py` with odds & amount check constraints | M1 | ORIGINAL_REQUEST §R1 |
| 2 | PvPDuel Model & User Relationships | `PvPDuel` model with disambiguated `foreign_keys` on `User` (`created_duels`, `sports_bets`) | M1 | ORIGINAL_REQUEST §R1, Survey 1 |
| 3 | Safe Admin Test Data Cleanup Update | Cascade-delete `SportsBet` and `PvPDuel` records in `AdminService.cleanup_test_users` | M1 | Survey 1 |
| 4 | Real Teams Catalog & Ratings | 12 Football (CL & RPL) and 6 Esports (CS2 & Dota 2) teams with ratings and player rosters | M2 | ORIGINAL_REQUEST §R2 |
| 5 | Poisson & xG Match Simulation Engine | Realistic goal/round probabilities, 90-minute chronological event log generator | M2 | ORIGINAL_REQUEST §R2 |
| 6 | Bookmaker Odds Engine (5% Margin) | Decimal odds generation for 1X2 and Total 2.5 with 5% margin adjustment | M2 | ORIGINAL_REQUEST §R2 |
| 7 | Bet Placement Service | Validates 100 ₽ to 20,000,000 ₽, atomic account balance debit (`tx_type="BET_PLACED"`), ledger | M2 | ORIGINAL_REQUEST §R2 |
| 8 | Match Settlement Service | Evaluates winning bets, atomic balance payout (`amount * odds`, `tx_type="BET_WIN"`), receipts | M2 | ORIGINAL_REQUEST §R2 |
| 9 | PvP Dice Duel Engine | 1d6 rolls, pot escrow, winner pot minus 3% bank commission (`tx_type="DUEL_WIN"`), draw refunds | M3 | ORIGINAL_REQUEST §R3 |
| 10 | PvP Coinflip Engine | 50/50 heads or tails, pot escrow, 3% commission deduction | M3 | ORIGINAL_REQUEST §R3 |
| 11 | Russian Roulette Solo Survival | Ladder x2 -> x4 -> x8 -> x16 -> x32 with cash-out at any step or bang | M3 | ORIGINAL_REQUEST §R3 |
| 12 | Russian Roulette 2-Player Duel | Turn-based cylinder pulling, survivor wins pot minus 3% commission | M3 | ORIGINAL_REQUEST §R3 |
| 13 | PvP Escrow & CAS Concurrency | Atomic CAS state transition on duel acceptance preventing race conditions & double-accepts | M3 | ORIGINAL_REQUEST §R3, Survey 2 |
| 14 | Dual-Compatible Reply Button | Rename `[💼 Работа и бизнес]` to `[💼 Заработок]` via `CompatibleZarabotokButtonText` | M4 | ORIGINAL_REQUEST §R4, Survey 3 |
| 15 | Zarabotok Hub Dashboard Update | Portal buttons in `jobs.py` for My Jobs, Quick Hustles, Bookmaker, and PvP Duels | M4 | ORIGINAL_REQUEST §R4 |
| 16 | Gambling Keyboards (<= 64 bytes) | Match list, odds grid, bet presets up to 20M, duels lobby, stake presets (<51 bytes) | M4 | ORIGINAL_REQUEST §R4, Survey 3 |
| 17 | Gambling Formatters (HTML Escaping) | Match card, 90-min commentary, tickets, duels lobby & results with `html.escape` | M4 | ORIGINAL_REQUEST §R4 |
| 18 | Gambling Routers Registration | `gambling_router` registered in `bot.py` and exported in `routers/__init__.py` | M4 | ORIGINAL_REQUEST §R4 |
| 19 | Automated Test Suite (Tier 1-4) | Comprehensive test suite in `tests/test_bookmaker_and_duels.py` | M5 | ORIGINAL_REQUEST §R5 |
| 20 | Zero Regression Verification | Pass 100% of existing 825+ tests + all new tests with 0 failures | M5 | ORIGINAL_REQUEST §R5 |
| 21 | Documentation Update | Update `README.md` documenting T-Bets and PvP High-Stakes Arena | M5 | ORIGINAL_REQUEST §R5 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Database Models & Schema | `app/database/models.py`, `app/database/__init__.py`, `app/services/admin.py` | none | DONE |
| M2 | Sports Bookmaker Domain Service | `app/services/bookmaker.py` (Catalog, Simulation, Odds, Bets, Settlement) | M1 | DONE |
| M3 | PvP Duels Domain Service | `app/services/duels.py` (Dice, Coinflip, Russian Roulette, Escrow, CAS) | M1 | DONE |
| M4 | Telegram UI & Hub Integration | `app/bot/keyboards/`, `app/bot/formatters/`, `app/bot/routers/`, `bot.py` | M2, M3 | DONE |
| M5 | Automated Tests & Verification | `tests/test_bookmaker_and_duels.py`, full pytest pass, `README.md` | M4 | IN_PROGRESS |

## Interface Contracts

### BookmakerService.place_bet
- Signature: `async def place_bet(cls, session: AsyncSession, user_id: int, match_id: str, bet_type: str, amount: Decimal) -> SportsBet`
- Validation:
  - `Decimal("100.00") <= amount <= Decimal("20000000.00")`, else raises `ValueError("Сумма ставки должна быть от 100 ₽ до 20 000 000 ₽.")`.
  - `bet_type in ("HOME", "DRAW", "AWAY", "OVER_2_5", "UNDER_2_5")`.
  - Match must exist and `status == "SCHEDULED"`.
- Balance: Atomic debit on `Account.balance >= amount`. If `rowcount == 0` raises `InsufficientFundsError`.
- Records: `Transaction(tx_type="BET_PLACED")`, `LedgerEntry(direction="DEBIT")`, `SportsBet(status="PENDING")`.

### BookmakerService.settle_match
- Signature: `async def settle_match(cls, session: AsyncSession, match_id: str) -> list[SportsBet]`
- Evaluates winning bets:
  - If WON: credits `Account.balance += bet.potential_payout`, records `Transaction(tx_type="BET_WIN")`, `LedgerEntry(direction="CREDIT")`, sets `bet.status = "WON"`.
  - If LOST: sets `bet.status = "LOST"`.
  - If CANCELLED: refunds `bet.amount`, records `Transaction(tx_type="BET_REFUND")`, sets `bet.status = "REFUNDED"`.

### DuelsService.create_duel & cancel_duel
- Signatures:
  `async def create_duel(cls, session: AsyncSession, creator_user_id: int, game_type: str, stake: Decimal) -> PvPDuel`
  `async def cancel_duel(cls, session: AsyncSession, duel_id: str, user_id: int) -> PvPDuel`
- `create_duel`: Validates stake ($100 \dots 20\,000\,000$ ₽), debits creator stake into escrow (`tx_type="DUEL_STAKE"`), inserts `PvPDuel(status="OPEN")`.
- `cancel_duel`: Atomic CAS `status == "OPEN" -> "CANCELLED"`. If successful, refunds stake to creator (`tx_type="DUEL_REFUND"`).

### DuelsService.accept_duel
- Signature: `async def accept_duel(cls, session: AsyncSession, duel_id: str, opponent_user_id: int) -> tuple[PvPDuel, dict[str, Any]]`
- Concurrency Guard: Atomic CAS `update(PvPDuel).where(id==duel_id, status=="OPEN", creator_user_id!=opponent_user_id).values(status="ACCEPTED", opponent_user_id=opponent_user_id)`.
- If `rowcount == 0`: raises `ValueError("Дуэль уже принята или недоступна.")`.
- Debits opponent stake into escrow (`tx_type="DUEL_STAKE"`).
- Resolves game (Dice, Coinflip, Roulette).
- Calculates 3% bank commission: `commission = (pot * Decimal("0.03")).quantize(Decimal("0.01"))`.
- Credits winner net payout: `payout = pot - commission` (`tx_type="DUEL_WIN"`).
- On Draw: refunds both creator and opponent stake (`tx_type="DUEL_REFUND"`).

### Telegram Callbacks Contract (strictly <= 64 bytes)
- `bet:menu` (8 bytes)
- `bet:f:<sport>` (max 14 bytes)
- `bet:m:<uuid>` (42 bytes)
- `b:pk:<uuid>:<oc>` (44 bytes)
- `b:st:<uuid>:<oc>:<preset>` (max 48 bytes)
- `b:sim:<uuid>` (42 bytes)
- `bet:my` (6 bytes)
- `duel:menu` (9 bytes)
- `d:f:<game>` (max 12 bytes)
- `d:create` (8 bytes)
- `d:new:<game>:<preset>` (max 18 bytes)
- `d:view:<uuid>` (43 bytes)
- `d:acc:<uuid>` (42 bytes)
- `d:cnl:<uuid>` (42 bytes)
- `d:rr:menu` (9 bytes), `d:rr:stk:<preset>` (14 bytes), `d:rr:pull` (9 bytes), `d:rr:cash` (9 bytes)

## Code Layout
- `app/database/models.py`: `SportsMatch`, `SportsBet`, `PvPDuel` models, `User` relationships.
- `app/database/__init__.py`: Exports new models.
- `app/services/admin.py`: Cascade cleanup update in `cleanup_test_users`.
- `app/services/bookmaker.py`: Teams catalog, Poisson simulation, odds engine, bet placement & settlement.
- `app/services/duels.py`: Dice, Coinflip, Russian Roulette solo ladder & 2-player duel, escrow & commission.
- `app/bot/keyboards/reply.py`: `CompatibleZarabotokButtonText("💼 Заработок")`.
- `app/bot/keyboards/jobs.py` & `formatters/jobs.py`: «Заработок» hub portal dashboard.
- `app/bot/keyboards/gambling.py`: Bookmaker & PvP Duels inline keyboards.
- `app/bot/formatters/gambling.py`: Rich cards, 90-minute commentary, receipts, HTML escaping.
- `app/bot/routers/gambling.py`: Telegram message & callback handlers.
- `app/bot/bot.py` & `app/bot/routers/__init__.py`: Router registration and export.
- `tests/test_bookmaker_and_duels.py`: Automated test suite covering Tiers 1-4.
- `README.md`: Feature documentation.
