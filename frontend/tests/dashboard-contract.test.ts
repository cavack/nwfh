import assert from "node:assert/strict";
import { dashboardSnapshot, decisionTerminal } from "../lib/dashboard-contract";
import {
  advisoryPresentation,
  blockedOrOtherBreakdown,
  blockedOrOtherCount,
  candidateFreshness,
  decisionPlanPresentation,
  canonicalLeverageAdvisory,
  pipelineHealthDegraded,
  rawLeveragePresentation,
  summarizeCandidateFreshness,
  tradePlanAvailable,
} from "../lib/decision-terminal-ui";

const counts = { ENTRY_READY: 0, FORMING: 0, ACTIVE: 0, LATE: 0, INVALIDATED: 0, EXPIRED: 0, NO_TRADE: 1, UNAVAILABLE: 0 };
const snapshot = {
  contract_version: "dashboard_snapshot_v2", schema_version: "2.0", snapshot_version: 1,
  generated_at: 1, state: "READY", total: 1, candidates: { BAD: null },
  decision_terminal: {
    contract_version: "decision_terminal_v1", counts, entry_ready: [], forming: [], active: [], late: [],
    zero_entry_ready_diagnostics: { entry_ready_zero: true, evaluated_candidates: 1, top_reasons: [], pipeline_degraded: false, systemic_unavailable_reasons: [] },
    recent_changes: [],
  },
  final_ranking: {}, signal_funnel: {},
};
assert.equal(dashboardSnapshot(snapshot), undefined, "null candidate values must be rejected");

const legacyTerminalWithoutPipelineHealth = {
  contract_version: "decision_terminal_v1", counts, entry_ready: [], forming: [], active: [], late: [],
  zero_entry_ready_diagnostics: { entry_ready_zero: true, evaluated_candidates: 1, top_reasons: [] },
  recent_changes: [],
};
assert.notEqual(
  decisionTerminal(legacyTerminalWithoutPipelineHealth),
  undefined,
  "additive pipeline health fields must remain backward compatible with decision_terminal_v1",
);
const partialPipelineHealth = {
  ...legacyTerminalWithoutPipelineHealth,
  zero_entry_ready_diagnostics: {
    ...legacyTerminalWithoutPipelineHealth.zero_entry_ready_diagnostics,
    pipeline_degraded: true,
  },
};
assert.equal(
  decisionTerminal(partialPipelineHealth),
  undefined,
  "partial pipeline health diagnostics must be rejected",
);


const productionLikeCounts = {
  ENTRY_READY: 0, FORMING: 0, ACTIVE: 0, LATE: 129,
  INVALIDATED: 0, EXPIRED: 0, NO_TRADE: 15, UNAVAILABLE: 0,
};
assert.equal(blockedOrOtherCount(productionLikeCounts), 144);
assert.equal(blockedOrOtherBreakdown(productionLikeCounts), "129 late · 15 no trade");

const freshnessCandidates = {
  FRESH: {
    analysis_age_seconds: 45,
    metrics: { entry_decision: { policy: { max_analysis_age_seconds: 180 } } },
  },
  STALE: {
    analysis_age_seconds: 360,
    metrics: { entry_decision: { policy: { max_analysis_age_seconds: 180 } } },
  },
};
assert.deepEqual(candidateFreshness(freshnessCandidates.FRESH), {
  ageSeconds: 45, thresholdSeconds: 180, state: "fresh",
});
assert.deepEqual(candidateFreshness(freshnessCandidates.STALE), {
  ageSeconds: 360, thresholdSeconds: 180, state: "stale",
});
assert.deepEqual(summarizeCandidateFreshness(freshnessCandidates), {
  total: 2, fresh: 1, stale: 1, unknown: 0, state: "mixed",
});


const referenceStale = {
  analysis_age_seconds: 30,
  reference_age_seconds: 70,
  metrics: { entry_decision: { policy: { max_analysis_age_seconds: 180, max_reference_age_seconds: 60 } } },
};
assert.equal(
  candidateFreshness(referenceStale).state,
  "stale",
  "reference freshness must participate in the canonical freshness decision",
);


const invalidReferenceLimit = {
  analysis_age_seconds: 30,
  reference_age_seconds: 10,
  metrics: { entry_decision: { policy: { max_analysis_age_seconds: 180, max_reference_age_seconds: 0 } } },
};
assert.equal(
  candidateFreshness(invalidReferenceLimit).state,
  "unknown",
  "an explicit nonpositive reference freshness limit must fail closed as unknown",
);

const timeProgression = {
  analysis_observed_at: 850,
  reference_observed_at: 950,
  metrics: { entry_decision: { policy: { max_analysis_age_seconds: 180, max_reference_age_seconds: 60 } } },
};
assert.equal(candidateFreshness(timeProgression, 1000).state, "fresh");
assert.equal(
  candidateFreshness(timeProgression, 1020).state,
  "stale",
  "freshness must advance with wall time even when no new snapshot arrives",
);

assert.equal(
  pipelineHealthDegraded({
    entry_ready_zero: false,
    evaluated_candidates: 1,
    top_reasons: [],
    pipeline_degraded: true,
    systemic_unavailable_reasons: [{ reason: "DERIVATIVES_UNAVAILABLE", count: 1, share_pct: 100 }],
  }),
  true,
  "systemic pipeline degradation must remain visible even with ENTRY_READY candidates",
);

assert.equal(tradePlanAvailable({
  entry_price: 1, stop_loss: 1.1, take_profit_1: 0.9, take_profit_2: 0.8,
}), true);
assert.equal(tradePlanAvailable({ entry_price: 1 }), false);

assert.deepEqual(advisoryPresentation({
  ai_advice: "PENDING", ai_confidence: 0, ai_reasoning: "queued",
}), {
  status: "PENDING",
  question: "Does the supplied evidence support a valid short setup?",
  answer: "—",
  reasoning: "queued",
  provider: "none",
  model: "none",
});
assert.deepEqual(advisoryPresentation({
  ai_advice: "DOES_NOT_SUPPORT_SHORT", ai_answer_yes: false, ai_confidence: 82, ai_confidence_label: "Solid support", ai_reasoning: "late cascade", ai_provider: "typesafe", ai_model: "jev-test",
}), { status: "DOES_NOT_SUPPORT_SHORT", question: "Does the supplied evidence support a valid short setup?", answer: "NO", confidence: 82, confidenceLabel: "Solid support", reasoning: "late cascade", provider: "typesafe", model: "jev-test" });

assert.deepEqual(
  canonicalLeverageAdvisory({}, { leverage_advisory: { status: "NOT_RECOMMENDED", leverage: null, policy_version: "adaptive_signal_leverage_v1" } }),
  { status: "NOT_RECOMMENDED", leverage: null, policy_version: "adaptive_signal_leverage_v1" },
  "persisted decision leverage advisory must remain canonical when the live metric copy is absent",
);

assert.equal(rawLeveragePresentation({ applied_leverage: 8, leverage_advisory: { status: "AVAILABLE" } }), "8×");
assert.equal(rawLeveragePresentation({ applied_leverage: null, leverage_advisory: { status: "UNAVAILABLE" } }), "UNAVAILABLE");
assert.equal(rawLeveragePresentation({ applied_leverage: null, leverage_advisory: { status: "NOT_RECOMMENDED" } }), "NOT RECOMMENDED");
assert.equal(rawLeveragePresentation({}), "—");

assert.deepEqual(
  decisionPlanPresentation(
    { technical_trade_plan_shadow: {
      available: true, feasible: true, observational_only: true, hard_gating_allowed: false,
      setup: { entry_price: 1, stop_loss: 1.1, take_profit_1: 0.9, take_profit_2: 0.8 },
    } },
    { trade_plan: null },
  ),
  { kind: "reference", plan: { entry_price: 1, stop_loss: 1.1, take_profit_1: 0.9, take_profit_2: 0.8 } },
);
assert.equal(
  decisionPlanPresentation(
    { technical_trade_plan_shadow: { available: true, feasible: false, setup: {} } },
    { trade_plan: null },
  ).kind,
  "unavailable",
);
assert.equal(
  decisionPlanPresentation(
    { technical_trade_plan_shadow: { available: true, feasible: true, observational_only: true, hard_gating_allowed: false, setup: { entry_price: 2, stop_loss: 2.1, take_profit_1: 1.9, take_profit_2: 1.8 } } },
    { trade_plan: { entry_price: 1, stop_loss: 1.1, take_profit_1: 0.9, take_profit_2: 0.8 } },
  ).kind,
  "canonical",
);
