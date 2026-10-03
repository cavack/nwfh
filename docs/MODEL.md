# Model

## Fixed domain rules

- Direction: SHORT only.
- Market: linear USDT perpetual futures only.
- Universe: current catalogue eligibility and economic-contract identity rules in backend discovery/validation.
- Cross-exchange evidence must refer to the same economic contract.
- PRE-TRIGGER/ARMED timing is preferred to chasing an extended move.
- Anti-chase is mandatory.
- Canonical bands (exact): `ENTRY_READY >= 72`, `FORMING >= 55`.
- **Active canonical threshold (current policy hardening):** `ENTRY_READY >= 72.0`, `FORMING` is the `55.0`-`71.99` band, and the Anti-Chase boundary is `2.5 ATR`. `ENTRY_READY` additionally requires all hard gates to pass (lifecycle must be `TRIGGERED`, not `PRE-TRIGGER`; deterministic bearish-structure and support-break-close confirmation; no absorption detected; valid execution). `PRE-TRIGGER` lifecycle can never be classified `ENTRY_READY`, regardless of readiness score.
- A historical `78` threshold is documented in [`DECISION_ENGINE.md`](DECISION_ENGINE.md) and the 2026-09-01 ledger row below; it is **historical only** and is not the active policy. See the "Open: unrecorded calibration drift" section of the [Model Change Ledger](MODEL_CHANGELOG.md) for the reconciliation status of that number. `72.0` is the active policy minimum as of the 2026-09-26 policy hardening described in this ledger.
- A readiness of `85.0` or above is a **presentation/quality tier** ("ENTRY READY" title weight in notifications), not the canonical decision minimum. The canonical decision minimum for `ENTRY_READY` remains `72.0` plus all hard gates; `85.0`+ only changes how confidently a confirmed setup is presented, and requires confirmed retest-rejection evidence where that evidence exists.
- Anti-Chase is applied only after freshness/invalidator checks and readiness classification: it converts otherwise `FORMING`, `ENTRY_READY`, or `ACTIVE` evidence to `LATE`, but does not turn sub-`FORMING` evidence into `LATE`.
- `LATE` cause is auditable through `late_origin` (`ANTI_CHASE` or `LIFECYCLE_EXHAUSTED`); `lifecycle_state` remains the current observed lifecycle rather than being frozen by a terminal decision.
- Missing/stale data lowers coverage or blocks only where explicitly mandatory; missing, stale, or invalid evidence stays **fail-closed** — it is never silently treated as confirming a signal, gate, or corroborating condition.

## Evidence priority

Price structure and timing, OI, aggressive trade flow/CVD, funding/crowding, observed liquidation flow, liquidity/order-book conditions, cross-exchange agreement, and relative weakness feed one canonical packet.

Cascade Intelligence uses observed public/exchange-native evidence. An estimated future liquidation zone must be labelled estimated; it is never represented as a venue-observed heatmap fact.

Location relative to VWAP (e.g. "price is below VWAP") is **location evidence only** — it describes where price currently sits, not that a bearish structural break has actually been confirmed. It must not be substituted for deterministic structural confirmation (bearish 15m structure + support-break close).

Long-heavy positioning (an elevated top-trader long/short ratio) is **not equivalent to trapped longs**. A ratio above threshold alone only shows the market is long-dominant; calling longs "trapped" (i.e. treating it as short-entry confirmation) additionally requires deterministic bearish structure, a confirmed support-break close, and at least one independent corroborating condition (OI unwinding, funding at a positioning extreme, dominant long liquidations, or confirmed downside displacement). See `classify_long_crowding` in `cascade_intelligence.py`.

Selling activity without accompanying price displacement can indicate absorption (passive resting size absorbing aggressive sell flow) rather than genuine breakdown pressure, and must not be read as confirmation of a valid short setup on its own.

## Quantity versus quality

Weak optional evidence reduces readiness instead of creating universal all-red gates. Objective safety conditions remain hard invalidators. The UI exposes at most 3 `ENTRY_READY` and 6 nearest `FORMING` setups.

## Calibration boundary

`entry_readiness` is a versioned evidence/readiness score, not a guaranteed probability. New evidence or thresholds require replay, walk-forward/holdout evaluation, and explicit promotion evidence before becoming authoritative.

Correctness fixes that restore the documented policy do not silently change calibration. They still require regression evidence, documentation in the same pull request, and an entry in the [Model Change Ledger](MODEL_CHANGELOG.md).
