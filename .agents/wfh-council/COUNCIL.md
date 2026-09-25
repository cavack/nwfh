# WaterfallHunter Senior Agent Council

Council v2 coordinates the existing canonical WaterfallHunter skills and the skill-system curator. It is an orchestration contract, not a second skill system and not a source of model truth.

## Invocation order

1. Resolve current target branch/SHA and open PRs/issues.
2. Run `python scripts/wfh_council.py validate --json`.
3. Run `python scripts/wfh_council.py route <task_type> --json`.
4. Resolve each routed skill with **canonical precedence**: `skills/waterfallhunter/<skill>/SKILL.md` is authoritative; `.agents/skills/<skill>/SKILL.md` is a discovery adapter only. Validate that both declared paths required by the repository contract exist, then read completely the selected canonical `SKILL.md` before proceeding. Never let an adapter override canonical content.
5. Collect current source/runtime/data evidence before accepting historical findings.
6. Execute each owner handoff with explicit evidence classification.
7. Route code completion through `regression_lead`.
8. Route deployment states only through `release_certifier`.

## Role roster

- `chief_orchestrator` — task graph, current-state resolution, evidence taxonomy.
- `architecture_adversary` — coupling, dead code, ownership and architectural debt.
- `market_evidence_forensics` — market identity, timestamps, source quality and microstructure.
- `strategy_owner` — ScoreV2, EntryDecision, lifecycle, gates, Anti-Chase and leverage semantics.
- `quant_validation_lead` — replay, outcomes, WFO/OOS, robustness and promotion evidence.
- `false_positive_hunter` — attacks accepted signals and identifies misleading evidence combinations.
- `false_negative_hunter` — attacks rejected near-misses and identifies unnecessary selectivity.
- `runtime_lead` — latency, memory, concurrency, backpressure and soak evidence.
- `backend_data_lead` — services, workers, persistence, migrations and data architecture.
- `contract_guardian` — API/Pydantic/OpenAPI/SSE and generated consumer contracts.
- `decision_ux_lead` — evidence-first dashboard, mobile/browser behavior and accessibility.
- `security_lead` — supply chain, secrets, containers and repository security.
- `incident_telemetry_lead` — metrics, logs, alerts, SLOs and recurrence prevention.
- `skill_system_curator` — audits skill triggers, ownership, handoffs, adapters and validators without taking domain authority.
- `capability_scout` — inventories runtime tool/plugin/MCP capabilities and records authorization state without guessing.
- `adversarial_prompt_tester` — pressure-tests skill/Council behavior for unsafe shortcuts, conflicts and overclaims.
- `research_librarian` — imports external documentation/research as provenance-bound hypotheses, never repository truth.
- `regression_lead` — RED→GREEN evidence, neighboring regressions and exact-artifact verification.
- `release_certifier` — sole authority for release/deployment/production certification states.

## Evidence taxonomy

Every finding must be one of:

- `VERIFIED_FACT` — directly established from current source/runtime/data evidence.
- `REPRODUCED_DEFECT` — demonstrated with a concrete failing reproduction.
- `INFERENCE` — reasoned conclusion not yet reproduced.
- `DEBT` — maintainability/quality weakness without demonstrated correctness failure.
- `PROPOSAL` — recommended experiment/change not yet implemented and validated.

Historical audit claims never silently become current facts. Missing market data is unavailable evidence, not bearish or bullish evidence.

## Model-optimization route

`chief_orchestrator → market_evidence_forensics → strategy_owner → quant_validation_lead → false_positive_hunter → false_negative_hunter → regression_lead → release_certifier`

The first four roles build a causal candidate; the adversaries try to falsify it; Regression verifies any implementation; Release controls promotion/deployment states.
## Scientific operating loop

1. Freeze champion/base SHA/config and immutable dataset hashes.
2. Build point-in-time data and label integrity report.
3. Attribute accepted losers and rejected winners before parameter search.
4. Preregister challenger mechanisms, fields, search bounds and falsifiers.
5. Search only development/walk-forward folds; retain every trial in a ledger.
6. Reject candidates with leakage, low outcome coverage, unstable parameter cliffs or concentration.
7. Compare stable Pareto candidates without touching the final holdout.
8. Open one untouched chronological final evaluation only after selection is frozen.
9. Require shadow evidence before strategy promotion.
10. Use normal regression/review/release certification for any promoted code/config.

## Protected safety

Council v1 cannot authorize live orders. `LIVE_TRADING_ENABLED=false` remains mandatory. It cannot manufacture `ENTRY_READY` from lifecycle state, cannot turn missing data into direction, cannot bypass immutable provenance or persistence-before-notification, and cannot duplicate canonical decision logic in the frontend.

The protected `78/55/1.2 ATR` policy may be researched only through Strategy + Quant ownership and remains authoritative until a separately validated promotion completes.

## Terminal outcomes

- `PROMOTED_CHAMPION` — reserved for a challenger that survives scientific, shadow and release gates.
- `NO_PROMOTION_EVIDENCE` — the correct terminal state when data, uncertainty, costs, stability or runtime evidence is insufficient.

A backtest-only configuration is never described by the Council as guaranteed profitable or globally best.

## Council v2 capability rule

Tool presence and tool authorization are separate facts. Council v2 records `AVAILABLE`, `AUTHORIZED_READ`, `AUTHORIZED_WRITE`, `UNAVAILABLE`, or `BLOCKED` where the execution environment exposes that distinction. A convenient MCP/plugin never receives production mutation authority from the manifest.

## Mission continuity route

The exact phrase `ادامه کار گروهی` resolves project `TWFH` / repository `cavack/WFH-ORG` under protocol `wfh_mission_continuity_v1`. Route: `chief_orchestrator → capability_scout → skill_system_curator → regression_lead`. This route locates and validates durable active-mission/checkpoint state; it does not authorize domain/model/Production changes.

## Council v2 self-audit route

`chief_orchestrator → skill_system_curator → adversarial_prompt_tester → regression_lead`

Use this route for canonical skill, adapter, validator, routing, hook, or Project Source changes. Domain-semantic findings hand off to their owning specialist rather than being rewritten by the curator.
