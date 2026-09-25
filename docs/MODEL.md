# Model

## Fixed domain rules

- Direction: SHORT only.
- Market: linear USDT perpetual futures only.
- Universe: current catalogue eligibility and economic-contract identity rules in backend discovery/validation.
- Cross-exchange evidence must refer to the same economic contract.
- PRE-TRIGGER/ARMED timing is preferred to chasing an extended move.
- Anti-chase is mandatory.
- Under `entry_policy_v1`, `ENTRY_READY >= 70`, `FORMING >= 55`, and the Anti-Chase boundary is `2.5 ATR`.
- Anti-Chase is applied only after freshness/invalidator checks and readiness classification: it converts otherwise `FORMING`, `ENTRY_READY`, or `ACTIVE` evidence to `LATE`, but does not turn sub-`FORMING` evidence into `LATE`.
- `LATE` cause is auditable through `late_origin` (`ANTI_CHASE` or `LIFECYCLE_EXHAUSTED`); `lifecycle_state` remains the current observed lifecycle rather than being frozen by a terminal decision.
- Missing/stale data lowers coverage or blocks only where explicitly mandatory.

## Evidence priority

Price structure and timing, OI, aggressive trade flow/CVD, funding/crowding, observed liquidation flow, liquidity/order-book conditions, cross-exchange agreement, and relative weakness feed one canonical packet.

Cascade Intelligence uses observed public/exchange-native evidence. An estimated future liquidation zone must be labelled estimated; it is never represented as a venue-observed heatmap fact.

## Quantity versus quality

Weak optional evidence reduces readiness instead of creating universal all-red gates. Objective safety conditions remain hard invalidators. The UI exposes at most 3 `ENTRY_READY` and 6 nearest `FORMING` setups.

## Calibration boundary

`entry_readiness` is a versioned evidence/readiness score, not a guaranteed probability. New evidence or thresholds require replay, walk-forward/holdout evaluation, and explicit promotion evidence before becoming authoritative.

Correctness fixes that restore the documented policy do not silently change calibration. They still require regression evidence, documentation in the same pull request, and an entry in the [Model Change Ledger](MODEL_CHANGELOG.md).
