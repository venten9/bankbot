# E2E Test Infra: T-Bank Telegram Neobank Bot

## Test Philosophy
- **Requirement-Driven & Opaque-Box**: Tests are derived strictly from `ORIGINAL_REQUEST.md` and public API contracts, without depending on internal implementation private details.
- **Methodology**: 4-Tier test suite utilizing Category-Partition, Boundary Value Analysis (BVA), Pairwise Combinatorial Testing, and Realistic Neobank Workloads.
- **Offline & Deterministic**: Zero live Telegram token required. All bot interactions are tested via `AsyncMock(spec=Bot)` with message/callback query offline execution and in-memory SQLite transactions.
- **Execution Command**: `py -m pytest tests/ -v` (Must pass with 100% pass rate, 0 failures, 0 errors).

---

## Feature Inventory Coverage Matrix
| # | Feature | Source (Requirement) | Tier 1 (Unit Math) | Tier 2 (Boundary & Concurrency) | Tier 3 (Cross-Feature) | Tier 4 (Workload/E2E) |
|---|---------|----------------------|:------------------:|:-------------------------------:|:----------------------:|:---------------------:|
| 1 | Luhn 16-Digit Card Gen/Val | R1 § 1 | 5 | 5 | ✓ | ✓ |
| 2 | Card Freeze / Unfreeze | R1 § 1 | 5 | 5 | ✓ | ✓ |
| 3 | P2P & SBP Transfers | R1 § 2 | 5 | 5 | ✓ | ✓ |
| 4 | Double-Entry & Concurrency | R1 § 3 | 5 | 10 (concurrency tasks) | ✓ | ✓ |
| 5 | Savings 18% APR & Accrual | R1 § 4 | 5 | 5 | ✓ | ✓ |
| 6 | Parody Stocks & Dynamic GBM | R1 § 5 | 5 | 5 | ✓ | ✓ |
| 7 | Monthly Cashback (4 cats) | R1 § 6 | 5 | 5 | ✓ | ✓ |
| 8 | Virtual Assistant "Олег" | R1 § 7 | 5 | 5 | ✓ | ✓ |
| 9 | Receipts & Expense Bars | R1 § 8 | 5 | 5 | ✓ | ✓ |
| 10 | /admin RBAC & Access Control | R2 § 1 | 5 | 5 | ✓ | ✓ |
| 11 | System Metrics Aggregation | R2 § 2 | 5 | 5 | ✓ | ✓ |
| 12 | User Mgmt & Reason Adjustments | R2 § 3 | 5 | 5 | ✓ | ✓ |
| 13 | Broadcast Engine & Summary | R2 § 4 | 5 | 5 | ✓ | ✓ |

---

## Test Architecture
- **Test Runner**: `pytest` with `pytest-asyncio` plugin (asyncio_mode = "auto").
- **Invocation**: `py -m pytest tests/ -v`
- **Pass/Fail Semantics**: 0 failures, 0 errors, non-zero return code fails the gate.
- **Directory Layout**:
  ```
  tests/
  ├── conftest.py                   (Async SQLite fixtures, mock_bot, FSM storage, sample users)
  ├── test_math_unit.py             (Tier 1: Luhn algorithm, APR interest formulas, GBM math, cashback percentages)
  ├── test_banking_concurrency.py   (Tier 2: Concurrency races, double-entry balance updates, overdraft prevention)
  ├── test_admin_operations.py      (Tier 3: RBAC security, admin user search, reason logging, broadcast engine)
  └── test_bot_e2e_handlers.py      (Tier 4: Offline aiogram handlers, menu navigation, FSM flows, receipts)
  ```

---

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Expected Outcome |
|---|----------|--------------------|------------------|
| 1 | New User Onboarding & Card Issuance | F1, F2, F4, F5 | Valid 16-digit Luhn card created with 0 initial balance, user profile registered |
| 2 | High-Speed P2P Transfer & Notification | F6, F10, F11, F12 | Sender debited, recipient credited, recipient receives push alert, receipt generated |
| 3 | Concurrent Overdraft Stress (Race Condition) | F11, F12 | 10 concurrent transfers of 200 ₽ on 500 ₽ balance: exactly 2 succeed, 8 fail with insufficient funds, final balance 100 ₽ |
| 4 | Savings Deposit, Accrual & 1-Click Payout | F15, F16, F18, F19 | Funds moved to savings, interest accumulates at 18% APR, atomic payout transfers interest to main account |
| 5 | Stock Market Dynamic Trade & Liquidation | F20, F21, F22, F23, F24 | Price ticks via GBM, user buys shares (fee deducted), price ticks again, user sells shares, balance reflects profit/loss |
| 6 | Cashback Selection & Shopping Payout | F25, F26, F27 | User selects 4 categories, makes purchase in category, bonus rubles accrue, 1-click payout credits main card |
| 7 | Admin Inspection, Reason Adjustment & Freeze | F34, F36, F40, F44, F42 | Admin /admin opens dashboard, credits 10,000 ₽ with reason, user receives alert, admin freezes user |
| 8 | Admin Emergency Broadcast with Summary | F48, F49, F50 | Admin composes broadcast, messages dispatched with rate-limiting, delivery summary reports exact count |

---

## Coverage Thresholds
- **Tier 1 (Feature Coverage)**: >= 65 test cases (>= 5 per feature domain)
- **Tier 2 (Boundary & Concurrency)**: >= 65 test cases / scenarios
- **Tier 3 (Cross-Feature Combinations)**: >= 15 pairwise interaction tests
- **Tier 4 (Real-World Application Scenarios)**: >= 8 comprehensive end-to-end user workflows
- **Total Suite**: >= 150 automated test assertions passing with 0 failures
