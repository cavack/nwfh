import { expect, test, type Page, type Route } from "@playwright/test";

const API_PREFIX = "/dashboard/api/";
const HASH = "a".repeat(64);

type Decision = "ENTRY_READY" | "FORMING" | "ACTIVE" | "LATE" | "NO_TRADE";
type LeverageStatus = "AVAILABLE" | "UNAVAILABLE" | "NOT_RECOMMENDED";

function plan(leverage: number | null = null) {
  return {
    entry_price: 0.1005,
    stop_loss: 0.1045,
    take_profit_1: 0.0965,
    take_profit_2: 0.0925,
    take_profit_3: null,
    reward_to_risk: 2,
    leverage,
  };
}

// cascade_intelligence emits PASS | FAIL | PARTIAL | UNAVAILABLE. The fixture
// previously used "COMPLETE", a status the backend never produces, so the
// assertion below was pinning a string that could not appear in production.
function evidence(cascadeStatus: "PASS" | "PARTIAL", antiChase: number) {
  return {
    anti_chase_extension_atr: antiChase,
    cross_exchange_confirmed: true,
    derivatives: {
      oi_change_1h_pct: 14.2,
      funding_rate_pct: 0.08,
      top_trader_long_short_ratio: 2.4,
    },
    order_flow: {
      taker_buy_sell_ratio: 0.72,
      sell_share_pct: 71.5,
    },
    execution: { spread_pct: 0.04, slippage_pct: 0.03 },
    cascade: { status: cascadeStatus, readiness_points: cascadeStatus === "PASS" ? 9.2 : 7.1, maximum_available: cascadeStatus === "PASS" ? 10 : 8 },
  };
}

function candidate({
  decision,
  readiness,
  score,
  leverageStatus,
  leverage,
  coverage,
  stale = false,
  status,
}: {
  decision: Decision;
  readiness: number;
  score: number | null;
  leverageStatus: LeverageStatus;
  leverage: number | null;
  coverage: number;
  stale?: boolean;
  status: "WATCH" | "PRE-TRIGGER" | "ARMED" | "TRIGGERED";
}) {
  const now = Date.now() / 1000;
  const analysisAge = stale ? 400 : 20;
  const referenceAge = stale ? 90 : 10;
  const hasPlan = decision !== "NO_TRADE";
  return {
    status,
    signal_class: "STRICT",
    data_status: "live",
    analysis_status: "ready",
    last_price: 0.1001,
    score,
    analysis_observed_at: now - analysisAge,
    analysis_age_seconds: analysisAge,
    reference_observed_at: now - referenceAge,
    reference_age_seconds: referenceAge,
    execution_suitability: { status: "SUITABLE", available: true },
    metrics: {
      score_version: score === null ? "score_v2" : "score_v2",
      score,
      total_score: score,
      trade_eligible: score !== null,
      score_components: score === null ? {} : { structure: 20, derivatives_confirmation: 15 },
      quality_gates: { channel_stage_chain: score !== null, complete_fresh_derivatives_packet: score !== null },
      leverage_advisory: {
        policy_version: "adaptive_signal_leverage_v1",
        status: leverageStatus,
        leverage,
        reason: leverageStatus === "AVAILABLE" ? null : `controlled ${leverageStatus.toLowerCase()}`,
      },
      applied_leverage: leverageStatus === "AVAILABLE" ? leverage : null,
      entry_decision: {
        contract_version: "entry_decision_v1",
        decision,
        lifecycle_state: status,
        lifecycle_id: 7,
        entry_readiness: readiness,
        evidence_coverage_pct: coverage,
        evaluated_at: Math.floor(now - analysisAge),
        block_reasons: decision === "LATE" ? ["ANTI_CHASE_HARD_BLOCK"] : decision === "NO_TRADE" ? ["EXECUTION_UNAVAILABLE"] : [],
        reason_codes: decision === "LATE" ? ["CASCADE_PARTIAL"] : ["ENTRY_GATES_PASS"],
        policy: {
          max_analysis_age_seconds: 600,
          max_reference_age_seconds: 60,
          anti_chase_hard_block_atr: 2.5,
          entry_ready_minimum: 70,
          forming_minimum: 55,
        },
        trade_plan: hasPlan ? plan(leverageStatus === "AVAILABLE" ? leverage : null) : null,
        leverage_advisory: {
          status: leverageStatus,
          leverage,
          policy_version: "adaptive_signal_leverage_v1",
          reason: leverageStatus === "AVAILABLE" ? null : `controlled ${leverageStatus.toLowerCase()}`,
        },
        evidence_summary: evidence(coverage === 100 ? "PASS" : "PARTIAL", decision === "LATE" ? 2.6 : 0.8),
      },
    },
  };
}

function snapshot(version: number, swapped = false, omitDelta = false) {
  const candidates: Record<string, ReturnType<typeof candidate>> = swapped ? {
    "BETA/USDT:USDT": candidate({ decision: "ENTRY_READY", readiness: 96, score: 99, leverageStatus: "AVAILABLE", leverage: 10, coverage: 100, status: "ARMED" }),
    "ALPHA/USDT:USDT": candidate({ decision: "FORMING", readiness: 70, score: 86, leverageStatus: "AVAILABLE", leverage: 6, coverage: 100, status: "PRE-TRIGGER" }),
    "GAMMA/USDT:USDT": candidate({ decision: "LATE", readiness: 84, score: 97, leverageStatus: "NOT_RECOMMENDED", leverage: null, coverage: 98, stale: true, status: "TRIGGERED" }),
    "DELTA/USDT:USDT": candidate({ decision: "NO_TRADE", readiness: 10, score: null, leverageStatus: "UNAVAILABLE", leverage: null, coverage: 60, status: "WATCH" }),
  } : {
    "ALPHA/USDT:USDT": candidate({ decision: "ENTRY_READY", readiness: 94, score: 98, leverageStatus: "AVAILABLE", leverage: 8, coverage: 100, status: "ARMED" }),
    "BETA/USDT:USDT": candidate({ decision: "FORMING", readiness: 75, score: 88, leverageStatus: "UNAVAILABLE", leverage: null, coverage: 98, status: "PRE-TRIGGER" }),
    "GAMMA/USDT:USDT": candidate({ decision: "LATE", readiness: 84, score: 97, leverageStatus: "NOT_RECOMMENDED", leverage: null, coverage: 98, stale: true, status: "TRIGGERED" }),
    "DELTA/USDT:USDT": candidate({ decision: "NO_TRADE", readiness: 10, score: null, leverageStatus: "UNAVAILABLE", leverage: null, coverage: 60, status: "WATCH" }),
  };
  if (omitDelta) delete candidates["DELTA/USDT:USDT"];
  const entryReady = swapped ? ["BETA/USDT:USDT"] : ["ALPHA/USDT:USDT"];
  const forming = swapped ? ["ALPHA/USDT:USDT"] : ["BETA/USDT:USDT"];
  const noTradeCount = omitDelta ? 0 : 1;
  return {
    contract_version: "dashboard_snapshot_v2",
    schema_version: "2.0",
    snapshot_version: version,
    generated_at: Date.now() / 1000,
    state: "READY",
    total: Object.keys(candidates).length,
    candidates,
    decision_terminal: {
      contract_version: "decision_terminal_v1",
      counts: { ENTRY_READY: 1, FORMING: 1, ACTIVE: 0, LATE: 1, INVALIDATED: 0, EXPIRED: 0, NO_TRADE: noTradeCount, UNAVAILABLE: 0 },
      entry_ready: entryReady,
      forming,
      active: [],
      late: ["GAMMA/USDT:USDT"],
      zero_entry_ready_diagnostics: { entry_ready_zero: false, evaluated_candidates: Object.keys(candidates).length, top_reasons: [], pipeline_degraded: false, systemic_unavailable_reasons: [] },
      recent_changes: [],
    },
    final_ranking: {},
    signal_funnel: {},
  };
}

function streamEvent(payload: ReturnType<typeof snapshot>) {
  return {
    contract_version: "dashboard_stream_event_v2",
    event_id: String(payload.snapshot_version),
    event_type: "snapshot",
    snapshot_version: payload.snapshot_version,
    schema_version: "2.0",
    generated_at: payload.generated_at,
    last_event_id: null,
    payload_hash: HASH,
    payload,
    replayed: false,
    full_snapshot: true,
  };
}

function errorCollector(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror:${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console:${message.text()}`);
  });
  return errors;
}

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function routeDashboard(page: Page, pollSnapshot = snapshot(1)) {
  await page.route(`**${API_PREFIX}**`, async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/api/candidates/raw")) return fulfillJson(route, pollSnapshot);
    if (pathname.endsWith("/api/candidates")) return fulfillJson(route, pollSnapshot);
    if (pathname.endsWith("/api/stream")) {
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: "retry: 60000\n\n" });
    }
    return fulfillJson(route, { state: "UNAVAILABLE" });
  });
}

test("desktop renders canonical decision, plan, tri-state leverage, evidence and freshness", async ({ page }) => {
  const errors = errorCollector(page);
  await routeDashboard(page);
  await page.goto("/dashboard");

  await expect(page.getByRole("heading", { name: "WaterfallHunter" })).toBeVisible();
  const entrySection = page.locator("#decision-terminal > section").filter({ has: page.getByRole("heading", { name: "ENTRY READY" }) });
  await expect(entrySection.getByText("ALPHA/USDT:USDT")).toBeVisible();
  await expect(entrySection.getByText("Leverage 8×")).toBeVisible();
  await expect(entrySection.getByText("$0.1045")).toBeVisible();
  await expect(entrySection.getByText("$0.0965")).toBeVisible();
  await expect(entrySection.getByText("$0.0925")).toBeVisible();
  await expect(entrySection.getByText("Evidence coverage 100%")).toBeVisible();
  await expect(entrySection).toContainText("Cascade");
  await expect(entrySection).toContainText("PASS · 9.2/10");

  const formingSection = page.locator("#decision-terminal > section").filter({ has: page.getByRole("heading", { name: /Closest setups/ }) });
  await expect(formingSection.getByText("BETA/USDT:USDT")).toBeVisible();
  await expect(formingSection.getByText("Leverage UNAVAILABLE")).toBeVisible();
  await expect(formingSection.getByText("Evidence coverage 98%")).toBeVisible();

  const lateSection = page.locator("#decision-terminal > section").filter({ has: page.getByRole("heading", { name: /Late · do not chase/ }) });
  await expect(lateSection.getByText("GAMMA/USDT:USDT")).toBeVisible();
  await expect(lateSection.getByText("Leverage NOT RECOMMENDED")).toBeVisible();
  await expect(lateSection.getByText("LATE", { exact: true })).toBeVisible();

  // The raw-candidate surface was removed with the Score-V2 components
  // (ScoreCard rendered score_v2_watch_v1, trade_eligible and the
  // TRIGGERED/ARMED/REJECTED lifecycle pills). Nothing in the app calls
  // /api/candidates/raw any more, so the assertions that opened it and read
  // "98/100" / "ARMED" out of article.panel were testing a UI that no longer
  // exists. The canonical surface above covers the same packets.
  await page.getByText("Research & Diagnostics").click();
  expect(errors).toEqual([]);
});


test("malformed display fields fail closed without object stringification", async ({ page }) => {
  const malformed = snapshot(1) as any;
  malformed.candidates["ALPHA/USDT:USDT"].metrics.entry_decision.evidence_summary.cascade.status = { unexpected: true };
  malformed.decision_terminal.recent_changes = [{
    event_id: { unexpected: true },
    symbol: { unexpected: true },
    event_at: Date.now() / 1000,
    previous_decision: { unexpected: true },
    decision: { unexpected: true },
    transition_reason: { unexpected: true },
  }];
  await routeDashboard(page, malformed);
  await page.goto("/dashboard");
  await expect(page.locator("body")).not.toContainText("[object Object]");
});

test("a backend restart does not freeze the dashboard on stale data", async ({ page }) => {
  // snapshot_version is monotonic only within one backend process. After a
  // restart it legitimately falls below what the tab has already seen; the
  // client must still accept the newer snapshot on its generated_at, or it
  // silently ignores every update and shows "Live" over frozen data until a
  // manual reload.
  const high = snapshot(900_000) as any;
  high.generated_at = 1_000_000;
  const afterRestart = snapshot(3) as any;
  afterRestart.generated_at = 1_000_100;
  afterRestart.candidates["ALPHA/USDT:USDT"].last_price = 0.4242;

  let polls = 0;
  await page.route(`**${API_PREFIX}**`, async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/api/candidates")) {
      polls += 1;
      return fulfillJson(route, polls === 1 ? high : afterRestart);
    }
    if (pathname.endsWith("/api/stream")) {
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: "retry: 60000\n\n" });
    }
    return fulfillJson(route, { state: "UNAVAILABLE" }, 503);
  });

  await page.goto("/dashboard");
  await expect(page.getByText("$0.4242").first()).toBeVisible({ timeout: 15_000 });
});

test("SSE reconnect accepts a newer canonical snapshot and reorders the decision surface", async ({ page }) => {
  const errors = errorCollector(page);
  let streamRequests = 0;
  await page.route(`**${API_PREFIX}**`, async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/api/candidates")) return fulfillJson(route, snapshot(1));
    if (!pathname.endsWith("/api/stream")) return fulfillJson(route, {}, 503);
    streamRequests += 1;
    if (streamRequests === 1) {
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: "retry: 50\n\n" });
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
    const updated = snapshot(2, true, true);
    const body = `retry: 60000\nid: 2\nevent: snapshot\ndata: ${JSON.stringify(streamEvent(updated))}\n\n`;
    return route.fulfill({ status: 200, contentType: "text/event-stream", body });
  });

  await page.goto("/dashboard");
  await expect(page.getByText("ALPHA/USDT:USDT").first()).toBeVisible();
  const entrySection = page.locator("#decision-terminal > section").filter({ has: page.getByRole("heading", { name: "ENTRY READY" }) });
  await expect(entrySection.getByText("BETA/USDT:USDT")).toBeVisible({ timeout: 8_000 });
  await expect(entrySection.getByText("Leverage 10×")).toBeVisible();
  const firstTableRow = page.locator("#all-candidates tbody tr").first();
  await expect(firstTableRow).toContainText("BETA/USDT:USDT");
  await expect(firstTableRow).toContainText("ENTRY READY");
  await expect(page.getByText("DELTA/USDT:USDT")).toHaveCount(0);
  await expect(page.locator("#all-candidates tbody")).not.toContainText("DELTA/USDT:USDT");
  expect(streamRequests).toBeGreaterThanOrEqual(2);
  const unexpectedErrors = errors.filter((message) =>
    !message.includes("status of 503 (Service Unavailable)"),
  );
  expect(unexpectedErrors).toEqual([]);
});

test("API and stream failure remain fail-closed without hydration or unexpected runtime errors", async ({ page }) => {
  const errors = errorCollector(page);
  await page.route(`**${API_PREFIX}**`, async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/api/stream")) return route.abort("connectionrefused");
    if (pathname.endsWith("/api/candidates")) return fulfillJson(route, { detail: "controlled outage" }, 503);
    return fulfillJson(route, {}, 503);
  });
  await page.goto("/dashboard");
  await expect(page.getByText("Loading…")).toBeVisible();
  await expect(page.getByText("Recon")).toBeVisible();
  await expect(page.getByText("ENTRY READY", { exact: true })).toHaveCount(0);
  const unexpectedErrors = errors.filter((message) =>
    !message.includes("Failed to load resource: net::ERR_CONNECTION_REFUSED") &&
    !message.includes("status of 503 (Service Unavailable)"),
  );
  expect(unexpectedErrors).toEqual([]);
});

test("mobile keeps the canonical terminal usable without page-level horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const errors = errorCollector(page);
  await routeDashboard(page);
  await page.goto("/dashboard");
  await expect(page.getByText("ALPHA/USDT:USDT").first()).toBeVisible();
  await expect(page.getByText("SIGNAL ONLY", { exact: true }).first()).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  expect(errors).toEqual([]);
});
