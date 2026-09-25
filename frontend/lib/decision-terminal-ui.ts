type Rec = Record<string, unknown>;

type DecisionCounts = {
  LATE?: unknown;
  INVALIDATED?: unknown;
  EXPIRED?: unknown;
  NO_TRADE?: unknown;
  UNAVAILABLE?: unknown;
};

type FreshnessState = "fresh" | "stale" | "unknown";
type FreshnessSummaryState = "fresh" | "mixed" | "stale" | "unknown";

function record(value: unknown): Rec {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Rec
    : {};
}

function finite(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function count(value: unknown): number {
  const parsed = finite(value);
  return parsed !== undefined && parsed >= 0 ? Math.trunc(parsed) : 0;
}

export function blockedOrOtherCount(counts: DecisionCounts): number {
  return count(counts.LATE)
    + count(counts.INVALIDATED)
    + count(counts.EXPIRED)
    + count(counts.NO_TRADE)
    + count(counts.UNAVAILABLE);
}

export function blockedOrOtherBreakdown(counts: DecisionCounts): string {
  const rows: Array<[string, number]> = [
    ["late", count(counts.LATE)],
    ["invalidated", count(counts.INVALIDATED)],
    ["expired", count(counts.EXPIRED)],
    ["no trade", count(counts.NO_TRADE)],
    ["unavailable", count(counts.UNAVAILABLE)],
  ];
  return rows.filter(([, value]) => value > 0).map(([label, value]) => `${value} ${label}`).join(" · ");
}

export function tradePlanAvailable(value: unknown): boolean {
  const plan = record(value);
  return ["entry_price", "stop_loss", "take_profit_1", "take_profit_2"]
    .every((key) => finite(plan[key]) !== undefined);
}

export function decisionPlanPresentation(
  metricsValue: unknown,
  decisionValue: unknown,
): { kind: "canonical" | "reference" | "unavailable"; plan: Rec } {
  const metrics = record(metricsValue);
  const decision = record(decisionValue);
  const canonical = record(decision.trade_plan);
  if (tradePlanAvailable(canonical)) return { kind: "canonical", plan: canonical };

  const shadow = record(metrics.technical_trade_plan_shadow);
  const reference = record(shadow.setup);
  if (
    shadow.available === true
    && shadow.feasible === true
    && shadow.observational_only === true
    && shadow.hard_gating_allowed === false
    && tradePlanAvailable(reference)
  ) {
    return { kind: "reference", plan: reference };
  }
  return { kind: "unavailable", plan: {} };
}

export function rawLeveragePresentation(metricsValue: unknown): string {
  const metrics = record(metricsValue);
  const leverage = finite(metrics.applied_leverage);
  if (leverage !== undefined) return `${leverage}×`;
  const advisory = record(metrics.leverage_advisory);
  const status = typeof advisory.status === "string" ? advisory.status.toUpperCase() : "";
  if (status === "NOT_RECOMMENDED") return "NOT RECOMMENDED";
  if (status === "UNAVAILABLE") return "UNAVAILABLE";
  return "—";
}

export function advisoryPresentation(value: unknown): {
  status: string;
  question: string;
  answer: string;
  confidence?: number;
  confidenceLabel?: string;
  reasoning: string;
  provider: string;
  model: string;
} {
  const advisory = record(value);
  const status = typeof advisory.ai_advice === "string" && advisory.ai_advice ? advisory.ai_advice : "UNAVAILABLE";
  const question = typeof advisory.ai_question === "string" && advisory.ai_question ? advisory.ai_question : "Does the supplied evidence support a valid short setup?";
  const answer = advisory.ai_answer_yes === true ? "YES" : advisory.ai_answer_yes === false ? "NO" : "—";
  const reasoning = typeof advisory.ai_reasoning === "string" && advisory.ai_reasoning ? advisory.ai_reasoning : "No advisory available";
  const confidence = finite(advisory.ai_confidence);
  const confidenceLabel = typeof advisory.ai_confidence_label === "string" ? advisory.ai_confidence_label : undefined;
  const provider = typeof advisory.ai_provider === "string" ? advisory.ai_provider : "none";
  const model = typeof advisory.ai_model === "string" ? advisory.ai_model : "none";
  if (["PENDING", "UNAVAILABLE"].includes(status.toUpperCase()) || confidence === undefined) return { status, question, answer, reasoning, provider, model };
  return { status, question, answer, confidence, confidenceLabel, reasoning, provider, model };
}

function advancingAge(snapshotAge: unknown, observedAt: unknown, nowSeconds?: number): number | undefined {
  const observed = finite(observedAt);
  if (nowSeconds !== undefined && observed !== undefined && nowSeconds >= observed) {
    return nowSeconds - observed;
  }
  return finite(snapshotAge);
}

export function candidateFreshness(value: unknown, nowSeconds?: number): {
  ageSeconds?: number;
  thresholdSeconds?: number;
  state: FreshnessState;
} {
  const candidate = record(value);
  const metrics = record(candidate.metrics);
  const decision = record(metrics.entry_decision);
  const policy = record(decision.policy);
  const analysisAge = advancingAge(
    candidate.analysis_age_seconds, candidate.analysis_observed_at, nowSeconds,
  );
  const analysisThreshold = finite(policy.max_analysis_age_seconds);
  const referenceThreshold = finite(policy.max_reference_age_seconds);
  const referenceAge = advancingAge(
    candidate.reference_age_seconds, candidate.reference_observed_at, nowSeconds,
  );
  if (analysisAge === undefined || analysisThreshold === undefined || analysisThreshold <= 0) {
    return { ageSeconds: analysisAge, thresholdSeconds: analysisThreshold, state: "unknown" };
  }
  if (referenceThreshold !== undefined) {
    if (referenceThreshold <= 0) {
      return { ageSeconds: referenceAge ?? analysisAge, thresholdSeconds: referenceThreshold, state: "unknown" };
    }
    if (referenceAge === undefined) {
      return { ageSeconds: analysisAge, thresholdSeconds: analysisThreshold, state: "unknown" };
    }
    if (referenceAge > referenceThreshold) {
      return { ageSeconds: referenceAge, thresholdSeconds: referenceThreshold, state: "stale" };
    }
  }
  return {
    ageSeconds: analysisAge,
    thresholdSeconds: analysisThreshold,
    state: analysisAge <= analysisThreshold ? "fresh" : "stale",
  };
}

export function pipelineHealthDegraded(value: unknown): boolean {
  const diagnostics = record(value);
  const systemic = Array.isArray(diagnostics.systemic_unavailable_reasons)
    ? diagnostics.systemic_unavailable_reasons
    : [];
  return diagnostics.pipeline_degraded === true && systemic.length > 0;
}

export function summarizeCandidateFreshness(
  candidates: Record<string, unknown>,
  nowSeconds?: number,
): {
  total: number;
  fresh: number;
  stale: number;
  unknown: number;
  state: FreshnessSummaryState;
} {
  let fresh = 0;
  let stale = 0;
  let unknown = 0;
  for (const candidate of Object.values(candidates)) {
    const state = candidateFreshness(candidate, nowSeconds).state;
    if (state === "fresh") fresh += 1;
    else if (state === "stale") stale += 1;
    else unknown += 1;
  }
  const total = fresh + stale + unknown;
  let state: FreshnessSummaryState = "unknown";
  if (total > 0 && stale === 0 && unknown === 0) state = "fresh";
  else if (total > 0 && stale === total) state = "stale";
  else if (total > 0 && (fresh > 0 || stale > 0)) state = "mixed";
  return { total, fresh, stale, unknown, state };
}

export function canonicalLeverageAdvisory(metricsValue: unknown, decisionValue: unknown): Rec {
  const metrics = record(metricsValue);
  const decision = record(decisionValue);
  const live = record(metrics.leverage_advisory);
  if (Object.keys(live).length > 0) return live;
  return record(decision.leverage_advisory);
}
