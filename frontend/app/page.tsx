"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Wifi,
  WifiOff,
  Clock3,
  Zap,
  Target,
  ChevronDown,
  BarChart3,
  FlaskConical,
  DollarSign,
  SlidersHorizontal,
} from "lucide-react";
import { Candidate } from "@/lib/candidate";
import { DecisionTerminal, CandidateTable } from "@/components/decision-terminal";
import { OutcomeEvidence } from "@/components/outcome-evidence";
import { RecentSignals } from "@/components/recent-signals";
import { HistoricalOutcomes } from "@/components/historical-outcomes";
import { ProductionEvidence } from "@/components/production-evidence";
import { FeatureReplay } from "@/components/feature-replay";
import { LifecycleShadow } from "@/components/lifecycle-shadow";
import { BacktestLab } from "@/components/backtest-lab";
import { SignalFunnel, SignalFunnelData } from "@/components/signal-funnel";
import { FinalRanking } from "@/components/final-ranking";
import { SettingsPanel } from "@/components/settings-panel";
import type { DashboardSnapshot } from "@/generated/dashboard-contract";
import { dashboardSnapshot, dashboardStreamEvent } from "@/lib/dashboard-contract";
import { summarizeCandidateFreshness } from "@/lib/decision-terminal-ui";

type ConnectionMode = "stream" | "polling" | "reconnecting";

function boundedJitter(maximum: number): number {
  const sample = new Uint32Array(1);
  globalThis.crypto.getRandomValues(sample);
  return Math.floor((sample[0] / 0xffffffff) * maximum);
}

/* ─── Packet accessors ───
   The backend decides. Nothing here re-derives a decision, a label or a
   threshold: a candidate is actionable because entry_decision.decision says
   so, never because its readiness crossed a number this file happens to know. */
function getMetrics(c: Candidate): Record<string, unknown> | undefined {
  const m = c.metrics;
  return m !== null && typeof m === "object" && !Array.isArray(m)
    ? (m as Record<string, unknown>)
    : undefined;
}
function getED(c: Candidate): Record<string, unknown> | undefined {
  const ed = getMetrics(c)?.entry_decision;
  return ed !== null && typeof ed === "object" && !Array.isArray(ed)
    ? (ed as Record<string, unknown>)
    : undefined;
}
function getReadiness(c: Candidate): number {
  const r = getED(c)?.entry_readiness;
  return typeof r === "number" && Number.isFinite(r) ? r : 0;
}
function getDecision(c: Candidate): string {
  return (getED(c)?.decision as string) ?? "—";
}
function getTradePlan(c: Candidate): Record<string, unknown> | null {
  const tp = getED(c)?.trade_plan;
  return tp !== null && typeof tp === "object" && !Array.isArray(tp)
    ? (tp as Record<string, unknown>)
    : null;
}

/** Decisions the engine considers actionable. Everything else is context. */
const ACTIONABLE = new Set(["ENTRY_READY", "ACTIVE"]);

function decisionTone(decision: string): string {
  if (ACTIONABLE.has(decision)) return "text-emerald-400";
  if (decision === "FORMING") return "text-sky-400";
  if (decision === "LATE") return "text-amber-400";
  if (decision === "INVALIDATED" || decision === "EXPIRED") return "text-rose-400";
  return "text-slate-400";
}

/* Smart number formatter — enough precision for any price */
function fmt(v: number | undefined): string {
  if (v === undefined || v === null || !Number.isFinite(v)) return "—";
  if (v === 0) return "0";
  const abs = Math.abs(v);
  if (abs >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (abs >= 1) return v.toFixed(4);
  if (abs >= 0.01) return v.toFixed(5);
  if (abs >= 0.0001) return v.toFixed(7);
  return v.toFixed(10);
}

/* ─── Signal card ─── */
function SignalCard({ symbol, candidate }: Readonly<{ symbol: string; candidate: Candidate }>) {
  const ed = getED(candidate);
  if (!ed) return null;

  const decision = getDecision(candidate);
  const readiness = getReadiness(candidate);
  const tp = getTradePlan(candidate);
  const shortName = symbol.replace("/USDT:USDT", "").replace("/USDT", "");
  const ep = tp?.entry_price as number | undefined;
  const sl = tp?.stop_loss as number | undefined;
  const tp1 = tp?.take_profit_1 as number | undefined;
  const tp2 = tp?.take_profit_2 as number | undefined;
  const r2r = tp?.reward_to_risk as number | undefined;
  const leverage = tp?.leverage as number | undefined;

  const es = ed.evidence_summary as Record<string, unknown> | undefined;
  const cascade = es?.cascade as Record<string, unknown> | undefined;
  const cross = es?.cross_exchange_confirmed as boolean | undefined;

  const ai = getMetrics(candidate)?.ai_advisory as Record<string, unknown> | undefined;
  const fundamental = getMetrics(candidate)?.fundamental_observational as Record<string, unknown> | undefined;
  const aiAdvice = (ai?.ai_advice as string) ?? "";
  const fundamentalScore = typeof fundamental?.fundamental_score === "number"
    ? fundamental.fundamental_score
    : undefined;
  // Jev answers one typed binary evidence question; it never changes the engine decision.
  const hasAI = aiAdvice === "SUPPORTS_SHORT" || aiAdvice === "DOES_NOT_SUPPORT_SHORT";

  const actionable = ACTIONABLE.has(decision);
  const tone = actionable
    ? "border-emerald-500/25 bg-emerald-500/[0.07]"
    : "border-sky-500/20 bg-sky-500/[0.05]";

  const riskPct =
    ep && sl && ep > 0 ? Math.abs(((ep - sl) / ep) * 100).toFixed(1) : undefined;

  return (
    <div className={`rounded-xl border ${tone} p-4 transition-shadow hover:shadow-lg hover:shadow-black/20`}>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-800/60 text-xs font-bold text-white">
            {shortName.slice(0, 3)}
          </div>
          <span className="text-base font-bold text-white">{shortName}</span>
        </div>
        <div className="rounded-lg bg-slate-900/50 px-2.5 py-1 text-center">
          <span className={`font-mono text-lg font-bold ${decisionTone(decision)}`}>
            {readiness.toFixed(0)}
          </span>
          {/* The decision comes from the engine, not from comparing readiness
              to a threshold duplicated in the browser. */}
          <span className={`block text-[9px] leading-none ${decisionTone(decision)}`}>
            {decision.replace("_", " ")}
          </span>
        </div>
      </div>

      {tp ? (
        <>
          <div className="mb-2 grid grid-cols-3 gap-1.5">
            <div className="rounded-lg border border-sky-500/10 bg-sky-500/5 px-2.5 py-2">
              <div className="mb-0.5 text-[9px] uppercase text-sky-400/60">Entry</div>
              <div className="font-mono text-sm font-bold text-sky-300">{fmt(ep)}</div>
            </div>
            <div className="rounded-lg border border-rose-500/10 bg-rose-500/5 px-2.5 py-2">
              <div className="mb-0.5 text-[9px] uppercase text-rose-400/60">Stop</div>
              <div className="font-mono text-sm font-bold text-rose-300">{fmt(sl)}</div>
            </div>
            <div className="rounded-lg border border-emerald-500/10 bg-emerald-500/5 px-2.5 py-2">
              <div className="mb-0.5 text-[9px] uppercase text-emerald-400/60">Target</div>
              <div className="font-mono text-sm font-bold text-emerald-300">{fmt(tp1)}</div>
            </div>
          </div>

          <div className="flex flex-wrap gap-1.5 text-[10px]">
            {r2r !== undefined && (
              <span className="rounded bg-slate-800/60 px-2 py-0.5 font-mono text-slate-300">
                1:{r2r.toFixed(1)}
              </span>
            )}
            {riskPct && (
              <span className="rounded bg-slate-800/60 px-2 py-0.5 font-mono text-slate-300">
                Risk {riskPct}%
              </span>
            )}
            {leverage !== undefined && leverage !== null && (
              <span className="rounded bg-violet-500/10 px-2 py-0.5 font-mono text-violet-300">
                {leverage}x iso
              </span>
            )}
            {tp2 !== undefined && tp2 !== null && (
              <span className="rounded bg-slate-800/60 px-2 py-0.5 font-mono text-slate-300">
                TP2 {fmt(tp2)}
              </span>
            )}
            {cross !== undefined && (
              <span
                className={`rounded px-2 py-0.5 font-mono ${
                  cross ? "bg-emerald-500/10 text-emerald-400" : "bg-slate-800/40 text-slate-500"
                }`}
              >
                Cross {cross ? "✓" : "—"}
              </span>
            )}
            {cascade && (
              <span
                className={`rounded px-2 py-0.5 font-mono ${
                  String(cascade.status) === "PASS"
                    ? "bg-emerald-500/10 text-emerald-400"
                    : "bg-rose-500/10 text-rose-400"
                }`}
              >
                {String(cascade.status ?? "?")}
              </span>
            )}
            {hasAI && (
              <span
                className={`rounded px-2 py-0.5 font-mono ${
                  aiAdvice === "DOES_NOT_SUPPORT_SHORT"
                    ? "bg-rose-500/10 text-rose-400"
                    : "bg-emerald-500/10 text-emerald-400"
                }`}
              >
                Jev {aiAdvice === "SUPPORTS_SHORT" ? "YES" : "NO"}
              </span>
            )}
            {fundamentalScore !== undefined && (
              <span
                title="Free DexScreener + CoinGecko observation. It has no decision weight or gate authority."
                className="rounded bg-violet-500/10 px-2 py-0.5 font-mono text-violet-300"
              >
                Fund obs {fundamentalScore.toFixed(0)}
              </span>
            )}
          </div>
        </>
      ) : (
        <p className="text-[11px] text-slate-500">
          No trade plan attached — the engine has not published levels for this decision.
        </p>
      )}
    </div>
  );
}

/* ─── Market overview ─── */
function MarketOverview({
  candidates,
  terminal,
}: Readonly<{
  candidates: Record<string, Candidate>;
  terminal: Record<string, unknown> | undefined;
}>) {
  const btc = candidates["BTC/USDT:USDT"];
  const eth = candidates["ETH/USDT:USDT"];
  const btcPrice = typeof btc?.last_price === "number" ? btc.last_price : undefined;
  const ethPrice = typeof eth?.last_price === "number" ? eth.last_price : undefined;

  // Counts come from decision_terminal, which the backend already computed.
  // Recomputing them here produced a third number that could disagree with
  // both the terminal and the table below it.
  const counts = (terminal?.counts ?? {}) as Record<string, number>;
  const num = (key: string) => (typeof counts[key] === "number" ? counts[key] : 0);
  const ready = num("ENTRY_READY") + num("ACTIVE");
  const forming = num("FORMING");
  const late = num("LATE");

  const fmtPrice = (v: number | undefined) => {
    if (v === undefined) return "—";
    if (v >= 1000) return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
    if (v >= 1) return `$${v.toFixed(2)}`;
    return `$${v.toFixed(4)}`;
  };

  const tile = (label: string, value: string, cls: string, border: string) => (
    <div className={`rounded-lg border ${border} p-2.5`}>
      <div className="text-[10px] text-slate-400">{label}</div>
      <div className={`mt-0.5 font-mono text-sm font-bold ${cls}`}>{value}</div>
    </div>
  );

  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
      {tile("BTC", fmtPrice(btcPrice), "text-amber-200", "border-amber-500/15 bg-amber-500/5")}
      {tile("ETH", fmtPrice(ethPrice), "text-indigo-200", "border-indigo-500/15 bg-indigo-500/5")}
      {tile("Tracked", String(Object.keys(candidates).length), "text-slate-200", "border-slate-700/30 bg-slate-800/30")}
      {tile("Ready", String(ready), "text-emerald-300", "border-emerald-500/15 bg-emerald-500/5")}
      {tile("Forming", String(forming), "text-sky-300", "border-sky-500/15 bg-sky-500/5")}
      {tile("Late", String(late), "text-amber-300", "border-amber-500/15 bg-amber-500/5")}
    </div>
  );
}

/* ─── Top candidates ─── */
function TopCandidates({
  rows,
  excludeSymbols,
}: Readonly<{ rows: [string, Candidate][]; excludeSymbols: Set<string> }>) {
  // Ranked by the engine's readiness. No client-side floor: a candidate is
  // shown because it is among the highest-ranked, not because it cleared an
  // arbitrary number invented here.
  const top5 = rows
    .filter(([sym]) => !excludeSymbols.has(sym))
    .filter(([, c]) => getED(c) !== undefined)
    .sort(([, a], [, b]) => getReadiness(b) - getReadiness(a))
    .slice(0, 5);

  if (top5.length === 0) return null;

  return (
    <section>
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
        <BarChart3 size={13} className="mr-1.5 inline text-sky-400" /> Top Candidates
      </h2>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        {top5.map(([symbol, candidate]) => {
          const r = getReadiness(candidate);
          const dec = getDecision(candidate);
          const tp = getTradePlan(candidate);
          const shortName = symbol.replace("/USDT:USDT", "").replace("/USDT", "");

          return (
            <div key={symbol} className="rounded-lg border border-slate-800/60 bg-slate-900/50 p-3">
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-sm font-bold text-white">{shortName}</span>
                <span className={`text-[10px] font-semibold ${decisionTone(dec)}`}>
                  {dec.replace("_", " ")}
                </span>
              </div>
              <div className="mb-2 font-mono text-lg font-bold text-slate-200">{r.toFixed(0)}</div>
              {tp && (
                <div className="grid grid-cols-3 gap-1 text-[10px]">
                  <div>
                    <span className="text-slate-500">EP</span>{" "}
                    <span className="font-mono text-sky-300">{fmt(tp.entry_price as number)}</span>
                  </div>
                  <div>
                    <span className="text-slate-500">SL</span>{" "}
                    <span className="font-mono text-rose-300">{fmt(tp.stop_loss as number)}</span>
                  </div>
                  <div>
                    <span className="text-slate-500">TP</span>{" "}
                    <span className="font-mono text-emerald-300">
                      {fmt(tp.take_profit_1 as number)}
                    </span>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

/* ─── Observational outcome tracking ─── */
function PaperTradeResults() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        // The basePath prefix is required: without it the request bypasses the
        // Next rewrite and 404s behind nginx, so this panel never rendered.
        const r = await fetch("/dashboard/api/backtest/results", { cache: "no-store" });
        if (r.ok) setData(await r.json());
      } catch {
        /* transient; retried on the next tick */
      }
    };
    void load();
    const t = setInterval(() => void load(), 60000);
    return () => clearInterval(t);
  }, []);

  if (!data) return null;
  const stats = (data.stats ?? {}) as Record<string, unknown>;
  const trades = Number(stats.total_trades ?? 0);
  if (!Number.isFinite(trades) || trades === 0) {
    return (
      <section>
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
          <DollarSign size={13} className="mr-1.5 inline text-amber-400" /> Outcome tracking
        </h2>
        <p className="rounded-lg border border-slate-800/60 bg-slate-900/50 p-3 text-[11px] text-slate-500">
          No settled observational outcomes yet. Each ENTRY_READY signal is tracked against
          live prices at its stop, targets, or the 24h timeout.
        </p>
      </section>
    );
  }

  const n = (key: string, digits: number) => Number(stats[key] ?? 0).toFixed(digits);
  const tile = (label: string, value: string, cls: string) => (
    <div className="rounded-lg border border-slate-800/60 bg-slate-900/50 p-2.5">
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className={`mt-0.5 font-mono text-sm font-bold ${cls}`}>{value}</div>
    </div>
  );

  return (
    <section>
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
        <DollarSign size={13} className="mr-1.5 inline text-amber-400" /> Outcome tracking · $100 ·
        4–18x isolated
      </h2>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        {tile("Capital", `$${n("current_capital", 2)}`, "text-amber-300")}
        {tile("Trades", String(trades), "text-slate-200")}
        {tile("Win Rate", `${n("win_rate", 0)}%`, "text-emerald-400")}
        {tile("Max DD", `${n("max_drawdown_pct", 1)}%`, "text-rose-400")}
        {tile("PF", n("profit_factor", 2), "text-sky-300")}
        {tile("Sharpe", n("sharpe_ratio", 2), "text-violet-300")}
      </div>
      {trades < 30 && (
        <p className="mt-2 text-[10px] text-amber-400/80">
          {trades} settled trades is too small a sample to read as performance.
        </p>
      )}
    </section>
  );
}

/* ─── Main dashboard ─── */
export default function Dashboard() {
  const [data, setData] = useState<DashboardSnapshot | null>(null);
  const [mode, setMode] = useState<ConnectionMode>("reconnecting");
  const [generatedAt, setGeneratedAt] = useState<number | null>(null);
  const [freshnessNow, setFreshnessNow] = useState<number | undefined>(undefined);
  const [researchOpen, setResearchOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const latestVersion = useRef(0);
  const latestGeneratedAt = useRef(0);
  const lastStreamEventAt = useRef(0);

  useEffect(() => {
    const t = setInterval(() => setFreshnessNow(Date.now() / 1000), 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    let active = true;
    let pollTimer: ReturnType<typeof setTimeout> | undefined;
    let pollAttempt = 0;
    let streaming = false;
    let hasSnapshot = false;
    let watchdog: ReturnType<typeof setInterval> | undefined;
    const stream = new EventSource("/dashboard/api/stream");

    const accept = (snapshot: DashboardSnapshot) => {
      if (!active) return;
      // Version is monotonic within one backend process. After a backend
      // restart the counter can legitimately fall below what this tab has
      // already seen, so a newer generated_at must also win — otherwise the
      // tab silently ignores every snapshot until a manual reload.
      const newerVersion = snapshot.snapshot_version > latestVersion.current;
      const newerClock = snapshot.generated_at > latestGeneratedAt.current;
      if (!newerVersion && !newerClock) return;
      latestVersion.current = snapshot.snapshot_version;
      latestGeneratedAt.current = snapshot.generated_at;
      hasSnapshot = true;
      setData(snapshot);
      setGeneratedAt(snapshot.generated_at * 1000);
    };

    const schedulePoll = (delay: number) => {
      if (!active || pollTimer !== undefined) return;
      pollTimer = setTimeout(() => {
        pollTimer = undefined;
        if (!active) return;
        void (async () => {
          try {
            const resp = await fetch("/dashboard/api/candidates", { cache: "no-store" });
            const snap = resp.ok ? dashboardSnapshot(await resp.json()) : undefined;
            if (!snap) throw new Error("bad snapshot");
            accept(snap);
            pollAttempt = 0;
            if (streaming) return;
            setMode("polling");
            schedulePoll(5000);
          } catch {
            pollAttempt++;
            if (!streaming) setMode("reconnecting");
            if (!streaming || !hasSnapshot)
              schedulePoll(Math.min(30000, 1000 * 2 ** Math.min(pollAttempt, 5)));
          }
        })();
      }, delay + (delay > 0 ? boundedJitter(1500) : 0));
    };

    const onMsg = (ev: MessageEvent<string>) => {
      try {
        const pkt = dashboardStreamEvent(JSON.parse(ev.data));
        if (!pkt) throw new Error("bad");
        if (pkt.payload) {
          accept(pkt.payload);
          if (hasSnapshot && pollTimer) {
            clearTimeout(pollTimer);
            pollTimer = undefined;
          }
        }
        lastStreamEventAt.current = Date.now();
        streaming = true;
        setMode("stream");
      } catch {
        streaming = false;
        setMode("reconnecting");
        schedulePoll(1000);
      }
    };

    stream.onopen = () => {
      pollAttempt = 0;
      if (!hasSnapshot) setMode("reconnecting");
    };
    stream.onerror = () => {
      streaming = false;
      setMode("reconnecting");
      schedulePoll(1000);
    };
    stream.onmessage = onMsg;
    stream.addEventListener(
      "snapshot",
      (e) => e instanceof MessageEvent && onMsg(e as MessageEvent<string>),
    );
    stream.addEventListener(
      "heartbeat",
      (e) => e instanceof MessageEvent && onMsg(e as MessageEvent<string>),
    );
    schedulePoll(0);
    watchdog = setInterval(() => {
      if (!active || !streaming || lastStreamEventAt.current <= 0) return;
      if (Date.now() - lastStreamEventAt.current > 45000) {
        streaming = false;
        setMode("reconnecting");
        schedulePoll(0);
      }
    }, 5000);

    return () => {
      active = false;
      stream.close();
      if (pollTimer) clearTimeout(pollTimer);
      if (watchdog) clearInterval(watchdog);
    };
  }, []);

  const candidates = useMemo(
    () => (data?.candidates ?? {}) as Record<string, Candidate>,
    [data],
  );
  const nowSeconds = freshnessNow;

  // Ranked by the engine's readiness. The previous sort used the retired
  // Gen-1 top-level `score`, which is null on every live packet, so the table
  // was effectively unordered.
  const rows = useMemo(
    () =>
      Object.entries(candidates).sort(
        ([, a], [, b]) => getReadiness(b) - getReadiness(a),
      ),
    [candidates],
  );

  const freshnessSummary = useMemo(
    () => summarizeCandidateFreshness(candidates as Record<string, unknown>, freshnessNow),
    [candidates, freshnessNow],
  );

  // Actionable decisions only. The engine already applied every gate; adding
  // a second filter here (the old "FORMING and readiness >= 45 and cascade
  // PASS") invented a policy the backend never agreed to.
  const signals = useMemo(
    () => rows.filter(([, c]) => ACTIONABLE.has(getDecision(c))),
    [rows],
  );

  const signalSymbols = useMemo(() => new Set(signals.map(([s]) => s)), [signals]);

  const terminal = data?.decision_terminal as Record<string, unknown> | undefined;

  return (
    <main className="min-h-dvh bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 pb-20 text-slate-100">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_50%_0%,rgba(16,185,129,0.03),transparent_50%)]" />

      <header className="sticky top-0 z-40 border-b border-slate-800/40 bg-slate-950/80 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4 sm:px-6">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-500/15">
            <Activity size={16} className="text-emerald-400" />
          </div>
          <div className="min-w-0">
            <h1 className="truncate text-base font-bold tracking-tight text-white">
              WaterfallHunter
            </h1>
            <p className="hidden text-[10px] text-slate-500 sm:block">Signal Terminal</p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <span className="hidden rounded-full border border-amber-400/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-200 sm:inline-flex">
              SIGNAL_ONLY · LIVE TRADING OFF
            </span>
            {generatedAt !== null && (
              <time className="hidden font-mono text-[10px] text-slate-600 md:inline">
                {new Date(generatedAt).toLocaleTimeString()}
              </time>
            )}
            <span
              className={`flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                freshnessSummary.state === "fresh"
                  ? "bg-emerald-500/10 text-emerald-300"
                  : "bg-amber-500/10 text-amber-300"
              }`}
            >
              <Clock3 size={10} />
              {freshnessSummary.fresh}/{freshnessSummary.total}
            </span>
            <span
              className={`flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                mode === "stream"
                  ? "bg-emerald-500/10 text-emerald-300"
                  : mode === "polling"
                    ? "bg-sky-500/10 text-sky-300"
                    : "bg-amber-500/10 text-amber-300"
              }`}
            >
              {mode === "stream" ? <Wifi size={10} /> : <WifiOff size={10} />}
              {mode === "stream" ? "Live" : mode === "polling" ? "Poll" : "Recon"}
            </span>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-7xl space-y-6 px-4 py-5 sm:px-6">
        {data === null ? (
          <div className="flex min-h-[300px] flex-col items-center justify-center text-center">
            <div className="mb-3 h-3 w-3 animate-pulse rounded-full bg-emerald-400" />
            <p className="text-base font-medium text-slate-300">Loading…</p>
            <p className="mt-1 text-xs text-slate-600">Waiting for stream</p>
          </div>
        ) : (
          <>
            {/* ─── 1. Actionable signals ─── */}
            <section>
              <div className="mb-3 flex items-center gap-2">
                <Zap size={14} className="text-emerald-400" />
                <h2 className="text-xs font-semibold uppercase tracking-wide text-emerald-400">
                  Signals ({signals.length})
                </h2>
              </div>
              {signals.length > 0 ? (
                <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
                  {signals.map(([symbol, candidate]) => (
                    <SignalCard key={symbol} symbol={symbol} candidate={candidate} />
                  ))}
                </div>
              ) : (
                <p className="rounded-lg border border-slate-800/60 bg-slate-900/50 p-3 text-[11px] text-slate-500">
                  No actionable signal right now. This is the normal state: the engine is
                  fail-closed, so a candidate becomes ENTRY_READY only when every gate passes.
                </p>
              )}
            </section>

            {/* ─── 2. Market ─── */}
            <section>
              <div className="mb-3 flex items-center gap-2">
                <Activity size={14} className="text-amber-400" />
                <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Market
                </h2>
              </div>
              <MarketOverview candidates={candidates} terminal={terminal} />
            </section>

            {/* ─── 3. Top candidates ─── */}
            <TopCandidates rows={rows} excludeSymbols={signalSymbols} />

            {/* ─── 4. Observational outcomes ─── */}
            <PaperTradeResults />

            {/* ─── 5. Decision terminal ─── */}
            <section>
              <div className="mb-3 flex items-center gap-2">
                <Target size={14} className="text-sky-400" />
                <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Decision Terminal
                </h2>
              </div>
              <CandidateTable candidates={candidates} nowSeconds={nowSeconds} />
              <div className="mt-4">
                <DecisionTerminal
                  terminal={data.decision_terminal}
                  candidates={data.candidates as Record<string, Candidate>}
                  nowSeconds={nowSeconds}
                />
              </div>
            </section>

            {/* ─── 6. Settings ─── */}
            <details
              className="overflow-hidden rounded-xl border border-slate-800/40 bg-slate-950/40"
              open={settingsOpen}
              onToggle={(e) => setSettingsOpen(e.currentTarget.open)}
            >
              <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-xs font-semibold text-slate-500">
                <span className="flex items-center gap-2">
                  <SlidersHorizontal size={13} /> Decision Settings
                </span>
                <ChevronDown
                  size={14}
                  className={`transition-transform ${settingsOpen ? "rotate-180" : ""}`}
                />
              </summary>
              {settingsOpen && (
                <div className="border-t border-slate-800/40 px-4 py-4">
                  <SettingsPanel />
                </div>
              )}
            </details>

            {/* ─── 7. Research ─── */}
            <details id="research"
              className="overflow-hidden rounded-xl border border-slate-800/40 bg-slate-950/40"
              open={researchOpen}
              onToggle={(event) => setResearchOpen(event.currentTarget.open)}
            >
              <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-xs font-semibold text-slate-500">
                <span className="flex items-center gap-2">
                  <FlaskConical size={13} /> Research &amp; Diagnostics
                </span>
                <ChevronDown
                  size={14}
                  className={`transition-transform ${researchOpen ? "rotate-180" : ""}`}
                />
              </summary>
              {researchOpen && (
                <div className="space-y-5 border-t border-slate-800/40 px-4 py-4">
                  <OutcomeEvidence />
                  <RecentSignals />
                  <HistoricalOutcomes />
                  <ProductionEvidence />
                  <FeatureReplay />
                  <LifecycleShadow />
                  <BacktestLab />
                  <SignalFunnel funnel={data?.signal_funnel as SignalFunnelData | undefined} />
                  <FinalRanking ranking={data?.final_ranking} />
                </div>
              )}
            </details>
          </>
        )}
      </div>
    </main>
  );
}
