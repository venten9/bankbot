# E2E Test Infra: T-Property & Fortune Wheel

## Test Philosophy
- Opaque-box and unit-level coverage derived from user requirements in `ORIGINAL_REQUEST.md`.
- No regression tolerance: all 712 baseline tests must remain green.
- Strict non-negative balance checking and concurrency/CAS verification.

## Feature Inventory Coverage Map
| # | Feature | Requirement Source | Tier 1 (Unit) | Tier 2 (Boundary) | Tier 3 (Cross-Feature) | Tier 4 (E2E) |
|---|---------|-------------------|:-------------:|:-----------------:|:----------------------:|:------------:|
| 1 | UserProperty Model | ORIGINAL_REQUEST §R1 | ✓ | ✓ | ✓ | ✓ |
| 2 | User Spin Timestamp | ORIGINAL_REQUEST §R1 | ✓ | ✓ | ✓ | ✓ |
| 3 | Transaction Types | ORIGINAL_REQUEST §R1 | ✓ | ✓ | ✓ | ✓ |
| 4 | Property Catalog | ORIGINAL_REQUEST §R2 | ✓ | ✓ | ✓ | ✓ |
| 5 | Buy Property | ORIGINAL_REQUEST §R2 | ✓ | ✓ | ✓ | ✓ |
| 6 | Rental Income | ORIGINAL_REQUEST §R2 | ✓ | ✓ | ✓ | ✓ |
| 7 | Car Buffs in Jobs | ORIGINAL_REQUEST §R2 | ✓ | ✓ | ✓ | ✓ |
| 8 | Wheel Free Spin | ORIGINAL_REQUEST §R3 | ✓ | ✓ | ✓ | ✓ |
| 9 | Wheel Extra Spin | ORIGINAL_REQUEST §R3 | ✓ | ✓ | ✓ | ✓ |
| 10 | Wheel 8 Sectors | ORIGINAL_REQUEST §R3 | ✓ | ✓ | ✓ | ✓ |
| 11 | Reply Keyboard 2x4 | ORIGINAL_REQUEST §R4 | ✓ | ✓ | ✓ | ✓ |
| 12 | Property Bot UI | ORIGINAL_REQUEST §R4 | ✓ | ✓ | ✓ | ✓ |
| 13 | Wheel Bot UI | ORIGINAL_REQUEST §R4 | ✓ | ✓ | ✓ | ✓ |

## Test Architecture
- Test Suite Path: `tests/test_property_wheel.py`
- Test Runner: `py -m pytest tests/ -v`
- Pass/Fail Semantics: 0 failures, 0 errors, 100% pass across all tests.
