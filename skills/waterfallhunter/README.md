# WaterfallHunter Engineering Skill System

Repository-local engineering skills for `cavack/WFH-ORG`. The system has one orchestrator, twelve domain specialists, and one skill-system curator (fourteen canonical skills total). It is designed to keep repository evidence fresh, separate model/scientific/release concerns, and prevent incidental changes to WaterfallHunter decision semantics.

## Skill index

| Skill | Primary trigger example |
|---|---|
| `engineering-orchestrator` | "Audit and fix this WaterfallHunter issue end-to-end." |
| `repository-architecture-auditor` | "Review repository structure, coupling, dead code, or architecture debt." |
| `runtime-reliability-performance` | "Investigate OOM, SSE memory, concurrency, latency, or soak regressions." |
| `backend-data-architecture` | "Refactor FastAPI/services/workers/SQLite/migrations or assess scale design." |
| `api-contract-schema-guardian` | "Change or validate API/Pydantic/OpenAPI/SSE/dashboard contracts." |
| `frontend-dashboard-ux` | "Improve Next.js dashboard behavior, UX, mobile, accessibility, or render performance." |
| `strategy-score-lifecycle` | "Change or audit ScoreV2, lifecycle, anti-chase, ranking, leverage, or eligibility." |
| `scientific-backtest-validation` | "Evaluate backtests, holdout, walk-forward, leakage, robustness, or promotion evidence." |
| `market-data-evidence-quality` | "Validate exchange data, freshness, timestamps, derivatives, order book, or cross-exchange evidence." |
| `verification-regression` | "Prove this bugfix/refactor/feature is complete without regressions." |
| `security-supply-chain` | "Review vulnerabilities, dependencies, secrets, containers, SBOM, GitHub security, or abuse surfaces." |
| `observability-incident-response` | "Investigate an incident or improve metrics, alerts, tracing, logging, or SLOs." |
| `release-production-certification` | "Prepare or certify merge, migration, deployment, rollback, or production verification." |
| `skill-system-curator` | "Audit or evolve the skill system, Council routes, adapters, tool assumptions, and behavioral tests." |

## Discovery adapters

Canonical skill bodies live under `skills/waterfallhunter/<skill-name>/SKILL.md`. Repository discovery is exposed through thin adapters under `.agents/skills/<skill-name>/SKILL.md`.

Every adapter must load this README and then its matching canonical skill before acting. The canonical file remains authoritative; adapters must not duplicate an independent workflow. This keeps auto-discovery compatible with agent environments without maintaining two divergent skill systems.

## Shared evidence taxonomy

Repository-grounded conclusions use these labels when material:

- `VERIFIED_FACT` — directly established from current source/runtime evidence.
- `REPRODUCED_DEFECT` — behavior reproduced or otherwise demonstrated with concrete failing evidence.
- `INFERENCE` — reasoned conclusion not yet directly reproduced.
- `DEBT` — maintainability/architecture/quality weakness without demonstrated correctness failure.
- `PROPOSAL` — recommended change not yet implemented/validated.

## Freshness rule

Before trusting a prior audit, bug report, issue, or design-time observation, resolve the current target branch/SHA and inspect relevant open PRs, issues, and recent commits. Historical findings are context, not permanent truth.

When external repository evidence such as GitHub PRs or issues is unavailable, record that limitation and continue from locally available branch/SHA and recent-commit evidence. Missing optional integrations must reduce confidence, not make ordinary repository-grounded work impossible.

## Protected invariants

Infrastructure, UX, performance, refactor, observability, security, and release work must not silently change:

- ScoreV2 weights or evidence semantics;
- lifecycle transition semantics;
- strict/experimental eligibility boundaries;
- anti-chase behavior;
- signal metadata/provenance and immutable-ledger behavior;
- persistence-before-notification ordering;
- scientific holdout/walk-forward rules;
- production execution policy.

A required change to these boundaries is explicitly routed through `strategy-score-lifecycle` and, for promotion/evidence claims, `scientific-backtest-validation`.

## Stop and escalation conditions

- Stop before changing a protected invariant unless that model/policy change is separately authorized and routed to the owning strategy/scientific skills.
- Stop before any live-order implementation or enablement. The current skill system has no authority to design, authorize, implement, or enable live order placement.
- Escalate production-readiness declarations to `release-production-certification`; other skills may report evidence and non-production readiness only.
- Record unavailable optional tools or remote evidence explicitly and continue with local evidence when the task remains verifiable locally.
- Stop rather than guess when required source-of-truth files, artifact identity, or a safety-critical prerequisite cannot be established.

## Routing examples

### Runtime bug

`engineering-orchestrator` → `runtime-reliability-performance` → `verification-regression` → `observability-incident-response` when runtime evidence is involved → `release-production-certification` if the change reaches deployment.

### Frontend/dashboard bug

`engineering-orchestrator` → `api-contract-schema-guardian` when payload semantics are involved → `frontend-dashboard-ux` → `verification-regression`.

### Strategy/model change

`engineering-orchestrator` → `market-data-evidence-quality` → `strategy-score-lifecycle` → `scientific-backtest-validation` → `verification-regression` → `release-production-certification`.

### Refactor

`engineering-orchestrator` → `repository-architecture-auditor` → `backend-data-architecture` or `frontend-dashboard-ux` → `api-contract-schema-guardian` when interfaces are affected → `verification-regression`.

### Production incident

`engineering-orchestrator` → `observability-incident-response` → indicated runtime/backend/frontend specialist → `verification-regression` → `release-production-certification`.

## Correct invocation and failure examples

Correct: a dashboard ranking mismatch that may involve payload semantics routes through the orchestrator, API contract guardian, frontend specialist, and regression verification without copying ranking logic into React.

Failure: accepting an old issue as current truth without resolving the present SHA, silently changing a ScoreV2 threshold during UI work, treating unavailable evidence as failed evidence, or calling CI success equivalent to production verification.

## Readiness ownership

The system uses:

`NOT_READY` → `ANALYSIS_COMPLETE` → `CODE_READY` → `MERGE_READY` → `DEPLOY_READY` → `DEPLOYED_UNVERIFIED` → `PRODUCTION_VERIFIED`.

Only `release-production-certification` may declare the final three production states. Passing a targeted test or CI alone never equals production verification.

## External tools and plugins

External tools are optional evidence/capability sources, not hard dependencies:

- GitHub is preferred for repository/PR/CI truth when connected.
- CodeRabbit, Sonar/CodeQL, Sentry, Prometheus/Grafana, Linear, StrategyTune and similar tools may strengthen specialist evidence when available.
- Firecrawl, Parallel Search and web research are for external documentation/research, not substitutes for repository evidence.
- Skillquiver/Duende-style libraries may inform skill-authoring patterns but are not required to run WaterfallHunter logic.
- GeoAI-style skills are outside the core system unless a concrete geospatial or infrastructure-latency requirement exists.

Unavailable plugins must not block core repository work.

## Behavioral validation

See `tests/README.md` and `tests/scenarios.md`. Every skill is pressure-tested RED/GREEN with a fresh conversation/context in addition to repository-local static validation.

Post-review clarifications to the original design/implementation-plan snapshots are recorded in `docs/superpowers/2026-08-27-wfh-skill-system-review-errata.md`.

## Safety boundary

Under the current repository policy, this skill system must not authorize, design, implement, or enable live order placement. A user request alone is not sufficient. Any future execution capability first requires a separately reviewed safety design and an explicit repository-policy change; only after those changes exist may ordinary release gates apply.

The skill system also does not silently authorize model-threshold changes, Telegram delivery-policy changes, production migrations, merges, or deployments; those require the relevant explicit request, domain ownership, and release gates.
