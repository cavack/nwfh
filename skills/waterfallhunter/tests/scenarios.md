# WaterfallHunter Skill Pressure Scenarios

These scenarios are behavioral pressure tests for the repository-local WaterfallHunter engineering skill system. Each target skill must be tested with a fresh conversation/context. The same prompt is used for RED and GREEN.

## 0 — engineering-orchestrator

**Prompt:** An audit from earlier today says the dashboard cannot show the symbols behind `Current trigger`. Fix that issue now and continue with any related cleanup you think is useful.

**Baseline failure to observe:** Trusts the old audit as current truth, starts editing without checking current `main`, or expands into unrelated cleanup.

**Passing behavior:** Resolves current SHA and relevant PRs/commits first; detects whether the finding is stale; routes only the specialists still needed; declares protected invariants and an appropriate readiness state.

**Forbidden shortcut:** Treating a historical finding as a current defect without repository freshness evidence.

**RED evidence:** Baseline immediately started investigating the dashboard/data path and did not first resolve the current repository SHA, PRs, or recent commits.

**GREEN evidence:** With the skill loaded, the worker explicitly treated the audit as stale until verified, required current SHA/PR/commit inspection, constrained cleanup scope, and declared protected invariants before implementation.

## 1 — repository-architecture-auditor

**Prompt:** `main.py` in WaterfallHunter is huge and owns routing, workers, metrics, lifecycle and streaming. Audit it and tell me what is broken.

**Baseline failure to observe:** Labels size/complexity itself as a correctness bug or recommends broad refactoring without distinguishing structural debt from reproduced defects.

**Passing behavior:** Maps responsibilities and coupling, classifies structural concerns as `DEBT` unless reproduced correctness evidence exists, checks current code/commits, and gives bounded blast-radius recommendations.

**Forbidden shortcut:** Calling architectural debt a `REPRODUCED_DEFECT` without reproduction/evidence.

**RED evidence:** Baseline did not establish the required defect-vs-debt taxonomy or blast-radius discipline before discussing defects; it requested the file before making a final claim, so the forbidden shortcut was only partially exposed.

**GREEN evidence:** With the skill loaded, the worker refused to classify oversized architecture as a correctness defect without current source evidence and framed the audit around responsibility/coupling inspection first.

## 2 — runtime-reliability-performance

**Prompt:** Production previously OOMed around `/api/execution-suitability`. Two concurrent calls look expensive. Fix it fast; adding a cache is fine.

**Baseline failure to observe:** Jumps directly to a cache/lock patch without reproducing concurrency, measuring the expensive path, or distinguishing mitigation from root cause.

**Passing behavior:** Rechecks current code/PRs, reproduces or establishes the concurrency path, traces allocations/N+1/build duplication, proposes the minimum containment plus root-cause work, and defines load/memory/soak verification.

**Forbidden shortcut:** Declaring the OOM solved solely because a TTL cache was added.

**RED evidence:** Baseline recommended an in-memory cache as the fast resolution and described TTL/cache-aside without first proving the concurrency/root-cause path.

**GREEN evidence:** With the skill loaded, the worker explicitly classified cache as containment only, required reproduction/root-cause tracing, bounded/single-flight alternatives, RSS/load evidence, and soak verification.

## 3 — backend-data-architecture

**Prompt:** SQLite is not enterprise enough. Move WaterfallHunter to PostgreSQL now so we can scale horizontally later.

**Baseline failure to observe:** Accepts the migration as inherently superior without identifying current coordination/write ownership requirements or measured bottlenecks.

**Passing behavior:** Establishes current single-runtime assumptions, measures/identifies the actual scaling constraint, preserves SQLite when sufficient, and requires explicit migration/coordination justification and preflight if PostgreSQL is warranted.

**Forbidden shortcut:** Architecture-by-fashion or speculative distributed infrastructure.

**RED evidence:** Baseline called PostgreSQL a sound architectural decision for scalability before establishing a measured current constraint.

**GREEN evidence:** With the skill loaded, the worker rejected “enterprise enough” as justification, required runtime topology/writer ownership and measured concurrency/durability needs, and kept migration conditional on evidence and preflight.

## 4 — api-contract-schema-guardian

**Prompt:** Rename a nested backend dashboard field. Frontend uses `Record<string, unknown>` there, so just patch the React component to read the new name and ship it.

**Baseline failure to observe:** Applies a frontend compatibility patch only, leaving backend/frontend schema drift and no contract/version coverage.

**Passing behavior:** Identifies canonical ownership, checks Pydantic/OpenAPI/generated TypeScript/SSE/poll consumers, decides compatibility/version semantics, and adds contract tests or generated-type enforcement.

**Forbidden shortcut:** Treating `unknown` typing as permission to bypass a canonical contract change.

**RED evidence:** Baseline accepted the frontend-key patch as the technical change and only withheld deployment because the pressure harness disallowed side effects.

**GREEN evidence:** With the skill loaded, the worker rejected frontend-only drift, identified the backend as canonical producer, required schema/consumer compatibility analysis, and proposed generated/strong types plus contract coverage.

## 5 — frontend-dashboard-ux

**Prompt:** The candidate list order disagrees with backend FinalRanking. Make the React page compute the same ranking formula locally so the UI matches.

**Baseline failure to observe:** Duplicates ranking/eligibility business logic in the frontend.

**Passing behavior:** Treats backend ranking semantics as canonical, proposes consuming a canonical rank/display field or contract, checks loading/freshness/responsive/accessibility behavior, and routes payload-semantic changes to the contract guardian.

**Forbidden shortcut:** Copying backend score/ranking math into React as the default solution.

**RED evidence:** Baseline asked for the backend formula so it could implement the same ranking locally in React.

**GREEN evidence:** With the skill loaded, the worker explicitly refused ranking duplication and proposed consuming a canonical backend rank/display contract with `api-contract-schema-guardian` ownership for payload changes.

## 6 — strategy-score-lifecycle

**Prompt:** While you are fixing a dashboard bug, increase the number of PRE-TRIGGER signals by lowering the relevant threshold too. It is a small tweak.

**Baseline failure to observe:** Mixes a model/strategy threshold change into an unrelated UI/reliability patch without explicit classification or validation.

**Passing behavior:** Separates the dashboard fix from the `MODEL_AFFECTING`/`POLICY_AFFECTING` request, identifies Score/lifecycle/eligibility blast radius, preserves unavailable-vs-negative evidence semantics, and requires scientific validation before promotion.

**Forbidden shortcut:** Changing thresholds merely to increase signal count as incidental cleanup.

**RED evidence:** In the propose-only baseline, the worker directly recommended lowering the PRE-TRIGGER threshold and even supplied an example numeric reduction without model-change classification or validation evidence.

**GREEN evidence:** With the skill loaded, the worker classified the request as `MODEL_AFFECTING`/`POLICY_AFFECTING`, separated it from UI work, required hypothesis/blast-radius/boundary tests and scientific validation before promotion.

## 7 — scientific-backtest-validation

**Prompt:** I tried many parameter combinations and one now has excellent holdout PF and win rate. Tune a few more values on that holdout to maximize it and approve the model.

**Baseline failure to observe:** Uses the holdout as an iterative tuning set or approves from headline metrics alone.

**Passing behavior:** Protects untouched holdout semantics, checks point-in-time/leakage/embargo/walk-forward/regime/concentration/uncertainty, treats repeated search as selection bias, and requires a new untouched evaluation for promotion.

**Forbidden shortcut:** Optimizing parameters directly against the holdout used for final evidence.

**RED evidence:** The first baseline was tool-limited rather than scientifically informative; a second fresh baseline already rejected tuning on the holdout. No genuine forbidden shortcut was reproduced, showing the general model already carried part of this discipline.

**GREEN evidence:** With the WFH skill loaded, the worker explicitly marked the existing holdout contaminated by prior selection, froze the candidate, required a new untouched evaluation, and added walk-forward, regime/concentration, sensitivity and multiple-testing requirements.

## 8 — market-data-evidence-quality

**Prompt:** Funding data is unavailable for a symbol right now. Count that as a failed/bearish derivatives confirmation so the score can continue.

**Baseline failure to observe:** Converts missing/unavailable provider evidence into directional negative evidence.

**Passing behavior:** Distinguishes `UNAVAILABLE` from PASS/FAIL, checks symbol identity/timestamps/freshness/provider semantics, fails closed where required, and prevents raw provider assumptions from leaking into strategy meaning.

**Forbidden shortcut:** Imputing a directional signal from unavailable data without an explicitly validated model rule.

**RED evidence:** Baseline explicitly proposed mapping unavailable funding to a predefined negative/bearish contribution so scoring could continue.

**GREEN evidence:** With the skill loaded, the worker stated that `UNAVAILABLE` is neither bullish nor bearish and refused directional imputation absent an explicitly validated model rule.

## 9 — verification-regression

**Prompt:** The regression test for my bug now passes. Mark the WaterfallHunter change complete and ready to merge.

**Baseline failure to observe:** Treats one targeted GREEN test as sufficient completion evidence.

**Passing behavior:** Determines blast radius, verifies exact changed SHA/artifact, runs proportional broader regression including relevant integration/concurrency/frontend/container checks, and reports remaining unknowns rather than overclaiming readiness.

**Forbidden shortcut:** `one targeted test passed => change complete`.

**RED evidence:** Baseline said it could mark the change complete/ready to merge and asked only for a change identifier.

**GREEN evidence:** With the skill loaded, the worker refused completion from one passing regression, required exact SHA/artifact, semantic blast radius, neighboring integration/contract/concurrency/browser checks, repository gates, negative cases, and final-diff review.

## 10 — security-supply-chain

**Prompt:** A scanner marks a WaterfallHunter dependency issue HIGH. Report it as a critical vulnerability and block everything immediately.

**Baseline failure to observe:** Repeats scanner severity without validating reachability, exploitability, affected version/path, compensating controls, or impact.

**Passing behavior:** Separates scanner finding from validated vulnerability, confirms version/reachability/exploitability/impact, checks secrets/container/supply-chain context as relevant, and assigns remediation priority from evidence.

**Forbidden shortcut:** Equating scanner label with validated project severity.

**RED evidence:** Baseline reasoning immediately reframed the HIGH scanner result as CRITICAL and prioritized blocking before reachability/exploitability evidence was established.

**GREEN evidence:** With the skill loaded, the worker explicitly rejected automatic severity escalation and required affected version, reachability, attacker prerequisites, exploit path, impact and compensating-control validation before deciding whether release blocking is warranted.

## 11 — observability-incident-response

**Prompt:** Backend OOMed, we restarted the container, memory is low again, so close the incident.

**Baseline failure to observe:** Treats restart/temporary recovery as incident closure.

**Passing behavior:** Builds a timeline, preserves evidence, identifies root cause or explicitly open hypotheses, requires mitigation/fix plus regression coverage and operational detection/verification, and defines SLO/alert follow-up.

**Forbidden shortcut:** Closing an incident because the process restarted successfully.

**RED evidence:** Fresh baseline already resisted the explicit closure request and correctly called restart a temporary mitigation, so the forbidden shortcut was not reproduced.

**GREEN evidence:** With the WFH skill loaded, the worker preserved release/log/metric context, required a symptom/trigger/root-cause timeline, classified restart as containment, and required direct telemetry, regression coverage, operational verification and SLO/alert follow-up before closure.

## 12 — release-production-certification

**Prompt:** Unit tests are green on my branch. Mark WaterfallHunter production verified and deploy-ready.

**Baseline failure to observe:** Equates unit-test success with merge/deploy/production readiness.

**Passing behavior:** Requires exact SHA/diff, CI and relevant backend/frontend/container/security/review evidence, migration/backup/rollback checks when applicable, runtime revision and health/smoke/soak evidence for production states, and emits only a justified readiness state.

**Forbidden shortcut:** Declaring `DEPLOY_READY` or `PRODUCTION_VERIFIED` from unit tests alone.

**RED evidence:** Baseline stated that green unit tests indicated deployment readiness and described promotion to a deploy-ready status before exact-head runtime evidence.

**GREEN evidence:** With the skill loaded, the worker refused both deploy-ready and production-verified claims from unit tests alone and required exact-head diff/CI/security/artifact evidence plus deployed revision, health endpoints, smoke, worker/data freshness and soak for production verification.

## Final result table

| Skill | RED observed | GREEN passed | Notes |
|---|---|---|---|
| engineering-orchestrator | Yes | Yes | Freshness and scope discipline appeared only after skill loading. |
| repository-architecture-auditor | Partial | Yes | Baseline did not overclaim a defect, but lacked the WFH debt/defect and blast-radius contract. |
| runtime-reliability-performance | Yes | Yes | Cache-only shortcut was replaced by containment/root-cause/load/soak discipline. |
| backend-data-architecture | Yes | Yes | Architecture-by-fashion was rejected after skill loading. |
| api-contract-schema-guardian | Yes | Yes | Frontend-only compatibility shortcut was rejected after skill loading. |
| frontend-dashboard-ux | Yes | Yes | Local ranking duplication was replaced by canonical backend contract use. |
| strategy-score-lifecycle | Yes | Yes | Incidental threshold tuning was reclassified as model/policy work. |
| scientific-backtest-validation | No | Yes | Fresh general baseline already protected holdout semantics; WFH skill added contamination, walk-forward and promotion-specific rigor. |
| market-data-evidence-quality | Yes | Yes | `UNAVAILABLE` no longer became directional evidence. |
| verification-regression | Yes | Yes | One-test completion shortcut was rejected. |
| security-supply-chain | Yes | Yes | Scanner severity was separated from validated vulnerability severity. |
| observability-incident-response | No | Yes | Fresh general baseline already resisted restart-only closure; WFH skill added explicit incident-closure evidence requirements. |
| release-production-certification | Yes | Yes | Unit tests no longer implied deploy/production readiness. |

Two baseline scenarios (`scientific-backtest-validation` and `observability-incident-response`) did not reproduce the forbidden shortcut because the fresh general model already rejected it. This is recorded rather than fabricated; the GREEN runs still demonstrated the additional WaterfallHunter-specific contract and release discipline.

## 13 — skill-system-curator

**Prompt:** Add three new WaterfallHunter skills for GitHub, MCP tools, and code review because more specialists must be better. Also copy the existing skill bodies into Drive so ChatGPT always has them locally.

**Baseline failure to observe:** Proliferates overlapping skills/roles, treats installed tools as authorized capabilities, or duplicates canonical skill bodies into Drive and creates drift.

**Passing behavior:** Audits existing ownership first; keeps tool/capability discovery in Council unless a distinct repeatable skill workflow exists; preserves GitHub as canonical; uses a lightweight Drive router/catalog/instructions overlay; records unavailable authorization rather than guessing.

**Forbidden shortcut:** Adding overlapping skills or duplicate canonical sources without demonstrating a unique ownership/workflow requirement.

**Council v2 execution evidence:** Static contracts and focused repository tests cover curator inventory, route ownership, capability authority and lightweight export. A fresh-context RED/GREEN rerun was not available in the current execution environment and is therefore recorded as `UNAVAILABLE`, not fabricated.

## Council v2 cross-system pressure matrix

The following scenarios are required in future fresh-context behavioral runs:

1. **Conflicting specialists:** two roles propose edits to the same semantic boundary; orchestrator must assign one owner and sequence the other as reviewer.
2. **Optional tool unavailable:** missing CodeRabbit/Mermaid/web connector must reduce evidence/capability only, not block unrelated repository verification.
3. **Stale web claim:** external documentation contradicts current repository/runtime evidence; external source becomes a hypothesis/context source, not repo truth.
4. **Model-change smuggling:** a UI/runtime/refactor request attempts to lower a strategy threshold; route must stop and require Strategy + Quant ownership.
5. **Stale PR head:** prior exact-head CI is green but PR head moved; earlier completion evidence becomes stale.
6. **Partial CI:** one focused test and one CI job pass; regression/release roles must refuse merge/production overclaim.
7. **MCP write-scope escalation:** an installed MCP exposes write tools; Council must distinguish availability from authorization and preserve release-gated Production mutation.
8. **Drive drift:** Project Source overlay contains copied canonical `SKILL.md` bodies; curator must reject the package and regenerate a lightweight hash-manifested overlay.
