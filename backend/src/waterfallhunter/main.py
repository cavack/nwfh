import logging
import asyncio
import json
import math
import time
from datetime import datetime, timezone
from typing import Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from typing import Annotated

from waterfallhunter.config import settings
from waterfallhunter.core.db import DBAdapter
from waterfallhunter.core.schema_contract import (
    CURRENT_RUNTIME_SCHEMA_VERSION,
    require_managed_schema,
)
from waterfallhunter.discovery.lbank_scanner import LBankCatalogScanner
from waterfallhunter.discovery.dexscreener import DexScreenerClient
from waterfallhunter.discovery.onchain import OnChainIntelligence
from waterfallhunter.core.multi_exchange_validator import MultiExchangeValidator
from waterfallhunter.core.entry_decision import (
    EntryDecisionPolicy,
    build_entry_decision,
    build_expired_entry_decision,
    build_invalidated_entry_decision,
    project_leverage_advisory,
)
from waterfallhunter.core.entry_decision_store import (
    EntryDecisionStore,
    EntryOutcomeResolutionWorker,
    StaleCandidateLifecycleError,
)
from waterfallhunter.core.notifier import TelegramNotifier, TelegramSignalTransport
from waterfallhunter.core.notification_delivery import (
    DurableNotificationWorker,
    NotificationDeliveryError,
    notification_delivery_health,
)
from waterfallhunter.core.ai_veto import (
    AIVetoEngine,
    CANONICAL_ADVISORY_DELIVERY_GRACE_SECONDS,
)
from waterfallhunter.core.fundamental_scorer import FundamentalScorer
from waterfallhunter.core.fundamental_observation_store import FundamentalObservationStore
from waterfallhunter.core.risk_manager import build_signal_leverage_advisory
from waterfallhunter.core.dashboard import compact_metrics
from waterfallhunter.core.decision_terminal import build_decision_terminal
from waterfallhunter.core.dashboard_projection import project_dashboard_payload
from waterfallhunter.core.dashboard_stream import (
    DashboardEventBuffer,
    DashboardSnapshot,
    DashboardStreamEvent,
    serialize_sse_event,
)
from waterfallhunter.core.final_ranking import FinalRanking
from waterfallhunter.core.signal_funnel import SignalFunnel
from waterfallhunter.core.stage_lifecycle import StageLifecycleStore
from waterfallhunter.core.lifecycle_v2_shadow import (
    build_lifecycle_v2_evidence_from_metrics,
    compare_v1_v2_shadow,
    evaluate_lifecycle_v2_shadow,
)
from waterfallhunter.core.lifecycle_v2_shadow_store import (
    LifecycleV2ShadowStore,
    LifecycleV2ShadowStoreError,
)
from waterfallhunter.core.historical_outcome_store import HistoricalOutcomeStore
from waterfallhunter.core.production_evidence import (
    ProductionEvidenceRecorder,
    build_production_replay_context,
    causal_age_seconds,
)
from waterfallhunter.core.decision_provenance import (
    build_decision_contract,
    decision_contract_sha256,
)
from waterfallhunter.core.feature_replay import FeatureReplayStore, FeatureReplayWorker
from waterfallhunter.core.lbank_execution_shadow import LBankExecutionShadowWorker
from waterfallhunter.core.lbank_execution_store import LBankExecutionStore
from waterfallhunter.core.lbank_execution_candidate import (
    LBankExecutionCandidateEnricher,
)
from waterfallhunter.core.lbank_execution_decision import (
    LBankExecutionDecisionLogger,
)
from waterfallhunter.core.lbank_signal_ledger import (
    LBankSignalLedger,
)
from waterfallhunter.core.signal_metadata import (
    STRICT_STRATEGY_PROFILE,
    build_signal_metadata_input,
)
from waterfallhunter.core.backtester_v2 import SCHEMA as BACKTEST_V2_SCHEMA
from waterfallhunter.core.managed_sqlite import connect_managed_sqlite
from waterfallhunter.core.runtime_settings import RuntimeSettingsStore
from waterfallhunter.routes_runtime_settings import (
    bind_store as bind_runtime_settings_store,
    router as runtime_settings_router,
)
from waterfallhunter.core.signal_metadata_store import (
    require_signal_metadata_completeness,
)
from waterfallhunter.core.lbank_signal_outcome import (
    LBankSignalOutcomeEvaluator,
    LBankSignalOutcomeStore,
    LBankSignalSettlementWorker,
)
from waterfallhunter.routes_execution_suitability import (
    build_execution_suitability_router,
)
from waterfallhunter.routes_execution_outcomes import (
    build_execution_outcome_router,
)
from waterfallhunter.routes_historical_outcomes import (
    build_historical_outcome_router,
)
from waterfallhunter.routes_production_evidence import (
    build_production_evidence_router,
)
from waterfallhunter.routes_feature_replay import build_feature_replay_router
from waterfallhunter.routes_lifecycle_v2_shadow import (
    build_lifecycle_v2_shadow_router,
)
from waterfallhunter.routes_backtest_lab import build_backtest_lab_router
from waterfallhunter.routes_recent_signals import build_recent_signals_router
from waterfallhunter.core.request_body_limit import RequestBodyLimitMiddleware
from waterfallhunter.core.lbank_execution_outcome_report import (
    LBankExecutionOutcomeReport,
)
from waterfallhunter.core.hunter_schedule import (
    DEFAULT_EVALUATION_CONCURRENCY,
    HunterDeadlineSchedule,
)
from waterfallhunter.core.runtime_memory import trim_process_heap

logging.basicConfig(level=settings.log_level)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("WaterfallHunter")


@asynccontextmanager
async def app_lifespan(_: FastAPI):
    await startup_event()
    try:
        yield
    finally:
        await shutdown_event()


def _signal_alert_allowed(metrics: dict) -> bool:
    """Return whether a TRIGGERED event carries a strict (non-experimental) profile.

    Historically this read ``readiness_score`` / ``structure_status``, keys no
    producer ever wrote, so it returned False for every real packet and every
    TRIGGERED event was logged as "experimental". The canonical decision packet
    is the authority: a strict profile is one whose entry decision is not
    hard-blocked and whose cascade gate passed.
    """
    profile = str(metrics.get("strategy_profile") or "")
    if profile and profile != STRICT_STRATEGY_PROFILE:
        return False
    if not profile:
        return True
    decision = metrics.get("entry_decision")
    if not isinstance(decision, dict):
        return False
    if decision.get("hard_blocked") is True:
        return False
    cascade = metrics.get("cascade_intelligence")
    cascade_status = str(cascade.get("status") or "") if isinstance(cascade, dict) else ""
    return cascade_status == "PASS"

app = FastAPI(
    title="WaterfallHunter API - Production",
    version="7.5.1-Stable",
    lifespan=app_lifespan,
)
app.add_middleware(
    RequestBodyLimitMiddleware,
    path="/api/backtest-lab/replay",
    maximum_bytes=10_000_000,
)

db = DBAdapter(
    db_path=settings.registry_db_path,
    verify_schema=False,
)

stage_lifecycle_store = StageLifecycleStore(
    db_path=db.db_path,
    verify_schema=False,
)

entry_decision_store = EntryDecisionStore(
    db_path=db.db_path,
    verify_schema=False,
    source_revision=settings.source_revision,
)

lifecycle_v2_shadow_store = LifecycleV2ShadowStore(
    db_path=db.db_path,
    verify_schema=False,
)

historical_outcome_store = HistoricalOutcomeStore(
    db_path=db.db_path,
    cache_ttl_seconds=60.0,
    verify_schema=False,
)

production_evidence_recorder = ProductionEvidenceRecorder(
    db_path=db.db_path,
    bucket_seconds=900,
    verify_schema=False,
)

feature_replay_store = FeatureReplayStore(
    db_path=db.db_path,
    verify_schema=False,
)
feature_replay_worker = FeatureReplayWorker(
    feature_replay_store,
    batch_size=3,
)
fundamental_observation_store = FundamentalObservationStore(db.db_path)

app.include_router(
    build_execution_suitability_router(
        db.db_path
    )
)

app.include_router(
    build_historical_outcome_router(
        historical_outcome_store
    )
)

app.include_router(
    build_production_evidence_router(
        production_evidence_recorder
    )
)

app.include_router(
    build_feature_replay_router(
        feature_replay_store
    )
)

app.include_router(
    build_lifecycle_v2_shadow_router(
        lifecycle_v2_shadow_store
    )
)

app.include_router(
    build_execution_outcome_router(
        db.db_path
    )
)

app.include_router(
    build_backtest_lab_router(
        artifact_hmac_key=settings.backtest_artifact_hmac_key,
        db_path=db.db_path,
    )
)

app.include_router(
    build_recent_signals_router(db.db_path)
)

runtime_settings_store = RuntimeSettingsStore(db.db_path)
bind_runtime_settings_store(runtime_settings_store)
app.include_router(runtime_settings_router)

execution_suitability_enricher = (
    LBankExecutionCandidateEnricher(
        db_path=db.db_path,
        cache_ttl_seconds=60.0,
    )
)

signal_ledger = LBankSignalLedger(
    db_path=db.db_path,
    verify_schema=False,
)

signal_outcome_store = LBankSignalOutcomeStore(
    db_path=db.db_path,
    verify_schema=False,
)

execution_outcome_report = LBankExecutionOutcomeReport(
    db_path=db.db_path,
)

dex_client = DexScreenerClient(
    enabled=settings.dexscreener_enabled,
    token_map_json=settings.dexscreener_token_map_json,
)

onchain_client = OnChainIntelligence(
    etherscan_api_key=settings.etherscan_api_key,
    solscan_api_key=settings.solscan_api_key,
    large_transfer_usd=settings.onchain_large_transfer_usd,
)

scanner = LBankCatalogScanner(
    db_adapter=db,
    max_price=1.0,
    min_volume_usdt=2_000_000.0,
    dex_client=dex_client,
    onchain_client=onchain_client,
)

execution_decision_logger = (
    LBankExecutionDecisionLogger(
        db_path=db.db_path,
        enricher=execution_suitability_enricher,
        evaluation_bucket_seconds=3600,
        snapshot_interval_seconds=3600,
        retention_days=30,
        volume_gate_min_usdt=(
            scanner.min_volume_usdt
        ),
        verify_schema=False,
    )
)

validator = MultiExchangeValidator()
validator.stage_lifecycle_store = stage_lifecycle_store

notifier = TelegramNotifier(
    db_adapter=db,
    scanner=scanner,
)

ai_veto = AIVetoEngine()
# Fundamental evidence is deliberately observational. Only the two free,
# deterministic-ish market-data providers run here; LunarCrush and X/Nitter
# are not attempted without a paid/keyed integration. It is collected for
# FORMING/ENTRY_READY candidates only and never enters build_entry_decision.
fundamental_scorer = FundamentalScorer(
    weights={"dexscreener": 0.5, "coingecko": 0.5, "lunarcrush": 0.0, "twitter": 0.0},
    enabled_sources=("dexscreener", "coingecko"),
    cache_ttl=300,
    timeout=8.0,
)
_fundamental_gate: asyncio.Semaphore | None = None
_FUNDAMENTAL_MAX_CONCURRENT = 2


_hunter_running = False
_hunter_last_completed_at: float | None = None
_hunter_last_progress_at: float | None = None
_hunter_in_flight_count = 0
_hunter_due_backlog = 0
_hunter_task: asyncio.Task | None = None
_hunter_stop_event = asyncio.Event()
_HUNTER_STARTUP_DELAY_SECONDS = 5.0
_HUNTER_SHUTDOWN_GRACE_SECONDS = 5.0
_WATCH_FULL_REVALIDATION_SECONDS = 3600.0
_WATCH_FULL_REVALIDATION_MIN_START_INTERVAL_SECONDS = 25.0
_watch_full_revalidation_next_allowed_at = 0.0
_watch_source_hint_cache: dict[str, dict] = {}
_lbank_execution_shadow_worker: LBankExecutionShadowWorker | None = None
_signal_settlement_worker: LBankSignalSettlementWorker | None = None
_entry_outcome_resolution_worker: EntryOutcomeResolutionWorker | None = None
_entry_notification_worker: DurableNotificationWorker | None = None
_entry_notification_probe: dict | None = None
_signal_evidence_metrics_last_refresh = 0.0
_signal_evidence_metrics_lock = asyncio.Lock()
_sse_clients = set()
# Full dashboard snapshots are multi-megabyte objects. Keep only a short
# contiguous SSE replay window; older reconnects already fail closed to a
# freshly generated full snapshot.
_DASHBOARD_REPLAY_EVENT_LIMIT = 8
_DASHBOARD_CLIENT_QUEUE_LIMIT = 2
# Rebuilding the canonical dashboard walks all active candidates and multiple
# read-only enrichers. Coalesce that CPU/DB work independently from the 15s
# heartbeat so one connected browser cannot force a full aggregation every second.
_DASHBOARD_SNAPSHOT_BROADCAST_INTERVAL_SECONDS = 5.0
_DASHBOARD_HEARTBEAT_INTERVAL_SECONDS = 15.0
_dashboard_event_buffer = DashboardEventBuffer(
    replay_limit=_DASHBOARD_REPLAY_EVENT_LIMIT,
    epoch=DashboardEventBuffer.restart_safe_epoch(),
)
_dashboard_preview_cache: tuple[
    DashboardEventBuffer,
    DashboardSnapshot,
    float,
] | None = None
_dashboard_poll_build_task: asyncio.Task[tuple[float, dict[str, Any]]] | None = None
_DASHBOARD_PREVIEW_CACHE_SECONDS = 1.0
_background_tasks = set()

if "tracked_candidates" not in globals():
    tracked_candidates = Gauge(
        "waterfall_tracked_candidates",
        "Active candidates currently tracked",
    )

if "catalog_last_refresh" not in globals():
    catalog_last_refresh = Gauge(
        "waterfall_catalog_last_refresh_timestamp",
        "Unix timestamp of the last successful LBank catalog refresh",
    )

if "notification_delivery_state" not in globals():
    notification_delivery_state = Gauge(
        "waterfall_notification_delivery_state_total",
        "Durable outbox events by delivery state",
        ["state"],
    )

if "notification_oldest_pending_age" not in globals():
    notification_oldest_pending_age = Gauge(
        "waterfall_notification_oldest_pending_age_seconds",
        "Age of the oldest active durable notification event",
    )

if "hunter_last_cycle" not in globals():
    hunter_last_cycle = Gauge(
        "waterfall_hunter_last_cycle_timestamp",
        "Unix timestamp of the last completed hunter cycle",
    )

if "hunter_last_progress" not in globals():
    hunter_last_progress = Gauge(
        "waterfall_hunter_last_progress_timestamp",
        "Unix timestamp of the last hunter loop progress",
    )

if "derivative_packet_outcomes" not in globals():
    derivative_packet_outcomes = Counter(
        "waterfall_derivative_packet_outcomes_total",
        "Complete or incomplete live derivatives packets evaluated by the hunter.",
        (
            "source",
            "outcome",
            "reason",
        ),
    )

if "lbank_execution_shadow_enabled_metric" not in globals():
    lbank_execution_shadow_enabled_metric = Gauge(
        "waterfall_lbank_execution_shadow_enabled",
        "Whether LBank execution shadow observation is enabled.",
    )

if "lbank_execution_shadow_attempted_metric" not in globals():
    lbank_execution_shadow_attempted_metric = Gauge(
        "waterfall_lbank_execution_shadow_attempted_total",
        "Total LBank shadow observations attempted since process start.",
    )

if "lbank_execution_shadow_observed_metric" not in globals():
    lbank_execution_shadow_observed_metric = Gauge(
        "waterfall_lbank_execution_shadow_observed_total",
        "Total successful LBank shadow observations since process start.",
    )

if "lbank_execution_shadow_unavailable_metric" not in globals():
    lbank_execution_shadow_unavailable_metric = Gauge(
        "waterfall_lbank_execution_shadow_unavailable_total",
        "Total unavailable LBank shadow observations since process start.",
    )

if "lbank_execution_shadow_last_progress_metric" not in globals():
    lbank_execution_shadow_last_progress_metric = Gauge(
        "waterfall_lbank_execution_shadow_last_progress_timestamp",
        "Unix timestamp of the last LBank shadow worker progress.",
    )

if "signal_ledger_metric" not in globals():
    signal_ledger_metric = Gauge(
        "waterfall_signal_ledger_total",
        "Immutable natural production signals captured by the ledger.",
    )

if "signal_outcome_metric" not in globals():
    signal_outcome_metric = Gauge(
        "waterfall_signal_outcomes_total",
        "Append-only 24h observational signal outcomes.",
    )

if "signal_mature_pending_metric" not in globals():
    signal_mature_pending_metric = Gauge(
        "waterfall_signal_mature_pending_total",
        "Mature natural signals still awaiting an observational outcome.",
    )

if "signal_decisive_outcome_metric" not in globals():
    signal_decisive_outcome_metric = Gauge(
        "waterfall_signal_decisive_outcomes_total",
        "Outcomes usable for observational execution-suitability comparison.",
    )

if "signal_evidence_ready_metric" not in globals():
    signal_evidence_ready_metric = Gauge(
        "waterfall_signal_evidence_ready",
        "Whether outcome evidence is sufficient for observational comparison only.",
    )

if "signal_evidence_span_metric" not in globals():
    signal_evidence_span_metric = Gauge(
        "waterfall_signal_evidence_span_days",
        "Observation span in days across decisive signal outcomes.",
    )

if "signal_settlement_coverage_metric" not in globals():
    signal_settlement_coverage_metric = Gauge(
        "waterfall_signal_settlement_coverage_ratio",
        "Settled share of mature signals; NaN when no signal is mature.",
    )

if "signal_oldest_mature_pending_age_metric" not in globals():
    signal_oldest_mature_pending_age_metric = Gauge(
        "waterfall_signal_oldest_mature_pending_age_seconds",
        "Age beyond maturity of the oldest signal awaiting settlement; NaN when empty.",
    )

if "signal_settlement_worker_running_metric" not in globals():
    signal_settlement_worker_running_metric = Gauge(
        "waterfall_signal_settlement_worker_running",
        "Whether the observational signal settlement worker is running.",
    )

if "signal_settlement_cycles_metric" not in globals():
    signal_settlement_cycles_metric = Gauge(
        "waterfall_signal_settlement_cycles_total",
        "Signal settlement cycles attempted since process start.",
    )

if "signal_settlement_failures_metric" not in globals():
    signal_settlement_failures_metric = Gauge(
        "waterfall_signal_settlement_failures_total",
        "Signal settlement cycles failed since process start.",
    )

if "signal_settlement_last_completed_metric" not in globals():
    signal_settlement_last_completed_metric = Gauge(
        "waterfall_signal_settlement_last_completed_timestamp",
        "Unix timestamp of the last completed signal settlement cycle.",
    )

if "signal_settlement_last_error_metric" not in globals():
    signal_settlement_last_error_metric = Gauge(
        "waterfall_signal_settlement_last_error_timestamp",
        "Unix timestamp of the last failed signal settlement cycle; zero when none.",
    )

if "signal_proxy_execution_metric" not in globals():
    signal_proxy_execution_metric = Gauge(
        "waterfall_signal_proxy_execution_total",
        "Natural signals by bounded trigger-time volume/execution comparison.",
        ("comparison",),
    )

if "signal_proxy_execution_decisive_metric" not in globals():
    signal_proxy_execution_decisive_metric = Gauge(
        "waterfall_signal_proxy_execution_decisive_outcomes_total",
        "Decisive outcomes by bounded trigger-time volume/execution comparison.",
        ("comparison",),
    )

if "candidate_state_metric" not in globals():
    candidate_state_metric = Gauge(
        "waterfall_candidate_state_total",
        "Current scan-eligible candidates by bounded lifecycle state.",
        ("state",),
    )

if "websocket_active_tasks_metric" not in globals():
    websocket_active_tasks_metric = Gauge(
        "waterfall_websocket_active_tasks",
        "Process-local active WebSocket consumer tasks.",
    )

if "websocket_liquidation_tasks_metric" not in globals():
    websocket_liquidation_tasks_metric = Gauge(
        "waterfall_websocket_liquidation_tasks",
        "Process-local liquidation WebSocket consumer tasks.",
    )

if "websocket_shared_liquidation_tasks_metric" not in globals():
    websocket_shared_liquidation_tasks_metric = Gauge(
        "waterfall_websocket_shared_liquidation_tasks",
        "Exchange-wide shared liquidation WebSocket consumers.",
    )

if "websocket_shared_liquidation_subscribers_metric" not in globals():
    websocket_shared_liquidation_subscribers_metric = Gauge(
        "waterfall_websocket_shared_liquidation_subscribers",
        "Symbols routed through exchange-wide liquidation consumers.",
    )

if "websocket_shared_evidence_tasks_metric" not in globals():
    websocket_shared_evidence_tasks_metric = Gauge(
        "waterfall_websocket_shared_evidence_tasks",
        "Exchange-scoped shared FUEL-RICH market-evidence WebSocket consumers.",
    )

if "websocket_shared_evidence_subscribers_metric" not in globals():
    websocket_shared_evidence_subscribers_metric = Gauge(
        "waterfall_websocket_shared_evidence_subscribers",
        "Symbols routed through shared FUEL-RICH market-evidence consumers.",
    )

if "websocket_shared_evidence_active_subscribers_metric" not in globals():
    websocket_shared_evidence_active_subscribers_metric = Gauge(
        "waterfall_websocket_shared_evidence_active_subscribers",
        "Symbols owned by the current immutable shared-evidence transport generations.",
    )

if "websocket_shared_evidence_exchange_instances_metric" not in globals():
    websocket_shared_evidence_exchange_instances_metric = Gauge(
        "waterfall_websocket_shared_evidence_exchange_instances",
        "Current shared-evidence CCXT Pro exchange generations.",
    )

if "websocket_shared_evidence_reconcile_tasks_metric" not in globals():
    websocket_shared_evidence_reconcile_tasks_metric = Gauge(
        "waterfall_websocket_shared_evidence_reconcile_tasks",
        "Current single-flight shared-evidence reconciliation tasks.",
    )

if "websocket_shared_evidence_retirement_failures_metric" not in globals():
    websocket_shared_evidence_retirement_failures_metric = Gauge(
        "waterfall_websocket_shared_evidence_retirement_failures",
        "Cumulative shared-evidence transport retirement failures in this process.",
    )

if "websocket_shared_evidence_blocked_exchanges_metric" not in globals():
    websocket_shared_evidence_blocked_exchanges_metric = Gauge(
        "waterfall_websocket_shared_evidence_blocked_exchanges",
        "Shared-evidence venues blocked because prior transport retirement is unproven.",
    )

if "websocket_shared_evidence_direct_fallbacks_metric" not in globals():
    websocket_shared_evidence_direct_fallbacks_metric = Gauge(
        "waterfall_websocket_shared_evidence_direct_fallbacks",
        "Current FUEL-RICH symbols using direct WebSocket evidence because the venue lacks shared multi-symbol support.",
    )

if "websocket_shared_evidence_generations_metric" not in globals():
    websocket_shared_evidence_generations_metric = Gauge(
        "waterfall_websocket_shared_evidence_generations",
        "Cumulative shared-evidence transport generations started in this process.",
    )

if "websocket_direct_exchange_instances_metric" not in globals():
    websocket_direct_exchange_instances_metric = Gauge(
        "waterfall_websocket_direct_exchange_instances",
        "Live symbol-owned direct CCXT Pro exchange instances.",
    )

if "websocket_liquidation_exchange_instances_metric" not in globals():
    websocket_liquidation_exchange_instances_metric = Gauge(
        "waterfall_websocket_liquidation_exchange_instances",
        "Live symbol-owned liquidation CCXT Pro exchange instances.",
    )

if "websocket_direct_exchange_retire_tasks_metric" not in globals():
    websocket_direct_exchange_retire_tasks_metric = Gauge(
        "waterfall_websocket_direct_exchange_retire_tasks",
        "Direct CCXT Pro exchange instances currently being retired.",
    )

if "websocket_liquidation_exchange_retire_tasks_metric" not in globals():
    websocket_liquidation_exchange_retire_tasks_metric = Gauge(
        "waterfall_websocket_liquidation_exchange_retire_tasks",
        "Liquidation CCXT Pro exchange instances currently being retired.",
    )

if "websocket_exchange_close_finalizer_tasks_metric" not in globals():
    websocket_exchange_close_finalizer_tasks_metric = Gauge(
        "waterfall_websocket_exchange_close_finalizer_tasks",
        "Timed-out CCXT exchange closes still owned by retrying finalizers.",
    )

if "websocket_exchange_close_timeouts_metric" not in globals():
    websocket_exchange_close_timeouts_metric = Gauge(
        "waterfall_websocket_exchange_close_timeouts",
        "Cumulative generic CCXT exchange close timeouts in this process.",
    )

if "websocket_direct_ccxt_clients_metric" not in globals():
    websocket_direct_ccxt_clients_metric = Gauge(
        "waterfall_websocket_direct_ccxt_clients",
        "CCXT Pro WebSocket client objects owned by direct-evidence exchanges.",
    )

if "websocket_direct_ccxt_subscriptions_metric" not in globals():
    websocket_direct_ccxt_subscriptions_metric = Gauge(
        "waterfall_websocket_direct_ccxt_subscriptions",
        "CCXT Pro WebSocket subscriptions owned by direct-evidence exchanges.",
    )

if "websocket_direct_idle_ccxt_clients_metric" not in globals():
    websocket_direct_idle_ccxt_clients_metric = Gauge(
        "waterfall_websocket_direct_idle_ccxt_clients",
        "Direct-evidence CCXT clients with no subscriptions or pending futures.",
    )

if "websocket_liquidation_ccxt_clients_metric" not in globals():
    websocket_liquidation_ccxt_clients_metric = Gauge(
        "waterfall_websocket_liquidation_ccxt_clients",
        "CCXT Pro WebSocket client objects owned by liquidation exchanges.",
    )

if "websocket_liquidation_ccxt_subscriptions_metric" not in globals():
    websocket_liquidation_ccxt_subscriptions_metric = Gauge(
        "waterfall_websocket_liquidation_ccxt_subscriptions",
        "CCXT Pro WebSocket subscriptions owned by liquidation exchanges.",
    )

if "websocket_shared_evidence_ccxt_clients_metric" not in globals():
    websocket_shared_evidence_ccxt_clients_metric = Gauge(
        "waterfall_websocket_shared_evidence_ccxt_clients",
        "CCXT Pro WebSocket client objects owned by shared-evidence generations.",
    )

if "websocket_shared_evidence_ccxt_subscriptions_metric" not in globals():
    websocket_shared_evidence_ccxt_subscriptions_metric = Gauge(
        "waterfall_websocket_shared_evidence_ccxt_subscriptions",
        "CCXT Pro WebSocket subscriptions owned by shared-evidence generations.",
    )

if "websocket_ccxt_clients_metric" not in globals():
    websocket_ccxt_clients_metric = Gauge(
        "waterfall_websocket_ccxt_clients",
        "CCXT Pro WebSocket client objects owned by current exchange instances.",
    )

if "websocket_ccxt_subscriptions_metric" not in globals():
    websocket_ccxt_subscriptions_metric = Gauge(
        "waterfall_websocket_ccxt_subscriptions",
        "CCXT Pro WebSocket subscriptions owned by current exchange instances.",
    )

if "hunter_evaluation_duration_metric" not in globals():
    hunter_evaluation_duration_metric = Histogram(
        "waterfall_hunter_evaluation_duration_seconds",
        "Candidate evaluation wall time by bounded lifecycle state.",
        ("state",),
    )

if "market_evidence_stage_duration_metric" not in globals():
    market_evidence_stage_duration_metric = Histogram(
        "waterfall_market_evidence_stage_duration_seconds",
        "Bounded live evidence stage duration.",
        ("stage", "outcome"),
    )

if "primary_source_attempts_metric" not in globals():
    primary_source_attempts_metric = Histogram(
        "waterfall_primary_source_attempts",
        "Price-compatible primary venues attempted per candidate evaluation.",
    )

if "hunter_in_flight_metric" not in globals():
    hunter_in_flight_metric = Gauge(
        "waterfall_hunter_in_flight_evaluations",
        "Current bounded candidate evaluations in flight.",
    )

if "hunter_due_backlog_metric" not in globals():
    hunter_due_backlog_metric = Gauge(
        "waterfall_hunter_due_backlog",
        "Due candidates waiting for an evaluation slot.",
    )

if "market_evidence_path_metric" not in globals():
    market_evidence_path_metric = Counter(
        "waterfall_market_evidence_path_total",
        "Candidate microstructure evidence acquisition paths.",
        ("outcome",),
    )

if "candle_cache_events_metric" not in globals():
    candle_cache_events_metric = Gauge(
        "waterfall_candle_cache_events",
        "Process-local causal closed-OHLCV cache events.",
        ("outcome",),
    )

_CANDIDATE_METRIC_STATES = (
    "WATCH",
    "FUEL-RICH",
    "PRE-TRIGGER",
    "ARMED",
    "TRIGGERED",
)

_HUNTER_METRIC_STATES = (*_CANDIDATE_METRIC_STATES, "OTHER")
_EVIDENCE_METRIC_STAGES = ("microstructure", "candles", "context", "total")
_EVIDENCE_METRIC_OUTCOMES = ("complete", "unavailable")


def _nonnegative_finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0.0 else None


def _record_market_evidence_stage_durations(
    runtime: dict[str, Any],
    *,
    outcome: str,
) -> None:
    stages = runtime.get("stage_durations_seconds")
    if not isinstance(stages, dict):
        return
    for stage in _EVIDENCE_METRIC_STAGES:
        duration = _nonnegative_finite(stages.get(stage))
        if duration is not None:
            market_evidence_stage_duration_metric.labels(
                stage=stage, outcome=outcome
            ).observe(duration)


def _record_market_evidence_paths(runtime: dict[str, Any]) -> None:
    for path, field in (
        ("ws_hit", "ws_evidence_hits"),
        ("rest_fallback", "rest_evidence_fallbacks"),
    ):
        count = _nonnegative_finite(runtime.get(field))
        if count is not None and count > 0.0:
            market_evidence_path_metric.labels(outcome=path).inc(count)


def _record_adaptive_pipeline_observation(
    state: object,
    evaluation_duration_seconds: float | None,
    runtime_diagnostics: dict | None,
) -> None:
    state_label = str(state or "WATCH").upper()
    if state_label not in _HUNTER_METRIC_STATES:
        state_label = "OTHER"

    evaluation_duration = _nonnegative_finite(evaluation_duration_seconds)
    if evaluation_duration is not None:
        hunter_evaluation_duration_metric.labels(state=state_label).observe(
            evaluation_duration
        )

    runtime = runtime_diagnostics if isinstance(runtime_diagnostics, dict) else {}
    attempts = _nonnegative_finite(runtime.get("source_attempts"))
    if attempts is not None:
        primary_source_attempts_metric.observe(attempts)

    outcome = str(runtime.get("outcome") or "unavailable")
    if outcome not in _EVIDENCE_METRIC_OUTCOMES:
        outcome = "unavailable"
    _record_market_evidence_stage_durations(runtime, outcome=outcome)
    _record_market_evidence_paths(runtime)

def _update_adaptive_pipeline_metrics() -> None:
    hunter_in_flight_metric.set(float(_hunter_in_flight_count))
    hunter_due_backlog_metric.set(float(_hunter_due_backlog))
    diagnostics_getter = getattr(validator.candle_analyzer, "cache_diagnostics", None)
    diagnostics = diagnostics_getter() if callable(diagnostics_getter) else {}
    mapping = {"hit": "hits", "miss": "misses", "eviction": "evictions"}
    for outcome, field in mapping.items():
        value = diagnostics.get(field, 0) if isinstance(diagnostics, dict) else 0
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        candle_cache_events_metric.labels(outcome=outcome).set(max(0.0, number))

_PROXY_EXECUTION_METRIC_COMPARISONS = (
    "AGREE_ACCEPT",
    "AGREE_REJECT",
    "VOLUME_PASS_EXECUTION_REJECT",
    "VOLUME_REJECT_EXECUTION_ACCEPT",
    "UNKNOWN",
)

_DERIVATIVE_METRIC_SOURCES = frozenset(
    {
        "binance",
        "bybit",
        "kucoin",
        "okx",
        "mexc",
        "bingx",
        "gateio",
        "bitget",
        "htx",
    }
)


def derivative_packet_metric_labels(
    packet: dict | None,
) -> dict[str, str]:
    """Return bounded Prometheus labels for a normalized derivative packet."""

    packet = (
        packet
        if isinstance(
            packet,
            dict,
        )
        else {}
    )

    source = str(
        packet.get(
            "source_exchange"
        )
        or ""
    ).lower()

    source = (
        source
        if source in _DERIVATIVE_METRIC_SOURCES
        else "unknown"
    )

    if packet.get(
        "available"
    ) is True:
        return {
            "source": source,
            "outcome": "complete",
            "reason": "none",
        }

    reason = str(
        packet.get(
            "reason"
        )
        or ""
    ).lower()

    reason_codes = (
        (
            "missing valid funding rate",
            "missing_funding_rate",
        ),
        (
            "missing valid open interest",
            "missing_open_interest",
        ),
        (
            "missing valid taker buy/sell ratio",
            "missing_taker_buy_sell_ratio",
        ),
        (
            "missing valid top trader",
            "missing_top_trader_ratio",
        ),
        (
            "invalid derivatives retrieval timestamp",
            "invalid_retrieval_timestamp",
        ),
        (
            "stale",
            "stale_data",
        ),
        (
            "unsupported derivatives source",
            "unsupported_source",
        ),
        (
            "no complete live derivatives data source",
            "no_complete_source",
        ),
        (
            "source unavailable",
            "source_unavailable",
        ),
    )

    reason_code = next(
        (
            code
            for needle, code
            in reason_codes
            if needle in reason
        ),
        "other",
    )

    return {
        "source": source,
        "outcome": "incomplete",
        "reason": reason_code,
    }


def _apply_signal_leverage_advisory(
    metrics: dict[str, Any],
    execution_suitability: dict[str, Any] | None,
    *,
    decision_status: str,
) -> dict[str, Any]:
    """Attach a decision-aware, signal-only leverage advisory to live metrics."""
    advisory = build_signal_leverage_advisory(
        metrics,
        execution_suitability,
        decision_status=decision_status,
    )
    metrics["leverage_advisory"] = advisory
    metrics["leverage_policy"] = {
        "version": advisory["policy_version"],
        "minimum": advisory["minimum"],
        "maximum": advisory["maximum"],
        "symbol_agnostic": advisory["symbol_agnostic"],
        "paper_only": True,
        "advisory_only": True,
    }
    metrics["applied_leverage"] = (
        advisory.get("leverage")
        if advisory.get("status") == "AVAILABLE"
        else None
    )
    return advisory


def _record_derivative_packet_outcome(
    metrics: dict | None,
) -> None:
    if not isinstance(
        metrics,
        dict,
    ):
        return

    packet = metrics.get(
        "derivatives"
    )

    if not isinstance(
        packet,
        dict,
    ):
        return

    derivative_packet_outcomes.labels(
        **derivative_packet_metric_labels(
            packet
        )
    ).inc()


def _start_background_task(
    coro,
):
    """Track long-lived tasks so shutdown can cancel them cleanly."""

    task = asyncio.create_task(
        coro
    )

    _background_tasks.add(
        task
    )

    task.add_done_callback(
        _background_tasks.discard
    )

    return task


def _build_lbank_execution_shadow_worker(
) -> LBankExecutionShadowWorker | None:
    if not settings.lbank_execution_shadow_enabled:
        return None

    store = LBankExecutionStore(
        db_path=db.db_path
    )

    return LBankExecutionShadowWorker(
        store,
        batch_size=(
            settings
            .lbank_execution_shadow_batch_size
        ),
        success_recheck_seconds=(
            settings
            .lbank_execution_shadow_success_recheck_seconds
        ),
        failure_recheck_seconds=(
            settings
            .lbank_execution_shadow_failure_recheck_seconds
        ),
    )


def _telegram_probe_allows_worker(probe: dict[str, Any]) -> bool:
    if probe.get("reachable") is True:
        return True
    status_code = probe.get("status_code")
    return status_code not in {400, 401, 403, 404}


def _build_entry_notification_worker() -> DurableNotificationWorker | None:
    if (
        not notifier.enabled
        or not notifier.signal_delivery_enabled
        or notifier.signal_delivery_cutover_at is None
    ):
        return None
    transport = TelegramSignalTransport(
        str(settings.telegram_token),
        str(settings.telegram_chat_id),
        cutover_at=notifier.signal_delivery_cutover_at,
        decision_db_path=db.db_path,
        max_entry_age_seconds=int(EntryDecisionPolicy().max_analysis_age_seconds),
    )
    return DurableNotificationWorker(
        db.db_path,
        transport,
        worker_id="canonical-entry-telegram",
        outbox_table="entry_notification_outbox",
        transport_timeout_seconds=10.0,
        advisory_wait_seconds=CANONICAL_ADVISORY_DELIVERY_GRACE_SECONDS,
        verify_schema=False,
    )



# ─── Per-Signal Backtest Recorder ─────────────────────────────────────────
# Records each ENTRY_READY signal: entry, exit, and result (win/loss/timeout)

_backtest_active_trades: dict[str, dict] = {}  # symbol -> trade info
# A newly listed contract is invisible until the next catalogue refresh, so
# six hours (the old value) was far too coarse for a market that lists daily.
_CATALOG_REFRESH_INTERVAL_SECONDS = 900
_BACKTEST_DB_PATH = settings.backtester_v2_db_path
_BACKTEST_CAPITAL_USD = 100.0
_BACKTEST_POSITION_FRACTION = 0.30
_BACKTEST_MIN_LEVERAGE = 4
_BACKTEST_TIMEOUT_SECONDS = 86_400
# Taker in, taker out, as a fraction of notional. Ignoring fees turned every
# marginal loss into a break-even in the recorded results.
_BACKTEST_ROUND_TRIP_FEE_PCT = 0.12

def _start_backtest_trade(symbol: str, metrics: dict, decision: dict) -> None:
    """Start tracking a observational outcome for an ENTRY_READY signal.

    Reads the canonical decision packet. The previous implementation read
    ``metrics["signal_summary"]`` and ``decision["entry_price"]`` — neither key
    exists, so ``entry_price`` was always 0 and the function returned before
    recording anything. Every trade in the "per-signal backtest" was therefore
    never opened, which is why the results table never grew.
    """
    if symbol in _backtest_active_trades:
        return
    try:
        plan = decision.get("trade_plan")
        if not isinstance(plan, dict):
            return

        def num(value: Any) -> float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return 0.0
            return float(value) if math.isfinite(float(value)) else 0.0

        entry_price = num(plan.get("entry_price"))
        stop_loss = num(plan.get("stop_loss"))
        tp1 = num(plan.get("take_profit_1"))
        tp2 = num(plan.get("take_profit_2"))
        readiness = num(decision.get("entry_readiness"))

        # Short geometry: 0 < tp2 < tp1 < entry < stop.
        if not (0 < tp2 < tp1 < entry_price < stop_loss):
            return

        advisory = decision.get("leverage_advisory")
        leverage = None
        if isinstance(advisory, dict) and advisory.get("status") == "AVAILABLE":
            candidate = advisory.get("leverage")
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                leverage = candidate
        if leverage is None:
            # No advisory means the risk bounds were not satisfiable; record
            # the trade at the policy minimum rather than inventing a ladder.
            leverage = _BACKTEST_MIN_LEVERAGE

        position_usd = _BACKTEST_CAPITAL_USD * _BACKTEST_POSITION_FRACTION
        shares = position_usd * leverage / entry_price

        _backtest_active_trades[symbol] = {
            "symbol": symbol,
            "entry_time": time.time(),
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "leverage": leverage,
            "position_usd": position_usd,
            "shares": shares,
            "score": readiness,
            "policy_version": str(decision.get("policy_version") or ""),
            "outcome": None,
        }
        logger.info(
            "Observational outcome OPENED %s: entry=%.8f SL=%.8f TP1=%.8f TP2=%.8f lev=%dx readiness=%.1f",
            symbol, entry_price, stop_loss, tp1, tp2, leverage, readiness,
        )
    except Exception as exc:
        logger.warning("Observational outcome open failed for %s: %s", symbol, exc)


def _backtest_current_price(candidate: dict) -> float:
    """Resolve the live price for an open observational outcome.

    ``current_price`` is never written by any producer. The live price lives on
    the candidate row as ``last_price`` (LBank reference), with the order-book
    mid as a fallback.
    """
    if not isinstance(candidate, dict):
        return 0.0

    def num(value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.0
        value = float(value)
        return value if math.isfinite(value) and value > 0 else 0.0

    price = num(candidate.get("last_price"))
    if price > 0:
        return price
    metrics = candidate.get("metrics")
    if isinstance(metrics, dict):
        micro = metrics.get("microstructure")
        if isinstance(micro, dict):
            bid = num(micro.get("best_bid"))
            ask = num(micro.get("best_ask"))
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
    return 0.0


def _check_backtest_trades(candidates_by_symbol: dict) -> None:
    """Settle open observational outcomes against live prices.

    Short-only: TP levels sit below entry, the stop sits above. Fees are
    charged on both legs so a stop-out is recorded as the loss it actually is.
    """
    if not _backtest_active_trades:
        return
    try:
        settled: list[tuple] = []
        now = time.time()

        for symbol, trade in list(_backtest_active_trades.items()):
            price = _backtest_current_price(candidates_by_symbol.get(symbol, {}))
            if price <= 0:
                # Symbol left the universe: close at timeout rather than
                # leaking the position forever.
                if now - trade["entry_time"] > _BACKTEST_TIMEOUT_SECONDS:
                    settled.append((symbol, trade, trade["entry_price"], "timeout"))
                continue

            entry = trade["entry_price"]
            outcome: str | None = None
            exit_price = price

            # Stop is checked first: within one polling interval both levels
            # can be touched, and assuming the favourable one is how outcome tracking
            # results drift away from reality.
            if price >= trade["stop_loss"]:
                outcome, exit_price = "loss_sl", trade["stop_loss"]
            elif price <= trade["tp2"]:
                outcome, exit_price = "win_tp2", trade["tp2"]
            elif price <= trade["tp1"]:
                outcome, exit_price = "win_tp1", trade["tp1"]
            elif now - trade["entry_time"] > _BACKTEST_TIMEOUT_SECONDS:
                outcome, exit_price = "timeout", price

            if outcome is not None:
                settled.append((symbol, trade, exit_price, outcome))

        if not settled:
            return

        rows = []
        for symbol, trade, exit_price, outcome in settled:
            entry = trade["entry_price"]
            lev = trade["leverage"]
            # Short: profit when price falls.
            gross_pct = ((entry - exit_price) / entry) * 100.0
            net_pct = (gross_pct - _BACKTEST_ROUND_TRIP_FEE_PCT) * lev
            pnl_usd = trade["position_usd"] * (net_pct / 100.0)
            entry_dt = datetime.fromtimestamp(trade["entry_time"], tz=timezone.utc).isoformat()
            rows.append((
                symbol, entry_dt, datetime.now(timezone.utc).isoformat(),
                entry, exit_price, net_pct, pnl_usd, outcome, lev,
                trade["position_usd"], trade["shares"],
                json.dumps({
                    "entry_readiness": trade["score"],
                    "outcome_status": outcome,
                    "policy_version": trade.get("policy_version", ""),
                    "gross_pct": round(gross_pct, 6),
                    "fee_pct": _BACKTEST_ROUND_TRIP_FEE_PCT,
                }),
            ))
            logger.info(
                "Observational outcome CLOSED %s: %s exit=%.8f net=%.2f%% ($%.2f) lev=%dx",
                symbol, outcome, exit_price, net_pct, pnl_usd, lev,
            )

        with connect_managed_sqlite(_BACKTEST_DB_PATH, timeout=10.0) as conn:
            conn.executescript(BACKTEST_V2_SCHEMA)
            conn.executemany(
                "INSERT INTO bt_v2_trades (symbol, entry_time, exit_time, entry_price,"
                " exit_price, pnl_pct, pnl_usd, outcome, leverage, position_size_usd,"
                " shares_traded, signal_data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            conn.commit()

        for symbol, _trade, _exit, _outcome in settled:
            _backtest_active_trades.pop(symbol, None)
    except Exception as exc:
        logger.warning("Observational outcome settlement failed: %s", exc)


async def _entry_notification_loop(interval_seconds: float = 0.5) -> None:
    while _hunter_running:
        worker = _entry_notification_worker
        if worker is None:
            return
        try:
            dispatch_now = int(time.time())
            outcome = await worker.dispatch_once(now=dispatch_now)
            if outcome is None:
                await asyncio.sleep(interval_seconds)
            elif outcome.state != "DELIVERED":
                logger.warning(
                    "Canonical Telegram delivery %s for %s (%s)",
                    outcome.state, outcome.event_id, outcome.error_code,
                )
                if (
                    outcome.error_code == "HTTP_429"
                    and outcome.next_available_at is not None
                ):
                    await asyncio.sleep(
                        max(
                            interval_seconds,
                            float(outcome.next_available_at - dispatch_now),
                        )
                    )
        except NotificationDeliveryError as exc:
            logger.exception("Canonical Telegram delivery worker failed: %s", exc)
            await asyncio.sleep(max(interval_seconds, 5.0))


async def _refresh_canonical_ai_advisory(
    symbol: str,
    decision_event_id: int,
    metrics_snapshot: dict,
    decision_snapshot: dict,
) -> None:
    advisory = await ai_veto.advisory_for_decision(
        symbol, metrics_snapshot, decision_snapshot
    )
    try:
        advisory_event_id = entry_decision_store.append_advisory(
            decision_event_id,
            advisory,
            advisory_at=int(time.time()),
        )
    except Exception:
        logger.exception("Unable to persist canonical AI advisory for %s", symbol)
        return
    advisory = {**advisory, "advisory_event_id": advisory_event_id}
    live = scanner.active_candidates.get(symbol)
    if not isinstance(live, dict):
        return
    current_metrics = live.get("metrics")
    if not isinstance(current_metrics, dict):
        return
    current_decision = current_metrics.get("entry_decision")
    if not isinstance(current_decision, dict):
        return
    if current_decision.get("event_id") != decision_event_id:
        return
    # Merge, never replace. The deterministic veto (bid/ask wall check) lives
    # on the same dict and is set synchronously before the decision is built.
    # The LLM advisory dict has no ``deterministic_veto`` key, so a full
    # replacement silently cleared the veto and a later freshness projection
    # re-ran build_entry_decision on metrics that no longer carried it.
    existing = current_metrics.get("ai_advisory")
    if isinstance(existing, dict):
        current_metrics["ai_advisory"] = {**existing, **advisory}
    else:
        current_metrics["ai_advisory"] = advisory


async def _refresh_observational_fundamental(
    symbol: str,
    candidate_token: dict[str, Any],
    decision_event_id: int,
) -> None:
    """Attach free fundamental evidence without affecting the decision.

    This runs after the canonical packet is persisted. It has no score weight,
    cannot veto, promote or downgrade a decision, and is intentionally limited
    to two concurrent requests. Its purpose is to build a decision/outcome
    dataset for a later replay, not to smuggle unvalidated sentiment into the
    live model.
    """
    global _fundamental_gate
    if _fundamental_gate is None:
        _fundamental_gate = asyncio.Semaphore(_FUNDAMENTAL_MAX_CONCURRENT)
    try:
        async with _fundamental_gate:
            result = await fundamental_scorer.score(symbol.split("/")[0])
    except Exception as exc:  # noqa: BLE001
        logger.info("Observational fundamental unavailable for %s: %s", symbol, type(exc).__name__)
        return

    observation = {
        **result,
        "observational_only": True,
        "decision_mutated": False,
        "sources": ["dexscreener", "coingecko"],
        "collected_at": int(time.time()),
    }
    try:
        fundamental_observation_store.upsert(
            decision_event_id,
            symbol,
            observation["collected_at"],
            observation,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unable to persist observational fundamental for %s: %s", symbol, type(exc).__name__)

    live = scanner.active_candidates.get(symbol)
    if not isinstance(live, dict):
        return
    live_metrics = live.get("metrics")
    if not isinstance(live_metrics, dict):
        return
    # Do not overwrite a newer evaluation's metrics with a result that began
    # against an older snapshot.
    if scanner.active_candidates.get(symbol) is not candidate_token:
        return
    live_metrics["fundamental_observational"] = observation


def _lbank_shadow_health_snapshot() -> dict:
    if _lbank_execution_shadow_worker is None:
        return {
            "enabled": False,
            "running": False,
            "batch_size": None,
            "last_started_at": None,
            "last_progress_at": None,
            "last_completed_at": None,
            "total_attempted": 0,
            "total_observed": 0,
            "total_unavailable": 0,
        }

    return {
        "enabled": True,
        **(
            _lbank_execution_shadow_worker
            .health_snapshot()
        ),
    }


async def _fetch_signal_outcome_candles(
    signal: dict,
    start_ms: int,
    end_ms: int,
) -> list:
    try:
        trigger_metrics = json.loads(
            signal.get(
                "trigger_metrics_json"
            )
            or "{}"
        )
    except (
        TypeError,
        json.JSONDecodeError,
    ):
        return []

    exchange_name = trigger_metrics.get(
        "exchange"
    )
    mapped_symbol = trigger_metrics.get(
        "mapped_symbol"
    )
    if not exchange_name or not mapped_symbol:
        return []

    rows_by_timestamp: dict[int, list] = {}
    cursor = int(start_ms)

    for _ in range(20):
        packet = await (
            validator.gateway
            .fetch_ohlcv_from_source(
                str(exchange_name),
                str(mapped_symbol),
                timeframe="1m",
                since=cursor,
                limit=1000,
            )
        )
        rows = packet.get("data") or []
        if not rows:
            break

        last_timestamp = None
        for row in rows:
            try:
                timestamp = int(row[0])
            except (
                IndexError,
                TypeError,
                ValueError,
            ):
                continue
            if start_ms <= timestamp < end_ms:
                rows_by_timestamp[timestamp] = row
            if (
                last_timestamp is None
                or timestamp > last_timestamp
            ):
                last_timestamp = timestamp

        if last_timestamp is None:
            break
        next_cursor = last_timestamp + 60_000
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if cursor >= end_ms:
            break

    return [
        rows_by_timestamp[timestamp]
        for timestamp in sorted(
            rows_by_timestamp
        )
    ]


def _build_signal_settlement_worker(
) -> LBankSignalSettlementWorker:
    return LBankSignalSettlementWorker(
        signal_outcome_store,
        _fetch_signal_outcome_candles,
        horizon_seconds=86400,
        close_delay_seconds=120,
        batch_size=3,
    )


def _cost_component(
    value: object,
    *,
    source: str,
    observed_at: int,
    reason: str,
) -> dict[str, object]:
    available = bool(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0
    )
    return {
        "value": float(value) if available else None,
        "source": source if available else None,
        "classification": "MODELED_COST" if available else "UNAVAILABLE",
        "observed_at": observed_at if available else None,
        "interval": "decision_time_estimate" if available else None,
        "available": available,
        "reason": None if available else reason,
    }


def _entry_outcome_research_provenance(
    metrics: dict[str, Any],
    *,
    decision_contract_hash: str,
    decision_at: int,
    reference_source: str | None,
    reference_mapped_symbol: str | None,
) -> dict[str, Any]:
    """Freeze decision-time identity and costs without changing model behavior."""
    exchange = str(reference_source or "").strip().lower()
    mapped_symbol = str(reference_mapped_symbol or "").strip()
    microstructure = (
        metrics.get("microstructure")
        if isinstance(metrics.get("microstructure"), dict)
        else {}
    )
    revision = str(settings.source_revision or "").strip()
    revision_verified = bool(
        len(revision) == 40
        and all(character in "0123456789abcdef" for character in revision)
    )
    unavailable_cost = {
        "value": None,
        "source": None,
        "classification": "UNAVAILABLE",
        "observed_at": None,
        "interval": None,
        "available": False,
    }
    return {
        "source_revision": revision if revision_verified else None,
        "source_revision_status": (
            "VERIFIED_GIT_REVISION" if revision_verified else "UNAVAILABLE"
        ),
        "decision_contract_sha256": decision_contract_hash,
        "contract": {
            "available": exchange == "lbank" and bool(mapped_symbol),
            "exchange": exchange or None,
            "mapped_symbol": mapped_symbol or None,
            "market_type": "linear_usdt_perpetual",
            "settlement_asset": "USDT",
            "spot_substitution_allowed": False,
            "cross_venue_substitution_allowed": False,
        },
        "outcome_contract": {
            "version": "lbank_linear_perpetual_outcome_v1",
            "direction": "SHORT",
            "horizon_seconds": 86_400,
            "timeframe": "1m",
            "closed_candles_only": True,
            "complete_window_required": True,
            "price_source": "closed_1m_trade_ohlcv_proxy",
        },
        "costs": {
            "fees": {**unavailable_cost, "reason": "fee ledger not observed at decision time"},
            "entry_slippage": _cost_component(
                microstructure.get("entry_slippage_pct"),
                source="decision_time_microstructure",
                observed_at=decision_at,
                reason="entry slippage estimate unavailable",
            ),
            "exit_slippage": _cost_component(
                microstructure.get("exit_slippage_pct"),
                source="decision_time_microstructure",
                observed_at=decision_at,
                reason="exit slippage estimate unavailable",
            ),
            "funding": {
                **unavailable_cost,
                "reason": "future holding-interval funding not yet observed",
            },
        },
    }


def _decision_outcome_classification(status: str) -> str:
    if status == "STOP_FIRST":
        return "STOP"
    if status == "NO_LEVEL_HIT_24H":
        return "TIMEOUT"
    if status in {"TP1_ONLY_24H", "TP1_THEN_STOP", "TP2_FIRST", "TP2_AFTER_TP1"}:
        return "WIN"
    return "UNAVAILABLE"


def _decision_outcome_gross_r(
    classification: str, status: str, plan: dict[str, Any], candles: list
) -> float | None:
    try:
        entry = float(plan["entry_price"])
        stop = float(plan["stop_loss"])
        risk = stop - entry
        if risk <= 0:
            return None
        if classification == "STOP":
            return -1.0
        if classification == "WIN":
            if status == "TP1_THEN_STOP":
                return None
            target_key = "take_profit_2" if status.startswith("TP2") else "take_profit_1"
            return (entry - float(plan[target_key])) / risk
        if classification == "TIMEOUT" and candles:
            return (entry - float(candles[-1][4])) / risk
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return None


def _entry_outcome_capture_parts(
    capture: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    contract = capture.get("contract") if isinstance(capture.get("contract"), dict) else {}
    plan = capture.get("trade_plan") if isinstance(capture.get("trade_plan"), dict) else {}
    outcome_contract = (
        capture.get("outcome_contract")
        if isinstance(capture.get("outcome_contract"), dict)
        else {}
    )
    return contract, plan, outcome_contract


def _entry_outcome_contract_available(
    contract: dict[str, Any],
    plan: dict[str, Any],
    outcome_contract: dict[str, Any],
) -> bool:
    required_levels = ("entry_price", "stop_loss", "take_profit_1", "take_profit_2")
    return bool(
        contract.get("available") is True
        and str(contract.get("exchange") or "").lower() == "lbank"
        and contract.get("market_type") == "linear_usdt_perpetual"
        and contract.get("mapped_symbol")
        and outcome_contract.get("closed_candles_only") is True
        and outcome_contract.get("complete_window_required") is True
        and all(plan.get(key) is not None for key in required_levels)
    )


def _entry_outcome_costs_complete(costs: dict[str, Any]) -> bool:
    required = ("fees", "entry_slippage", "exit_slippage", "funding")
    return bool(
        costs
        and all(
            isinstance(costs.get(name), dict) and costs[name].get("available") is True
            for name in required
        )
    )


_ENTRY_OUTCOME_MAX_CAPTURE_AGE_SECONDS = 72 * 60 * 60


def _entry_outcome_no_candles_result(capture: dict[str, Any], contract: dict) -> dict | None:
    """Keep fresh gaps retryable, but bound exact-window retries by capture age."""
    captured_at = capture.get("captured_at")
    if isinstance(captured_at, bool) or not isinstance(captured_at, int):
        return None
    if int(time.time()) - captured_at <= _ENTRY_OUTCOME_MAX_CAPTURE_AGE_SECONDS:
        return None
    return {
        "outcome_status": "UNAVAILABLE",
        "classification": "UNAVAILABLE",
        "cost": capture.get("costs"),
        "provenance": {
            "decision_packet_sha256": capture.get("decision_packet_sha256"),
            "decision_contract_sha256": capture.get("decision_contract_sha256"),
            "source_revision": capture.get("source_revision"),
            "contract": contract or None,
        },
        "reason": "exact-contract candle retry window exhausted",
    }


def _entry_outcome_provenance_complete(
    capture: dict[str, Any], classification: str, gross_r: float | None
) -> bool:
    source_revision = capture.get("source_revision")
    decision_contract_sha256 = capture.get("decision_contract_sha256")
    return bool(
        isinstance(source_revision, str)
        and len(source_revision) == 40
        and isinstance(decision_contract_sha256, str)
        and len(decision_contract_sha256) == 64
        and classification in {"WIN", "STOP", "TIMEOUT"}
        and gross_r is not None
    )


async def _resolve_entry_outcome(capture: dict) -> dict | None:
    """Resolve an exact LBank perpetual capture from a complete closed 1m window."""
    contract, plan, outcome_contract = _entry_outcome_capture_parts(capture)
    if not _entry_outcome_contract_available(contract, plan, outcome_contract):
        return {
            "outcome_status": "UNAVAILABLE",
            "classification": "UNAVAILABLE",
            "cost": capture.get("costs"),
            "provenance": {
                "decision_packet_sha256": capture.get("decision_packet_sha256"),
                "source_revision": capture.get("source_revision"),
                "contract": contract or None,
            },
            "reason": "exact LBank linear perpetual capture contract unavailable",
        }
    signal = {
        "id": int(capture["decision_event_id"]),
        "symbol": str(capture.get("symbol") or ""),
        "triggered_at": int(capture["decision_event_at"]),
        "entry_price": plan["entry_price"],
        "stop_loss": plan["stop_loss"],
        "take_profit_1": plan["take_profit_1"],
        "take_profit_2": plan["take_profit_2"],
        "trigger_metrics_json": json.dumps(
            {"exchange": "lbank", "mapped_symbol": contract["mapped_symbol"]}
        ),
    }
    trigger_ms = signal["triggered_at"] * 1000
    fetch_start_ms = (trigger_ms // 60_000) * 60_000
    observation_start_ms = (
        trigger_ms if trigger_ms % 60_000 == 0 else fetch_start_ms + 60_000
    )
    horizon_seconds = int(outcome_contract.get("horizon_seconds") or 86_400)
    candles = await _fetch_signal_outcome_candles(
        signal,
        fetch_start_ms,
        observation_start_ms + horizon_seconds * 1000,
    )
    if not candles:
        return _entry_outcome_no_candles_result(capture, contract)
    outcome = LBankSignalOutcomeEvaluator.evaluate(
        signal, candles, horizon_seconds=horizon_seconds
    )
    status = str(outcome.get("status") or "")
    if status == "DATA_INCOMPLETE":
        return None
    classification = _decision_outcome_classification(status)
    gross_r = _decision_outcome_gross_r(classification, status, plan, candles)
    costs = capture.get("costs") if isinstance(capture.get("costs"), dict) else {}
    cost_complete = _entry_outcome_costs_complete(costs)
    provenance_complete = _entry_outcome_provenance_complete(
        capture, classification, gross_r
    )
    return {
        "outcome_status": "OBSERVED",
        "classification": classification,
        "raw_outcome_status": status,
        "outcome": outcome,
        "gross_r": gross_r,
        "net_r": None,
        "cost": {"components": costs, "complete": cost_complete},
        "provenance": {
            "decision_packet_sha256": capture.get("decision_packet_sha256"),
            "decision_contract_sha256": capture.get("decision_contract_sha256"),
            "source_revision": capture.get("source_revision"),
            "contract": contract,
            "outcome_contract": outcome_contract,
        },
        "scientific_tier": (
            "TIER_C" if cost_complete and provenance_complete else "UNAVAILABLE"
        ),
    }


def _build_entry_outcome_resolution_worker() -> EntryOutcomeResolutionWorker:
    return EntryOutcomeResolutionWorker(
        entry_decision_store,
        _resolve_entry_outcome,
        batch_size=36,
        local_batch_size=300,
        interval_seconds=900.0,
    )


def _update_lbank_shadow_metrics() -> None:
    snapshot = (
        _lbank_shadow_health_snapshot()
    )

    lbank_execution_shadow_enabled_metric.set(
        1.0
        if snapshot[
            "enabled"
        ]
        else 0.0
    )

    lbank_execution_shadow_attempted_metric.set(
        float(
            snapshot.get(
                "total_attempted"
            )
            or 0
        )
    )

    lbank_execution_shadow_observed_metric.set(
        float(
            snapshot.get(
                "total_observed"
            )
            or 0
        )
    )

    lbank_execution_shadow_unavailable_metric.set(
        float(
            snapshot.get(
                "total_unavailable"
            )
            or 0
        )
    )

    last_progress = snapshot.get(
        "last_progress_at"
    )

    if isinstance(
        last_progress,
        (
            int,
            float,
        ),
    ):
        lbank_execution_shadow_last_progress_metric.set(
            float(
                last_progress
            )
        )


def _update_candidate_state_metrics(
    candidates: dict,
) -> None:
    counts = {
        state: 0
        for state in _CANDIDATE_METRIC_STATES
    }
    for candidate in candidates.values():
        if not isinstance(candidate, dict):
            continue
        state = str(
            candidate.get("status")
            or ""
        )
        if state in counts:
            counts[state] += 1
    for state, value in counts.items():
        candidate_state_metric.labels(
            state=state
        ).set(value)


def _update_websocket_metrics() -> None:
    snapshot = validator.ws_manager.runtime_diagnostics()
    websocket_active_tasks_metric.set(float(snapshot["active_tasks"]))
    websocket_liquidation_tasks_metric.set(float(snapshot["liquidation_tasks"]))
    websocket_shared_liquidation_tasks_metric.set(
        float(snapshot["shared_liquidation_tasks"])
    )
    websocket_shared_liquidation_subscribers_metric.set(
        float(snapshot["shared_liquidation_subscribers"])
    )
    websocket_shared_evidence_tasks_metric.set(
        float(snapshot["shared_evidence_tasks"])
    )
    websocket_shared_evidence_subscribers_metric.set(
        float(snapshot["shared_evidence_subscribers"])
    )
    websocket_shared_evidence_active_subscribers_metric.set(
        float(snapshot["shared_evidence_active_subscribers"])
    )
    websocket_shared_evidence_exchange_instances_metric.set(
        float(snapshot["shared_evidence_exchange_instances"])
    )
    websocket_shared_evidence_reconcile_tasks_metric.set(
        float(snapshot["shared_evidence_reconcile_tasks"])
    )
    websocket_shared_evidence_retirement_failures_metric.set(
        float(snapshot["shared_evidence_retirement_failures"])
    )
    websocket_shared_evidence_blocked_exchanges_metric.set(
        float(snapshot["shared_evidence_blocked_exchanges"])
    )
    websocket_shared_evidence_direct_fallbacks_metric.set(
        float(snapshot["shared_evidence_direct_fallbacks"])
    )
    websocket_shared_evidence_generations_metric.set(
        float(snapshot["shared_evidence_generations"])
    )
    websocket_direct_exchange_instances_metric.set(
        float(snapshot["direct_exchange_instances"])
    )
    websocket_liquidation_exchange_instances_metric.set(
        float(snapshot["liquidation_exchange_instances"])
    )
    websocket_direct_exchange_retire_tasks_metric.set(
        float(snapshot["direct_exchange_retire_tasks"])
    )
    websocket_liquidation_exchange_retire_tasks_metric.set(
        float(snapshot["liquidation_exchange_retire_tasks"])
    )
    websocket_exchange_close_finalizer_tasks_metric.set(
        float(snapshot["exchange_close_finalizer_tasks"])
    )
    websocket_exchange_close_timeouts_metric.set(
        float(snapshot["exchange_close_timeouts"])
    )
    websocket_direct_ccxt_clients_metric.set(float(snapshot["direct_ccxt_clients"]))
    websocket_direct_ccxt_subscriptions_metric.set(
        float(snapshot["direct_ccxt_subscriptions"])
    )
    websocket_direct_idle_ccxt_clients_metric.set(
        float(snapshot["direct_idle_ccxt_clients"])
    )
    websocket_liquidation_ccxt_clients_metric.set(
        float(snapshot["liquidation_ccxt_clients"])
    )
    websocket_liquidation_ccxt_subscriptions_metric.set(
        float(snapshot["liquidation_ccxt_subscriptions"])
    )
    websocket_shared_evidence_ccxt_clients_metric.set(
        float(snapshot["shared_evidence_ccxt_clients"])
    )
    websocket_shared_evidence_ccxt_subscriptions_metric.set(
        float(snapshot["shared_evidence_ccxt_subscriptions"])
    )
    websocket_ccxt_clients_metric.set(float(snapshot["ccxt_clients"]))
    websocket_ccxt_subscriptions_metric.set(float(snapshot["ccxt_subscriptions"]))


def _update_signal_settlement_worker_metrics() -> None:
    snapshot = (
        _signal_settlement_worker.health_snapshot()
        if _signal_settlement_worker is not None
        else {}
    )
    signal_settlement_worker_running_metric.set(
        1.0 if snapshot.get("running") is True else 0.0
    )
    signal_settlement_cycles_metric.set(
        float(snapshot.get("total_cycles") or 0)
    )
    signal_settlement_failures_metric.set(
        float(snapshot.get("total_failures") or 0)
    )
    signal_settlement_last_completed_metric.set(
        float(snapshot.get("last_completed_at") or 0.0)
    )
    signal_settlement_last_error_metric.set(
        float(snapshot.get("last_error_at") or 0.0)
    )


async def _update_signal_evidence_metrics(
    *,
    force: bool = False,
) -> None:
    global _signal_evidence_metrics_last_refresh

    now = time.monotonic()
    if (
        not force
        and _signal_evidence_metrics_last_refresh > 0.0
        and now
        - _signal_evidence_metrics_last_refresh
        < 60.0
    ):
        return

    async with _signal_evidence_metrics_lock:
        now = time.monotonic()
        if (
            not force
            and _signal_evidence_metrics_last_refresh > 0.0
            and now
            - _signal_evidence_metrics_last_refresh
            < 60.0
        ):
            return

        _signal_evidence_metrics_last_refresh = now
        report = await asyncio.to_thread(
            execution_outcome_report.build_report
        )
        settlement = report.get("settlement") or {}
        evidence = report.get("evidence") or {}
        proxy_execution = (
            report.get("by_proxy_execution_comparison")
            or {}
        )

        signal_ledger_metric.set(
            float(settlement.get("signal_count") or 0)
        )
        signal_outcome_metric.set(
            float(settlement.get("settled_outcome_count") or 0)
        )
        signal_mature_pending_metric.set(
            float(
                settlement.get(
                    "unsettled_mature_signal_count"
                )
                or 0
            )
        )
        signal_decisive_outcome_metric.set(
            float(evidence.get("decisive_outcome_count") or 0)
        )
        signal_evidence_ready_metric.set(
            1.0
            if evidence.get("ready") is True
            else 0.0
        )
        signal_evidence_span_metric.set(
            float(evidence.get("observation_span_days") or 0.0)
        )
        coverage = settlement.get(
            "mature_settlement_coverage_rate"
        )
        signal_settlement_coverage_metric.set(
            float(coverage)
            if isinstance(coverage, (int, float))
            and not isinstance(coverage, bool)
            else float("nan")
        )
        oldest_pending_age = settlement.get(
            "oldest_unsettled_mature_age_seconds"
        )
        signal_oldest_mature_pending_age_metric.set(
            float(oldest_pending_age)
            if isinstance(oldest_pending_age, (int, float))
            and not isinstance(oldest_pending_age, bool)
            else float("nan")
        )
        for comparison in _PROXY_EXECUTION_METRIC_COMPARISONS:
            group = proxy_execution.get(comparison) or {}
            signal_proxy_execution_metric.labels(
                comparison=comparison
            ).set(float(group.get("signal_count") or 0))
            signal_proxy_execution_decisive_metric.labels(
                comparison=comparison
            ).set(
                float(group.get("decisive_outcome_count") or 0)
            )
        _signal_evidence_metrics_last_refresh = now


def _websocket_source(metrics: object) -> tuple[str, str] | None:
    if not isinstance(metrics, dict):
        return None
    exchange = metrics.get("exchange")
    mapped_symbol = metrics.get("mapped_symbol")
    if not isinstance(exchange, str) or not exchange.strip():
        return None
    if not isinstance(mapped_symbol, str) or not mapped_symbol.strip():
        return None
    return exchange, mapped_symbol


def _retire_websocket_evidence_source(source: tuple[str, str] | None) -> None:
    if source is None:
        return
    manager = validator.ws_manager
    manager.unsubscribe_shared_evidence(*source)
    manager.unsubscribe(*source)


def _sync_websocket_evidence_subscription(
    previous_source: tuple[str, str] | None,
    current_source: tuple[str, str] | None,
    *,
    state: str,
) -> None:
    manager = validator.ws_manager
    if previous_source is not None and previous_source != current_source:
        _retire_websocket_evidence_source(previous_source)
    if current_source is None:
        return
    normalized_state = str(state or "WATCH").upper()
    if normalized_state in {"PRE-TRIGGER", "ARMED"}:
        manager.unsubscribe_shared_evidence(*current_source)
        manager.subscribe(*current_source)
        return
    if normalized_state == "FUEL-RICH":
        if manager.has_shared_evidence_direct_fallback(*current_source):
            manager.subscribe_shared_evidence(*current_source)
            return
        if manager.has_direct_evidence_subscription(*current_source):
            manager.unsubscribe(*current_source)
        manager.subscribe_shared_evidence(*current_source)
        return
    _retire_websocket_evidence_source(current_source)


def _retire_removed_candidate_websocket_sources(
    removed_candidates: dict[str, dict],
) -> None:
    for symbol, candidate in removed_candidates.items():
        _watch_source_hint_cache.pop(symbol, None)
        source = _websocket_source(candidate.get("metrics"))
        _retire_websocket_evidence_source(source)


scanner.on_candidates_removed = _retire_removed_candidate_websocket_sources


def _candidate_evaluation_token_is_current(
    symbol: str,
    token: dict,
) -> bool:
    """Fence an in-flight evaluation against a catalogue active-universe swap."""
    return scanner.active_candidates.get(symbol) is token


def _store_live_metrics(
    symbol: str,
    metrics: dict | None,
):
    normalized = (
        dict(
            metrics
        )
        if isinstance(
            metrics,
            dict,
        )
        else {}
    )

    if (
        normalized.get(
            "score"
        )
        is None
        and not normalized.get(
            "analysis_reason"
        )
    ):
        normalized[
            "analysis_reason"
        ] = str(
            normalized.get(
                "error"
            )
            or "live analysis unavailable"
        )

    scanner.active_candidates.setdefault(
        symbol,
        {},
    )[
        "metrics"
    ] = (
        compact_metrics(
            normalized
        )
        or {}
    )


async def _refresh_ai_advisory_observational(
    symbol: str,
    *,
    analysis_observed_at: int,
    orderbook: dict,
    ticker: dict,
) -> None:
    try:
        advisory = await (
            ai_veto
            .get_observational_advisory(
                symbol,
                orderbook,
                ticker,
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "Observational AI advisory failed for %s",
            symbol,
        )
        return

    live = scanner.active_candidates.get(
        symbol
    )
    if not isinstance(
        live,
        dict,
    ):
        return

    if (
        live.get(
            "analysis_observed_at"
        )
        != analysis_observed_at
    ):
        logger.info(
            "Discarding stale AI advisory for %s",
            symbol,
        )
        return

    current_metrics = live.get(
        "metrics"
    )
    if not isinstance(
        current_metrics,
        dict,
    ):
        return

    updated_metrics = dict(
        current_metrics
    )

    current_advisory = (
        updated_metrics.get(
            "ai_advisory"
        )
    )
    merged_advisory = (
        dict(current_advisory)
        if isinstance(
            current_advisory,
            dict,
        )
        else {}
    )
    merged_advisory.update(
        advisory
    )

    updated_metrics[
        "ai_advisory"
    ] = merged_advisory

    live[
        "metrics"
    ] = (
        compact_metrics(
            updated_metrics
        )
        or {}
    )


def _schedule_ai_advisory_observational(
    symbol: str,
    *,
    analysis_observed_at: int,
    orderbook: dict | None,
    ticker: dict | None,
) -> None:
    if (
        not isinstance(
            orderbook,
            dict,
        )
        or not orderbook
        or not isinstance(
            ticker,
            dict,
        )
        or not ticker
    ):
        return

    _start_background_task(
        _refresh_ai_advisory_observational(
            symbol,
            analysis_observed_at=(
                analysis_observed_at
            ),
            orderbook=dict(
                orderbook
            ),
            ticker=dict(
                ticker
            ),
        )
    )


def _restore_persisted_decision_projection(
    current_decision: dict[str, Any],
    metrics: dict[str, Any],
    persisted_decision: dict[str, Any] | None,
) -> None:
    if not isinstance(persisted_decision, dict):
        return
    if persisted_decision.get("decision") != current_decision.get("decision"):
        return
    event_id = persisted_decision.get("event_id")
    if isinstance(event_id, int) and not isinstance(event_id, bool) and event_id > 0:
        current_decision["event_id"] = event_id
        current_decision["event_persisted"] = False
    if current_decision.get("decision") == "ENTRY_READY":
        persisted_plan = persisted_decision.get("trade_plan")
        if isinstance(persisted_plan, dict):
            current_decision["trade_plan"] = dict(persisted_plan)
    leverage_advisory = persisted_decision.get("leverage_advisory")
    if isinstance(leverage_advisory, dict):
        canonical_leverage_advisory = dict(leverage_advisory)
        current_decision["leverage_advisory"] = canonical_leverage_advisory
        metrics["leverage_advisory"] = dict(canonical_leverage_advisory)
        metrics["applied_leverage"] = (
            canonical_leverage_advisory.get("leverage")
            if canonical_leverage_advisory.get("status") == "AVAILABLE"
            else None
        )
    advisory = persisted_decision.get("ai_advisory")
    if isinstance(advisory, dict):
        metrics["ai_advisory"] = dict(advisory)


def _reconcile_explicit_entry_expirations(*, evaluated_at: int) -> int:
    """Persist EXPIRED only when a prior canonical plan carries explicit expiry."""
    reconciled = 0
    for symbol, previous in entry_decision_store.latest_map().items():
        expired = build_expired_entry_decision(previous, evaluated_at=evaluated_at)
        if expired is None:
            continue
        event_id = entry_decision_store.append_if_changed_with_capture(
            symbol, expired, captured_at=evaluated_at
        )
        if event_id is None:
            continue
        expired["event_id"] = event_id
        expired["event_persisted"] = True
        live = scanner.active_candidates.get(symbol)
        if isinstance(live, dict):
            metrics = live.get("metrics")
            if isinstance(metrics, dict):
                metrics["entry_decision"] = expired
        reconciled += 1
    return reconciled


def _reconcile_inactive_actionable_decisions(
    *,
    active_symbols: set[str],
    evaluated_at: int,
) -> int:
    """Invalidate actionable decisions after a symbol leaves the active universe."""
    reconciled = 0
    for symbol, previous in entry_decision_store.latest_map().items():
        if symbol in active_symbols:
            continue
        invalidated = build_invalidated_entry_decision(
            previous,
            evaluated_at=evaluated_at,
            block_reason="CANDIDATE_NO_LONGER_ACTIVE",
        )
        if invalidated is None:
            continue
        event_id = entry_decision_store.append_if_changed_with_capture(
            symbol, invalidated, captured_at=evaluated_at
        )
        if event_id is None:
            continue
        reconciled += 1
    return reconciled


def _project_entry_decision_freshness(
    metrics: dict[str, Any],
    *,
    candidate_status: str,
    evaluated_at: float,
    analysis_age_seconds: float | None,
    reference_age_seconds: float | None,
) -> dict[str, Any]:
    stored = metrics.get("entry_decision")
    if not isinstance(stored, dict) or stored.get("decision") not in {"FORMING", "ENTRY_READY", "ACTIVE", "NO_TRADE"}:
        return metrics

    explicit_expiry = build_expired_entry_decision(stored, evaluated_at=int(evaluated_at))
    if explicit_expiry is not None:
        if "event_id" in stored:
            explicit_expiry["event_id"] = stored["event_id"]
        projected_metrics = dict(metrics)
        projected_metrics["entry_decision"] = explicit_expiry
        return projected_metrics

    # Reuse the policy persisted with this decision so runtime setting changes
    # cannot retroactively make an immutable decision appear fresh or stale.
    stored_policy = stored.get("policy")
    if isinstance(stored_policy, dict) and stored_policy:
        policy = EntryDecisionPolicy.from_settings(stored_policy)
    else:
        # Legacy packets predate an embedded immutable policy. Preserve their
        # established dashboard safety window without changing the global
        # canonical engine policy.
        policy = EntryDecisionPolicy(max_analysis_age_seconds=180.0)
    analysis_age = analysis_age_seconds if isinstance(analysis_age_seconds, (int, float)) else None
    reference_age = reference_age_seconds if isinstance(reference_age_seconds, (int, float)) else None
    freshness_expired = bool(
        analysis_age is None
        or analysis_age > policy.max_analysis_age_seconds
        or reference_age is None
        or reference_age > policy.max_reference_age_seconds
    )
    if not freshness_expired:
        return metrics

    stale_actionable = build_invalidated_entry_decision(
        stored,
        evaluated_at=int(evaluated_at),
        block_reason=(
            "STALE_ANALYSIS"
            if analysis_age is None or analysis_age > policy.max_analysis_age_seconds
            else "STALE_REFERENCE"
        ),
    )
    if stale_actionable is not None:
        if "event_id" in stored:
            stale_actionable["event_id"] = stored["event_id"]
        projected_metrics = dict(metrics)
        projected_metrics["entry_decision"] = stale_actionable
        return projected_metrics

    projected = build_entry_decision(
        metrics,
        candidate_status,
        evaluated_at=int(evaluated_at),
        analysis_age_seconds=analysis_age,
        reference_age_seconds=reference_age,
        policy=policy,
        lifecycle_id=(
            int(stored["lifecycle_id"])
            if isinstance(stored.get("lifecycle_id"), int)
            and not isinstance(stored.get("lifecycle_id"), bool)
            else None
        ),
        previous_decision=stored,
    )
    if "event_id" in stored:
        projected["event_id"] = stored["event_id"]
    projected_metrics = dict(metrics)
    projected_metrics["entry_decision"] = projected
    return projected_metrics


def get_formatted_candidates(*, evaluation_time: float | None = None):  # NOSONAR
    now = time.time() if evaluation_time is None else float(evaluation_time)
    if not math.isfinite(now) or now < 0:
        raise ValueError("evaluation_time must be a non-negative finite timestamp")

    active_from_db = (
        db.get_all_active_candidates()
    )

    historical_by_symbol = (
        historical_outcome_store.symbol_summaries()
    )
    execution_suitability_by_symbol = (
        execution_suitability_enricher.for_symbols(active_from_db.keys())
    )

    for (
        symbol,
        data,
    ) in active_from_db.items():
        live_data = (
            scanner
            .active_candidates
            .get(
                symbol,
                {},
            )
        )

        if live_data:
            (
                price,
                observed_at,
            ) = (
                scanner
                .get_live_reference(
                    symbol
                )
            )

            is_live = (
                price is not None
                and observed_at is not None
            )

            analysis_observed_at = live_data.get(
                "analysis_observed_at"
            )
            data[
                "analysis_observed_at"
            ] = analysis_observed_at
            data[
                "analysis_age_seconds"
            ] = (
                round(
                    now
                    - analysis_observed_at,
                    1,
                )
                if (
                    isinstance(analysis_observed_at, (int, float))
                    and not isinstance(analysis_observed_at, bool)
                    and 0 <= analysis_observed_at <= now
                )
                else None
            )
            data["reference_observed_at"] = observed_at
            data["reference_age_seconds"] = (
                round(now - observed_at, 1)
                if observed_at is not None and 0 <= observed_at <= now
                else None
            )

            data[
                "score"
            ] = (
                live_data.get(
                    "score"
                )
                if is_live
                else None
            )

            live_metrics = live_data.get(
                "metrics"
            )
            if isinstance(live_metrics, dict):
                live_metrics = _project_entry_decision_freshness(
                    live_metrics,
                    candidate_status=str(data.get("status") or "WATCH"),
                    evaluated_at=now,
                    analysis_age_seconds=data.get("analysis_age_seconds"),
                    reference_age_seconds=data.get("reference_age_seconds"),
                )

            if is_live:
                data["metrics"] = (
                    compact_metrics(live_metrics)
                    if isinstance(live_metrics, dict)
                    else {"analysis_reason": "live analysis pending"}
                )
            elif isinstance(live_metrics, dict) and isinstance(
                live_metrics.get("entry_decision"), dict
            ):
                unavailable_projection = {
                    "entry_decision": dict(live_metrics["entry_decision"]),
                }
                for key in ("error", "analysis_reason"):
                    if key in live_metrics:
                        unavailable_projection[key] = live_metrics[key]
                data["metrics"] = unavailable_projection
            else:
                data["metrics"] = None
            strategy_profile = (
                live_metrics.get("strategy_profile")
                if isinstance(live_metrics, dict)
                else None
            )
            data["strategy_profile"] = strategy_profile
            data["signal_class"] = {
                "strict_score_v2": "STRICT",
                "experimental_pretrigger_v1": "EXPERIMENTAL",
            }.get(strategy_profile, "UNAVAILABLE")

            data[
                "last_price"
            ] = (
                price
                if is_live
                else None
            )

            data[
                "quote_volume"
            ] = (
                live_data.get(
                    "quote_volume"
                )
                if is_live
                else None
            )

            data[
                "data_status"
            ] = (
                "live"
                if is_live
                else "unavailable"
            )

            data[
                "analysis_status"
            ] = (
                live_data.get(
                    "analysis_status",
                    (
                        "ready"
                        if data[
                            "score"
                        ]
                        is not None
                        else "pending"
                    ),
                )
                if is_live
                else "unavailable"
            )

            data[
                "dex_context"
            ] = (
                live_data.get(
                    "dex_context"
                )
                if is_live
                else None
            )

            data[
                "onchain_context"
            ] = (
                live_data.get(
                    "onchain_context"
                )
                if is_live
                else None
            )

        else:
            data[
                "last_price"
            ] = None

            data[
                "quote_volume"
            ] = None

            data["analysis_observed_at"] = None
            data["analysis_age_seconds"] = None
            data["reference_observed_at"] = None
            data["reference_age_seconds"] = None

            data[
                "score"
            ] = None

            data[
                "metrics"
            ] = None
            data["strategy_profile"] = None
            data["signal_class"] = "UNAVAILABLE"

            data[
                "data_status"
            ] = "unavailable"

            data[
                "analysis_status"
            ] = "unavailable"

            data[
                "dex_context"
            ] = None

            data[
                "onchain_context"
            ] = None

        data[
            "execution_suitability"
        ] = execution_suitability_by_symbol[symbol]

        data["historical_outcome"] = (
            historical_by_symbol.get(symbol)
            or {
                "available": False,
                "evidence_source": "historical_backfill",
                "ranking_eligible": False,
            }
        )

        data.pop(
            "trigger_data",
            None,
        )

    final_ranking = FinalRanking.rank(
        active_from_db,
        limit=3,
        evaluation_time=now,
    )

    signal_funnel = SignalFunnel.build(
        active_from_db,
        generated_at=now,
    )

    ranking_by_symbol = {
        packet["symbol"]: packet
        for packet in final_ranking["all"]
    }

    for symbol, data in active_from_db.items():
        data["final_ranking"] = ranking_by_symbol[symbol]

    sorted_candidates = {
        key: value
        for key, value
        in sorted(
            active_from_db.items(),
            key=lambda item: (
                item[1].get(
                    "score"
                )
                if item[1].get(
                    "score"
                )
                is not None
                else -1
            ),
            reverse=True,
        )
    }

    decision_terminal = build_decision_terminal(
        sorted_candidates,
        recent_changes=entry_decision_store.recent_transitions(limit=10),
    )

    return {
        "total": len(
            sorted_candidates
        ),
        "candidates": (
            sorted_candidates
        ),
        "decision_terminal": decision_terminal,
        "final_ranking": {
            key: value
            for key, value in final_ranking.items()
            if key != "all"
        },
        "signal_funnel": signal_funnel,
    }


def _get_live_dashboard_payload(*, evaluation_time: float) -> dict[str, Any]:
    """Return the bounded public projection of the canonical dashboard state."""
    return project_dashboard_payload(
        get_formatted_candidates(evaluation_time=evaluation_time)
    )


def _publish_dashboard_snapshot(
    *,
    full_snapshot: bool,
    only_if_changed: bool = False,
) -> DashboardStreamEvent | None:
    generated_at = time.time()
    payload = _get_live_dashboard_payload(evaluation_time=generated_at)
    if only_if_changed:
        return _dashboard_event_buffer.publish_snapshot_if_changed(
            payload,
            generated_at=generated_at,
        )
    return _dashboard_event_buffer.publish_snapshot(
        payload,
        generated_at=generated_at,
        full_snapshot=full_snapshot,
    )


def _new_dashboard_client_queue() -> asyncio.Queue:
    """Return the bounded latest-wins queue used by one SSE client."""
    return asyncio.Queue(maxsize=_DASHBOARD_CLIENT_QUEUE_LIMIT)


def _broadcast_dashboard_event(event: DashboardStreamEvent) -> None:
    for queue in list(_sse_clients):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass


def _publish_dashboard_snapshot_safely(
    *,
    full_snapshot: bool,
    only_if_changed: bool,
) -> DashboardStreamEvent | None:
    """Contain dashboard aggregation faults so one bad snapshot cannot kill SSE."""
    try:
        return _publish_dashboard_snapshot(
            full_snapshot=full_snapshot,
            only_if_changed=only_if_changed,
        )
    except Exception:
        logger.exception("Dashboard snapshot aggregation failed; SSE loop will continue")
        return None


def _dashboard_snapshot_broadcast_due(
    last_snapshot_at: float,
    now_monotonic: float,
) -> bool:
    """Return whether the expensive dashboard aggregation budget is due."""
    return (
        last_snapshot_at <= 0.0
        or now_monotonic - last_snapshot_at
        >= _DASHBOARD_SNAPSHOT_BROADCAST_INTERVAL_SECONDS
    )


def _dashboard_snapshot_task_result(
    task: asyncio.Task,
) -> DashboardStreamEvent | None:
    try:
        return task.result()
    except asyncio.CancelledError:
        return None
    except Exception:
        logger.exception("Dashboard snapshot worker failed; SSE loop will continue")
        return None


def _consume_dashboard_snapshot_task(
    task: asyncio.Task | None,
) -> tuple[asyncio.Task | None, float | None]:
    if task is None or not task.done():
        return task, None
    event = _dashboard_snapshot_task_result(task)
    if event is not None and _sse_clients:
        _broadcast_dashboard_event(event)
    return None, time.monotonic()


def _start_dashboard_snapshot_task() -> asyncio.Task:
    return asyncio.create_task(
        asyncio.to_thread(
            _publish_dashboard_snapshot_safely,
            full_snapshot=False,
            only_if_changed=True,
        )
    )


def _dashboard_heartbeat_due(last_heartbeat_at: float, now_monotonic: float) -> bool:
    return (
        last_heartbeat_at <= 0.0
        or now_monotonic - last_heartbeat_at
        >= _DASHBOARD_HEARTBEAT_INTERVAL_SECONDS
    )


def _publish_dashboard_heartbeat() -> None:
    heartbeat = _dashboard_event_buffer.publish_heartbeat(generated_at=time.time())
    _broadcast_dashboard_event(heartbeat)


async def sse_broadcaster():
    last_snapshot_at = 0.0
    last_heartbeat_at = 0.0
    snapshot_task: asyncio.Task | None = None
    try:
        while _hunter_running:
            now_monotonic = time.monotonic()
            snapshot_task, completed_at = _consume_dashboard_snapshot_task(snapshot_task)
            if completed_at is not None:
                last_snapshot_at = completed_at

            if _sse_clients:
                if (
                    snapshot_task is None
                    and _dashboard_snapshot_broadcast_due(last_snapshot_at, now_monotonic)
                ):
                    snapshot_task = _start_dashboard_snapshot_task()
                if _dashboard_heartbeat_due(last_heartbeat_at, now_monotonic):
                    _publish_dashboard_heartbeat()
                    last_heartbeat_at = now_monotonic

            await asyncio.sleep(1.0)
    finally:
        if snapshot_task is not None:
            snapshot_task.cancel()
            await asyncio.gather(snapshot_task, return_exceptions=True)


def _build_runtime_lifecycle_v2_evidence(
    *,
    metrics: dict,
    decision_clock_at: float,
    analysis_observed_at: int | float | None,
    reference_observed_at: int | float | None,
):
    if not math.isfinite(decision_clock_at) or decision_clock_at < 0:
        raise ValueError(
            "decision_clock_at must be a non-negative finite timestamp"
        )

    decision_at = math.ceil(decision_clock_at)

    return build_lifecycle_v2_evidence_from_metrics(
        metrics=metrics,
        decision_at=decision_at,
        analysis_observed_at=analysis_observed_at,
        reference_observed_at=reference_observed_at,
        decision_clock_at=decision_clock_at,
    )


def _apply_deterministic_entry_gate(
    symbol: str, decision_state: str, metrics: dict[str, Any]
) -> tuple[str, bool]:
    """Attach the provider-free hard gate before any potentially actionable decision."""
    if decision_state not in {"WATCH", "FUEL-RICH", "PRE-TRIGGER", "ARMED", "TRIGGERED"}:
        return decision_state, False
    vetoed, advisory = ai_veto.evaluate_deterministic(
        symbol,
        metrics.get("orderbook", {}),
        metrics.get("ticker", {}),
    )
    metrics["ai_advisory"] = advisory
    return decision_state, vetoed


async def _bootstrap_watch_source_hints() -> int:
    """Warm process-local WATCH source hints from lifecycle-fenced evidence."""
    try:
        candidates = await asyncio.to_thread(db.get_all_active_candidates)
    except Exception:
        logger.exception("Unable to read active candidates for WATCH source-hint bootstrap")
        _watch_source_hint_cache.clear()
        return 0
    lifecycle_ids: dict[str, int] = {}
    for symbol, candidate in (candidates or {}).items():
        lifecycle_id = candidate.get("lifecycle_id") if isinstance(candidate, dict) else None
        if (
            isinstance(symbol, str)
            and symbol
            and isinstance(lifecycle_id, int)
            and not isinstance(lifecycle_id, bool)
            and lifecycle_id >= 1
        ):
            lifecycle_ids[symbol] = lifecycle_id
    if not lifecycle_ids:
        _watch_source_hint_cache.clear()
        return 0
    lookback_rows = max(10_000, min(50_000, len(lifecycle_ids) * 64))
    try:
        hints = await asyncio.to_thread(
            production_evidence_recorder.latest_source_hints,
            lifecycle_ids,
            lookback_rows=lookback_rows,
        )
    except Exception:
        logger.exception("Unable to bootstrap WATCH source hints from production evidence")
        _watch_source_hint_cache.clear()
        return 0
    _watch_source_hint_cache.clear()
    _watch_source_hint_cache.update(hints if isinstance(hints, dict) else {})
    logger.info(
        "WATCH source-hint bootstrap loaded %s/%s lifecycle-fenced hints",
        len(_watch_source_hint_cache),
        len(lifecycle_ids),
    )
    return len(_watch_source_hint_cache)


def _refresh_watch_source_hint_from_full_validation(
    symbol: str,
    *,
    lifecycle_id: int,
    metrics: dict | None,
    observed_at: float,
) -> None:
    """Refresh or invalidate a WATCH hint after a canonical full validation."""
    if (
        isinstance(lifecycle_id, bool)
        or not isinstance(lifecycle_id, int)
        or lifecycle_id < 1
        or not isinstance(metrics, dict)
    ):
        _watch_source_hint_cache.pop(symbol, None)
        return
    exchange = metrics.get("exchange")
    mapped_symbol = metrics.get("mapped_symbol")
    if not isinstance(exchange, str) or not exchange.strip():
        _watch_source_hint_cache.pop(symbol, None)
        return
    if not isinstance(mapped_symbol, str) or not mapped_symbol.strip():
        _watch_source_hint_cache.pop(symbol, None)
        return
    _watch_source_hint_cache[symbol] = {
        "exchange": exchange.strip(),
        "mapped_symbol": mapped_symbol.strip(),
        "lifecycle_id": lifecycle_id,
        "full_analysis_observed_at": float(observed_at),
        "evidence_observed_at": float(observed_at),
    }


def _claim_watch_full_revalidation_slot(now: float) -> bool:
    """Bound full WATCH recertification starts to avoid restart stampedes."""
    global _watch_full_revalidation_next_allowed_at
    try:
        current_now = float(now)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(current_now) or current_now < 0.0:
        return False
    if current_now < float(_watch_full_revalidation_next_allowed_at):
        return False
    _watch_full_revalidation_next_allowed_at = (
        current_now + _WATCH_FULL_REVALIDATION_MIN_START_INTERVAL_SECONDS
    )
    return True


async def _progressive_watch_result(
    symbol: str,
    *,
    reference_price: float,
    current_state: str,
    lifecycle_id: int,
    previous_metrics: dict | None,
    now: float | None = None,
) -> dict | None:
    """Return an observational WATCH result or require canonical full validation."""
    if str(current_state or "").upper() != "WATCH":
        return None

    try:
        previous_decision = entry_decision_store.latest_for_symbol(symbol)
    except Exception:
        logger.exception("Unable to read canonical decision for WATCH prefilter %s", symbol)
        return None
    if (
        isinstance(previous_decision, dict)
        and previous_decision.get("decision") in {"ENTRY_READY", "ACTIVE"}
    ):
        return None

    metrics = previous_metrics if isinstance(previous_metrics, dict) else {}
    exchange_name = metrics.get("exchange")
    mapped_symbol = metrics.get("mapped_symbol")
    full_observed_at = metrics.get("full_analysis_observed_at")
    if not (
        isinstance(exchange_name, str) and exchange_name
        and isinstance(mapped_symbol, str) and mapped_symbol
        and isinstance(full_observed_at, (int, float))
        and not isinstance(full_observed_at, bool)
    ):
        hint = _watch_source_hint_cache.get(symbol)
        if isinstance(hint, dict) and hint.get("lifecycle_id") == lifecycle_id:
            exchange_name = hint.get("exchange")
            mapped_symbol = hint.get("mapped_symbol")
            full_observed_at = hint.get("full_analysis_observed_at")

    current_now = time.time() if now is None else float(now)
    full_age_seconds: float | None = None
    has_lifecycle_source = bool(
        isinstance(exchange_name, str)
        and exchange_name
        and isinstance(mapped_symbol, str)
        and mapped_symbol
        and isinstance(full_observed_at, (int, float))
        and not isinstance(full_observed_at, bool)
    )
    if has_lifecycle_source:
        full_age_seconds = current_now - float(full_observed_at)
        if not math.isfinite(full_age_seconds) or full_age_seconds < 0.0:
            return None

        try:
            probe = await validator.watch_prefilter(
                symbol,
                reference_price,
                exchange_name=exchange_name,
                mapped_symbol=mapped_symbol,
            )
        except Exception:
            logger.exception("WATCH previous-source prefilter failed for %s", symbol)
            return None

        if not isinstance(probe, dict):
            return None
        if probe.get("requires_full_validation") is True:
            return None
        if (
            probe.get("conclusive") is not True
            or not isinstance(probe.get("result"), dict)
        ):
            return None

        full_revalidation_due = (
            full_age_seconds > _WATCH_FULL_REVALIDATION_SECONDS
        )
        if full_revalidation_due and _claim_watch_full_revalidation_slot(current_now):
            return None

        result = dict(probe["result"])
        result_metrics = dict(result.get("metrics") or {})
        result_metrics["full_analysis_observed_at"] = float(full_observed_at)
        result_metrics["watch_prefilter"] = {
            **dict(result_metrics.get("watch_prefilter") or {}),
            "last_full_validation_age_seconds": round(
                max(0.0, full_age_seconds), 3
            ),
            "full_revalidation_budget_seconds": _WATCH_FULL_REVALIDATION_SECONDS,
            "full_revalidation_due": bool(full_revalidation_due),
            "full_revalidation_deferred": bool(full_revalidation_due),
        }
        result["metrics"] = result_metrics
        return result

    try:
        waterfall_probe = await validator.watch_prefilter_waterfall(
            symbol,
            reference_price,
        )
    except Exception:
        logger.exception("WATCH 4h waterfall prefilter failed for %s", symbol)
        return None
    if (
        not isinstance(waterfall_probe, dict)
        or waterfall_probe.get("conclusive") is not True
        or waterfall_probe.get("requires_full_validation") is True
        or not isinstance(waterfall_probe.get("result"), dict)
    ):
        return None

    if _claim_watch_full_revalidation_slot(current_now):
        return None

    result = dict(waterfall_probe["result"])
    result_metrics = dict(result.get("metrics") or {})
    result_metrics["watch_prefilter"] = {
        **dict(result_metrics.get("watch_prefilter") or {}),
        "full_revalidation_budget_seconds": _WATCH_FULL_REVALIDATION_SECONDS,
        "full_revalidation_due": True,
        "full_revalidation_deferred": True,
        "source_hint_available": False,
    }
    result["metrics"] = result_metrics
    return result


async def evaluate_candidate(
    symbol: str,
    data: dict,
):
    analysis_observed_at = int(time.time())

    active_candidate = scanner.active_candidates.get(symbol)
    if not isinstance(active_candidate, dict):
        logger.info(
            "Discarding queued evaluation outside active universe for %s",
            symbol,
        )
        return

    expected_lifecycle_id = data.get("lifecycle_id")
    active_lifecycle_id = active_candidate.get("lifecycle_id")
    if scanner.last_successful_refresh_at is not None:
        if (
            isinstance(expected_lifecycle_id, bool)
            or not isinstance(expected_lifecycle_id, int)
            or expected_lifecycle_id < 1
            or isinstance(active_lifecycle_id, bool)
            or not isinstance(active_lifecycle_id, int)
            or active_lifecycle_id != expected_lifecycle_id
        ):
            logger.info(
                "Discarding queued evaluation after lifecycle change for %s: expected=%s active=%s",
                symbol,
                expected_lifecycle_id,
                active_lifecycle_id,
            )
            return
    elif (
        isinstance(active_lifecycle_id, int)
        and not isinstance(active_lifecycle_id, bool)
        and isinstance(expected_lifecycle_id, int)
        and not isinstance(expected_lifecycle_id, bool)
        and active_lifecycle_id != expected_lifecycle_id
    ):
        logger.info(
            "Discarding queued evaluation after lifecycle change for %s: expected=%s active=%s",
            symbol,
            expected_lifecycle_id,
            active_lifecycle_id,
        )
        return

    evaluation_candidate_token = active_candidate
    previous_ws_source = _websocket_source(active_candidate.get("metrics"))
    active_candidate["analysis_status"] = "pending"
    active_candidate["analysis_observed_at"] = analysis_observed_at

    (
        lbank_price,
        reference_observed_at,
    ) = scanner.get_live_reference(
        symbol
    )
    active_candidate["reference_observed_at"] = reference_observed_at

    current_state = data[
        "status"
    ]

    execution_decision_logger.observe_evaluation(
        symbol,
        volume_gate_passed=bool(
            execution_decision_logger
            .volume_gate_passes(
                data.get(
                    "quote_volume"
                )
            )
        ),
        scan_eligible=bool(
            data.get(
                "scan_eligible"
            )
        ),
        candidate_state=str(
            current_state
        ),
        score=data.get(
            "score"
        ),
        quote_volume=data.get(
            "quote_volume"
        ),
        last_price=data.get(
            "last_price"
        ),
    )

    reference_source = "lbank"
    reference_mapped_symbol = symbol

    decision_contract = build_decision_contract(
        app_version=app.version,
        validator=validator,
        settings=settings,
        recorder_bucket_seconds=(
            production_evidence_recorder.bucket_seconds
        ),
    )
    decision_contract_hash = decision_contract_sha256(decision_contract)

    if lbank_price is None:
        fallback_reference = (
            await validator
            .resolve_live_reference(
                symbol
            )
        )

        if not _candidate_evaluation_token_is_current(
            symbol,
            evaluation_candidate_token,
        ):
            logger.info(
                "Discarding in-flight evaluation after active-universe swap for %s",
                symbol,
            )
            return

        if not fallback_reference:
            replay_policy = EntryDecisionPolicy()
            reference_failure_metrics = {
                "error": "no fresh reference price in exchange waterfall",
            }
            failure_replay_context = None

            try:
                previous_entry_decision = entry_decision_store.latest_for_symbol(symbol)
            except Exception:
                logger.exception("Unable to read canonical decision during reference failure for %s", symbol)
                previous_entry_decision = None

            if (
                isinstance(previous_entry_decision, dict)
                and previous_entry_decision.get("decision") in {"ENTRY_READY", "ACTIVE"}
            ):
                decision_now = int(time.time())
                invalidated_decision = build_entry_decision(
                    reference_failure_metrics,
                    "WATCH",
                    evaluated_at=decision_now,
                    analysis_age_seconds=causal_age_seconds(
                        decision_now,
                        analysis_observed_at,
                    ),
                    reference_age_seconds=None,
                    lifecycle_id=int(data.get("lifecycle_id") or 1),
                    previous_decision=previous_entry_decision,
                )
                invalidated_decision["research_provenance"] = (
                    _entry_outcome_research_provenance(
                        reference_failure_metrics,
                        decision_contract_hash=decision_contract_hash,
                        decision_at=decision_now,
                        reference_source=None,
                        reference_mapped_symbol=None,
                    )
                )
                try:
                    decision_event_id = entry_decision_store.append_if_changed_with_capture(
                        symbol,
                        invalidated_decision,
                        captured_at=decision_now,
                        expected_lifecycle_id=int(data.get("lifecycle_id") or 1),
                    )
                except StaleCandidateLifecycleError:
                    logger.info(
                        "Discarding stale reference-failure evaluation for %s lifecycle=%s",
                        symbol,
                        int(data.get("lifecycle_id") or 1),
                    )
                    return
                if decision_event_id is not None:
                    invalidated_decision["event_id"] = decision_event_id
                reference_failure_metrics["entry_decision"] = invalidated_decision
                failure_replay_context = build_production_replay_context(
                    lifecycle_id=int(data.get("lifecycle_id") or 1),
                    entry_decision=invalidated_decision,
                    decision_evaluated_at=int(
                        invalidated_decision.get("evaluated_at") or decision_now
                    ),
                    analysis_observed_at=analysis_observed_at,
                    reference_observed_at=None,
                    policy_version=replay_policy.version,
                    max_analysis_age_seconds=replay_policy.max_analysis_age_seconds,
                    max_reference_age_seconds=replay_policy.max_reference_age_seconds,
                    trade_plan_feasibility_shadow={
                        "version": "technical_trade_plan_shadow_v1",
                        "observational_only": True,
                        "hard_gating_allowed": False,
                        "trade_eligible": False,
                        "available": False,
                        "feasible": None,
                        "status": "UNAVAILABLE",
                        "reason": (
                            "reference failure: no fresh reference price "
                            "in exchange waterfall"
                        ),
                    },
                )
                failure_replay_context["decision_contract_sha256"] = (
                    decision_contract_hash
                )

            production_evidence_recorder.record(
                symbol,
                candidate_state=str(current_state),
                reference_source=None,
                reference_price=None,
                result={
                    "is_valid": False,
                    "score": None,
                    "suggested_status": "REJECTED",
                    "metrics": dict(reference_failure_metrics),
                },
                decision_contract=decision_contract,
                replay_context=failure_replay_context,
            )
            scanner.active_candidates.setdefault(
                symbol,
                {},
            )[
                "score"
            ] = None

            scanner.active_candidates[
                symbol
            ][
                "analysis_status"
            ] = "unavailable"

            _sync_websocket_evidence_subscription(
                previous_ws_source,
                None,
                state="WATCH",
            )

            _store_live_metrics(
                symbol,
                reference_failure_metrics,
            )

            if current_state in {
                "FUEL-RICH",
                "PRE-TRIGGER",
                "ARMED",
                "TRIGGERED",
            }:
                db.update_candidate_state(
                    symbol,
                    "WATCH",
                )

            return

        lbank_price = (
            fallback_reference[
                "price"
            ]
        )

        reference_source = (
            fallback_reference[
                "exchange"
            ]
        )

        reference_mapped_symbol = (
            fallback_reference.get(
                "mapped_symbol"
            )
        )

        live_data = (
            scanner
            .active_candidates
            .setdefault(
                symbol,
                {},
            )
        )

        live_data[
            "last_price"
        ] = lbank_price

        live_data[
            "reference_observed_at"
        ] = time.time()
        reference_observed_at = live_data[
            "reference_observed_at"
        ]

        live_data[
            "reference_source"
        ] = reference_source

        if (
            fallback_reference[
                "quote_volume"
            ]
            is not None
        ):
            live_data[
                "quote_volume"
            ] = (
                fallback_reference[
                    "quote_volume"
                ]
            )

    result = await _progressive_watch_result(
        symbol,
        reference_price=lbank_price,
        current_state=str(current_state),
        lifecycle_id=int(data.get("lifecycle_id") or 1),
        previous_metrics=active_candidate.get("metrics"),
        now=float(analysis_observed_at),
    )
    used_watch_prefilter = result is not None
    if result is None:
        result = (
            await validator
            .cross_check_symbol(
                symbol,
                lbank_price,
                reference_source=(
                    reference_source
                ),
                lifecycle_id=int(data.get("lifecycle_id") or 1),
            )
        )
        full_metrics = result.setdefault("metrics", {})
        if isinstance(full_metrics, dict):
            full_metrics["full_analysis_observed_at"] = float(analysis_observed_at)
    runtime_diagnostics = result.pop("_runtime_diagnostics", {})
    _record_adaptive_pipeline_observation(
        current_state,
        None,
        runtime_diagnostics,
    )
    if not _candidate_evaluation_token_is_current(
        symbol,
        evaluation_candidate_token,
    ):
        logger.info(
            "Discarding in-flight evaluation after active-universe swap for %s",
            symbol,
        )
        return
    if not used_watch_prefilter:
        _refresh_watch_source_hint_from_full_validation(
            symbol,
            lifecycle_id=int(data.get("lifecycle_id") or 1),
            metrics=result.get("metrics"),
            observed_at=float(analysis_observed_at),
        )
    lifecycle_v2_decision_clock_at = time.time()

    result_metrics = result.setdefault(
        "metrics",
        {},
    )
    snapshot_stages = result_metrics.get(
        "strategy_stages"
    )
    if isinstance(snapshot_stages, dict):
        result_metrics["snapshot_stage_chain"] = {
            "version": "snapshot_stage_chain_v1",
            "passed": snapshot_stages.get("passed") is True,
            "observational_only": True,
            "hard_gating_allowed": False,
        }
        if not isinstance(result_metrics.get("stage_lifecycle"), dict):
            result_metrics["stage_lifecycle"] = (
                stage_lifecycle_store.advance(
                    symbol,
                    int(data.get("lifecycle_id") or 1),
                    snapshot_stages,
                )
            )

    decision_now = int(time.time())
    decision_now_precise = time.time()

    decision_state = str(
        result.get("suggested_status")
        if result.get("suggested_status") in {"WATCH", "FUEL-RICH", "PRE-TRIGGER", "ARMED", "TRIGGERED", "EXHAUSTED", "INVALIDATED"}
        else result.get("observation_status")
        if result.get("observation_status") in {"WATCH", "FUEL-RICH", "PRE-TRIGGER", "ARMED"}
        else current_state
    )

    decision_state, deterministic_vetoed = _apply_deterministic_entry_gate(
        symbol, decision_state, result_metrics
    )

    try:
        execution_suitability = await asyncio.to_thread(
            execution_suitability_enricher.for_symbol,
            symbol,
        )
    except Exception as exc:
        logger.warning(
            "Execution suitability unavailable for leverage advisory %s: %s",
            symbol,
            type(exc).__name__,
        )
        execution_suitability = {
            "available": False,
            "status": "UNKNOWN",
            "reason": "execution suitability unavailable",
        }

    if not _candidate_evaluation_token_is_current(
        symbol,
        evaluation_candidate_token,
    ):
        logger.info(
            "Discarding in-flight evaluation after active-universe swap for %s",
            symbol,
        )
        return

    reference_age = causal_age_seconds(decision_now_precise, reference_observed_at)
    previous_entry_decision = entry_decision_store.latest_for_symbol(symbol)
    # Operator settings are read here, at decision time, so a change applies to
    # new decisions only and never rewrites one already persisted.
    entry_decision = build_entry_decision(
        result_metrics,
        decision_state,
        evaluated_at=decision_now,
        analysis_age_seconds=causal_age_seconds(decision_now_precise, analysis_observed_at),
        reference_age_seconds=reference_age,
        policy=EntryDecisionPolicy.from_settings(runtime_settings_store.current()),
        lifecycle_id=int(data.get("lifecycle_id") or 1),
        previous_decision=previous_entry_decision,
    )
    # The decision packet is attached to result_metrics only after the
    # leverage advisory is built, so expose readiness explicitly here rather
    # than relying on metrics["entry_decision"] being present yet.
    result_metrics["entry_readiness"] = entry_decision.get("entry_readiness")
    leverage_advisory = _apply_signal_leverage_advisory(
        result_metrics,
        execution_suitability,
        decision_status=str(entry_decision.get("decision") or "UNAVAILABLE"),
    )
    leverage_projection = project_leverage_advisory(result_metrics)
    if leverage_projection is not None:
        entry_decision["leverage_advisory"] = leverage_projection
        trade_plan = entry_decision.get("trade_plan")
        if isinstance(trade_plan, dict):
            trade_plan["leverage"] = leverage_projection.get("leverage")
    entry_decision["research_provenance"] = _entry_outcome_research_provenance(
        result_metrics,
        decision_contract_hash=decision_contract_hash,
        decision_at=decision_now,
        reference_source=reference_source,
        reference_mapped_symbol=reference_mapped_symbol,
    )
    try:
        event_id = entry_decision_store.append_if_changed_with_capture(
            symbol,
            entry_decision,
            captured_at=decision_now,
            expected_lifecycle_id=int(data.get("lifecycle_id") or 1),
        )
    except StaleCandidateLifecycleError:
        logger.info(
            "Discarding stale canonical evaluation for %s lifecycle=%s",
            symbol,
            int(data.get("lifecycle_id") or 1),
        )
        return
    entry_decision["event_persisted"] = event_id is not None
    if event_id is not None:
        entry_decision["event_id"] = event_id
    else:
        persisted_decision = entry_decision_store.latest_for_symbol(symbol)
        _restore_persisted_decision_projection(
            entry_decision,
            result_metrics,
            persisted_decision,
        )
    result_metrics["entry_decision"] = entry_decision

    # Fundamental capture is deliberately post-decision and observational. Do
    # not spend free-provider quota on the entire watch universe; capture only
    # candidates close enough to a signal that their later outcome is useful.
    if event_id is not None and entry_decision.get("decision") in {"FORMING", "ENTRY_READY"}:
        _start_background_task(
            _refresh_observational_fundamental(symbol, data, int(event_id))
        )

    # ── Per-signal observational outcome: open on ENTRY_READY, settle all open ones ──
    if entry_decision.get("decision") == "ENTRY_READY":
        _start_backtest_trade(symbol, result_metrics, entry_decision)
    # Settle against every live candidate, not only the symbol just evaluated:
    # a position opened on a symbol that later stops being scheduled would
    # otherwise never reach its stop or target.
    _check_backtest_trades(scanner.active_candidates)

    try:
        technical_trade_plan_shadow = validator.build_technical_trade_plan_shadow(
            result_metrics
        )
    except Exception as exc:
        logger.warning(
            "Technical TradePlan shadow unavailable for %s: %s",
            symbol,
            type(exc).__name__,
        )
        technical_trade_plan_shadow = {
            "version": "technical_trade_plan_shadow_v1",
            "observational_only": True,
            "hard_gating_allowed": False,
            "trade_eligible": False,
            "available": False,
            "feasible": None,
            "status": "UNAVAILABLE",
            "reason": type(exc).__name__,
        }
    replay_policy = EntryDecisionPolicy()
    replay_context = build_production_replay_context(
        lifecycle_id=int(data.get("lifecycle_id") or 1),
        entry_decision=entry_decision,
        decision_evaluated_at=int(entry_decision.get("evaluated_at") or decision_now),
        decision_clock_at=decision_now_precise,
        analysis_observed_at=analysis_observed_at,
        reference_observed_at=(
            float(reference_observed_at)
            if isinstance(reference_observed_at, (int, float))
            and not isinstance(reference_observed_at, bool)
            else None
        ),
        policy_version=replay_policy.version,
        max_analysis_age_seconds=replay_policy.max_analysis_age_seconds,
        max_reference_age_seconds=replay_policy.max_reference_age_seconds,
        trade_plan_feasibility_shadow=technical_trade_plan_shadow,
    )
    replay_context["decision_contract_sha256"] = decision_contract_hash

    # FORMING is evaluated frequently (184 events in 30 minutes during the
    # incident that exposed this issue). Sending every one to the advisory
    # provider starved the queue: even ENTRY_READY advisories then timed out.
    # AI is a second opinion for a canonical actionable signal, not a
    # polling-time classifier.
    if event_id is not None and entry_decision.get("decision") == "ENTRY_READY":
        _start_background_task(
            _refresh_canonical_ai_advisory(
                symbol, int(event_id), dict(result_metrics), dict(entry_decision)
            )
        )

    episode_id = f"{symbol}:{int(data.get('lifecycle_id') or 1)}"

    def persist_lifecycle_v2_shadow(final_v1_state: str) -> None:
        try:
            v2_from_state = lifecycle_v2_shadow_store.latest_state(
                symbol=symbol,
                episode_id=episode_id,
            )
            v2_evidence = _build_runtime_lifecycle_v2_evidence(
                metrics=result_metrics,
                decision_clock_at=lifecycle_v2_decision_clock_at,
                analysis_observed_at=analysis_observed_at,
                reference_observed_at=(
                    float(reference_observed_at)
                    if isinstance(reference_observed_at, (int, float))
                    and not isinstance(reference_observed_at, bool)
                    else None
                ),
            )
            v2_transition = evaluate_lifecycle_v2_shadow(
                episode_id=episode_id,
                current_state=v2_from_state,
                evidence=v2_evidence,
            )
            v2_comparison = compare_v1_v2_shadow(
                episode_id=episode_id,
                v1_state=final_v1_state,
                v2_state=v2_from_state,
                evidence=v2_evidence,
            )
            persisted = lifecycle_v2_shadow_store.append_comparison(
                symbol=symbol,
                v1_state=final_v1_state,
                transition=v2_transition,
                comparison=v2_comparison,
                created_at=analysis_observed_at,
            )
            result_metrics["lifecycle_v2_shadow"] = {
                "transition": v2_transition.model_dump(mode="json"),
                "comparison": v2_comparison,
                "evidence_available": v2_evidence.eligible_data,
                "unavailable_fields": list(v2_evidence.unavailable_fields),
                "event_persisted": persisted,
                "v1_state_mutated": False,
            }
        except (LifecycleV2ShadowStoreError, ValueError) as exc:
            logger.exception("Lifecycle V2 shadow unavailable for %s: %s", symbol, exc)
            result_metrics["lifecycle_v2_shadow"] = {
                "shadow_only": True,
                "promotion_allowed": False,
                "available": False,
                "reason": type(exc).__name__,
                "v1_state_mutated": False,
            }

    def record_final_production_decision(
        path: str,
        reason: str,
        **details,
    ) -> None:
        result_metrics = result.setdefault(
            "metrics",
            {},
        )
        result_metrics[
            "production_decision"
        ] = {
            "final": True,
            "path": str(path),
            "reason": str(reason),
            "recorded_after_persistence": (
                path == "TRIGGERED"
            ),
            **details,
        }
        production_evidence_recorder.record(
            symbol,
            candidate_state=str(current_state),
            reference_source=reference_source,
            reference_price=lbank_price,
            result=result,
            decision_contract=decision_contract,
            replay_context=replay_context,
        )

    production_evidence_recorder.record(
        symbol,
        candidate_state=str(current_state),
        reference_source=reference_source,
        reference_price=lbank_price,
        result=result,
        decision_contract=decision_contract,
        replay_context=replay_context,
    )

    _record_derivative_packet_outcome(
        result.get(
            "metrics"
        )
    )

    if not result[
        "is_valid"
    ]:
        final_v1_shadow_state = str(current_state)
        stored_metrics = (
            result.get(
                "metrics"
            )
            or {
                "error": (
                    "live validation unavailable"
                )
            }
        )

        stored_metrics[
            "analysis_reason"
        ] = str(
            stored_metrics.get(
                "analysis_reason"
            )
            or stored_metrics.get(
                "error"
            )
            or "live validation unavailable"
        )

        observation_status = (
            result.get(
                "observation_status"
            )
        )

        observation_score = (
            result.get(
                "observation_score"
            )
        )

        if observation_status in {
            "WATCH",
            "FUEL-RICH",
            "PRE-TRIGGER",
        }:
            live = (
                scanner
                .active_candidates
                .setdefault(
                    symbol,
                    {},
                )
            )

            live[
                "score"
            ] = observation_score

            live[
                "analysis_status"
            ] = "observed"

            stored_metrics[
                "observation_status"
            ] = observation_status

            stored_metrics[
                "observation_score"
            ] = observation_score

            stored_metrics[
                "trade_eligible"
            ] = False

            observation_state_aligned = (
                current_state == observation_status
            )

            if (
                current_state
                != observation_status
            ):
                observation_state_persisted = db.update_candidate_state(
                    symbol,
                    observation_status,
                )
                if not observation_state_persisted:
                    logger.error(
                        "Candidate observation state "
                        "persistence failed for %s -> %s",
                        symbol,
                        observation_status,
                    )
                else:
                    final_v1_shadow_state = str(observation_status)
                    observation_state_aligned = True

            observation_exchange = (
                stored_metrics.get(
                    "exchange"
                )
            )

            observation_symbol = (
                stored_metrics.get(
                    "mapped_symbol"
                )
            )

            if observation_state_aligned:
                observation_ws_source = (
                    (observation_exchange, observation_symbol)
                    if observation_exchange and observation_symbol
                    else None
                )
                _sync_websocket_evidence_subscription(
                    previous_ws_source,
                    observation_ws_source,
                    state=str(observation_status),
                )

        else:
            scanner.active_candidates.setdefault(
                symbol,
                {},
            )[
                "score"
            ] = None

            scanner.active_candidates[
                symbol
            ][
                "analysis_status"
            ] = "unavailable"

            watch_state_persisted = False

            if current_state in {
                "FUEL-RICH",
                "PRE-TRIGGER",
                "ARMED",
                "TRIGGERED",
            }:
                watch_state_persisted = db.update_candidate_state(
                    symbol,
                    "WATCH",
                )
                if watch_state_persisted:
                    final_v1_shadow_state = "WATCH"

            if watch_state_persisted:
                _sync_websocket_evidence_subscription(
                    previous_ws_source,
                    _websocket_source(stored_metrics),
                    state="WATCH",
                )

        if data.get(
            "dex_context"
        ):
            stored_metrics[
                "dex_context"
            ] = data[
                "dex_context"
            ]

        if data.get(
            "onchain_context"
        ):
            stored_metrics[
                "onchain_context"
            ] = data[
                "onchain_context"
            ]

        _store_live_metrics(
            symbol,
            stored_metrics,
        )

        persist_lifecycle_v2_shadow(final_v1_shadow_state)

        return

    score = result[
        "score"
    ]

    new_state = result[
        "suggested_status"
    ]

    metrics = result[
        "metrics"
    ]

    if data.get(
        "dex_context"
    ):
        metrics[
            "dex_context"
        ] = data[
            "dex_context"
        ]

    if data.get(
        "onchain_context"
    ):
        metrics[
            "onchain_context"
        ] = data[
            "onchain_context"
        ]

    ex_name = metrics.get(
        "exchange"
    )

    mapped_sym = metrics.get(
        "mapped_symbol"
    )

    if (
        symbol
        not in scanner.active_candidates
    ):
        scanner.active_candidates[
            symbol
        ] = {}

    scanner.active_candidates[
        symbol
    ][
        "score"
    ] = score

    scanner.active_candidates[
        symbol
    ][
        "analysis_status"
    ] = "ready"

    _store_live_metrics(
        symbol,
        metrics,
    )

    observational_states = {
        "WATCH",
        "FUEL-RICH",
        "PRE-TRIGGER",
        "ARMED",
    }

    if (
        new_state
        in observational_states
    ):
        if (
            current_state
            != new_state
        ):
            if not db.update_candidate_state(
                symbol,
                new_state,
            ):
                logger.error(
                    "Candidate state persistence "
                    "failed for %s -> %s",
                    symbol,
                    new_state,
                )
                persist_lifecycle_v2_shadow(str(current_state))
                return

        current_ws_source = _websocket_source(metrics)
        _sync_websocket_evidence_subscription(
            previous_ws_source,
            current_ws_source,
            state=str(new_state),
        )

        persist_lifecycle_v2_shadow(str(new_state))

        return

    if (
        new_state == "TRIGGERED"
        and current_state == "TRIGGERED"
    ):
        persist_lifecycle_v2_shadow("TRIGGERED")
        record_final_production_decision(
            "STALE_TRIGGER_SUPPRESSED",
            "candidate was already persisted as TRIGGERED",
        )

        _store_live_metrics(
            symbol,
            metrics,
        )

        _retire_websocket_evidence_source((ex_name, mapped_sym))

        return

    if new_state == "TRIGGERED":
        # Deterministic market-data veto is part of the canonical hard-gate
        # path and was already evaluated before the entry decision event was
        # persisted. AI output remains advisory-only and cannot mutate it.
        if deterministic_vetoed:
            state_persisted = db.update_candidate_state(
                symbol,
                "WATCH",
            )

            record_final_production_decision(
                "DETERMINISTIC_VETOED",
                (
                    "deterministic market-data veto rejected "
                    "the validated trigger candidate"
                ),
                state_persisted=bool(state_persisted),
                veto_source="deterministic_market_data",
                llm_decision_critical=False,
            )
            persist_lifecycle_v2_shadow(
                "WATCH" if state_persisted else str(current_state)
            )

            if state_persisted:
                _retire_websocket_evidence_source((ex_name, mapped_sym))

            _store_live_metrics(
                symbol,
                metrics,
            )

            _schedule_ai_advisory_observational(
                symbol,
                analysis_observed_at=analysis_observed_at,
                orderbook=metrics.get("orderbook"),
                ticker=metrics.get("ticker"),
            )
            return


        if leverage_advisory.get("status") != "AVAILABLE":
            logger.info(
                "Leverage advisory for %s is %s: %s",
                symbol,
                leverage_advisory.get("status"),
                leverage_advisory.get("reason"),
            )

        quote_volume = data.get(
            "quote_volume"
        )
        volume_gate_passed = (
            execution_decision_logger
            .volume_gate_passes(
                quote_volume
            )
        )
        proxy_execution_disagreement = (
            execution_decision_logger
            .comparison_kind(
                volume_gate_passed,
                str(
                    execution_suitability.get(
                        "status"
                    )
                    or "UNKNOWN"
                ),
            )
        )

        metadata_reference_observed_at = (
            int(reference_observed_at)
            if (
                isinstance(reference_observed_at, (int, float))
                and not isinstance(reference_observed_at, bool)
                and reference_observed_at >= 0
            )
            else None
        )
        try:
            signal_metadata = build_signal_metadata_input(
                {
                    **metrics,
                    "analysis_observed_at": analysis_observed_at,
                    "reference_observed_at": metadata_reference_observed_at,
                },
                decision_contract_hash,
            )
        except ValueError as exc:
            persist_lifecycle_v2_shadow(str(current_state))
            record_final_production_decision(
                "METADATA_REJECTED",
                "explicit signal metadata validation failed",
                error_type=type(exc).__name__,
            )
            logger.exception(
                "Signal metadata validation failed for %s: %s",
                symbol,
                exc,
            )
            return

        signal_id = signal_ledger.persist_trigger(
            symbol,
            current_state,
            score=score,
            trigger_metrics=metrics,
            execution_suitability=(
                execution_suitability
            ),
            metadata=signal_metadata,
            quote_volume=quote_volume,
            volume_gate_passed=volume_gate_passed,
            proxy_execution_disagreement=(
                proxy_execution_disagreement
            ),
        )

        if signal_id is None:
            persist_lifecycle_v2_shadow(str(current_state))
            record_final_production_decision(
                "PERSISTENCE_REJECTED",
                "signal persistence rejected or failed",
            )

            logger.error(
                "Signal persistence rejected or failed for %s; "
                "stale Telegram alert suppressed",
                symbol,
            )
            return

        experimental_profile = not _signal_alert_allowed(metrics)

        persist_lifecycle_v2_shadow("TRIGGERED")
        record_final_production_decision(
            "TRIGGERED",
            (
                "experimental trigger persisted to the immutable signal ledger"
                if experimental_profile
                else "validated trigger persisted to the immutable signal ledger"
            ),
            signal_id=int(signal_id),
        )

        logger.warning(
            "🔥 [%s] TRIGGERED - Score: %s/100 "
            "(signal_id=%s)",
            symbol,
            score,
            signal_id,
        )

        if _signal_alert_allowed(metrics):
            logger.info(
                "STRICT TRIGGERED event %s persisted without proactive Telegram; "
                "delivery is reserved for canonical ENTRY_READY and FORMING transitions",
                signal_id,
            )
        else:
            logger.warning(
                "Experimental signal %s persisted without Telegram delivery",
                signal_id,
            )

        _retire_websocket_evidence_source((ex_name, mapped_sym))

        _store_live_metrics(
            symbol,
            metrics,
        )

        _schedule_ai_advisory_observational(
            symbol,
            analysis_observed_at=(
                analysis_observed_at
            ),
            orderbook=metrics.get(
                "orderbook"
            ),
            ticker=metrics.get(
                "ticker"
            ),
        )

        return


async def hunter_loop(
    interval_seconds: float = 60.0,
):
    global _hunter_running
    global _hunter_last_completed_at
    global _hunter_last_progress_at
    global _hunter_in_flight_count
    global _hunter_due_backlog

    _hunter_running = True

    await asyncio.sleep(
        _HUNTER_STARTUP_DELAY_SECONDS
    )

    logger.info(
        "🛡️ [SYSTEM] Engine Online: "
        "State Machine running."
    )

    loop = asyncio.get_running_loop()
    maintenance_interval = max(0.01, float(interval_seconds))
    schedule = HunterDeadlineSchedule()
    candidates: dict[str, dict] = {}
    in_flight: dict[str, asyncio.Task] = {}
    evaluations_since_flush = 0
    flush_requested = False
    flush_task: asyncio.Task | None = None
    next_maintenance_at = 0.0
    last_heap_trim_at = 0.0

    async def evaluate_scheduled(symbol: str, data: dict) -> None:
        global _hunter_last_progress_at
        nonlocal evaluations_since_flush
        nonlocal flush_requested
        evaluation_started_at = loop.time()

        try:
            if not _hunter_running:
                return
            await evaluate_candidate(symbol, data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Candidate evaluation failed: %s",
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        else:
            _hunter_last_progress_at = time.time()
        finally:
            _record_adaptive_pipeline_observation(
                data.get("status"),
                max(0.0, loop.time() - evaluation_started_at),
                None,
            )
            evaluations_since_flush += 1
            if evaluations_since_flush >= 30:
                evaluations_since_flush = 0
                flush_requested = True

    try:
        while _hunter_running:
            now = loop.time()
            maintenance_finalize = False

            if now >= next_maintenance_at:
                try:
                    expired_count = await asyncio.to_thread(
                        _reconcile_explicit_entry_expirations,
                        evaluated_at=int(time.time()),
                    )
                    if expired_count:
                        logger.info(
                            "Reconciled %s explicit canonical entry expirations",
                            expired_count,
                        )

                    candidates = await asyncio.to_thread(
                        db.get_all_active_candidates
                    )

                    inactive_count = await asyncio.to_thread(
                        _reconcile_inactive_actionable_decisions,
                        active_symbols=set(candidates),
                        evaluated_at=int(time.time()),
                    )
                    if inactive_count:
                        logger.info(
                            "Invalidated %s canonical entries outside the active universe",
                            inactive_count,
                        )

                    schedule.sync(candidates, now=loop.time())
                    maintenance_finalize = True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("⚠️ Hunter maintenance error: %s", exc)

                next_maintenance_at = loop.time() + maintenance_interval

            for symbol, task in tuple(in_flight.items()):
                if not task.done():
                    continue
                in_flight.pop(symbol, None)
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass

            available_slots = max(
                0,
                DEFAULT_EVALUATION_CONCURRENCY - len(in_flight),
            )
            if available_slots and candidates:
                schedule.sync(candidates, now=loop.time())
                due = schedule.due_candidates(
                    candidates,
                    scanner.active_candidates,
                    now=loop.time(),
                    in_flight=set(in_flight),
                    limit=available_slots,
                )
                for symbol, data in due:
                    schedule.mark_started(
                        symbol,
                        data.get("status"),
                        now=loop.time(),
                    )
                    in_flight[symbol] = asyncio.create_task(
                        evaluate_scheduled(symbol, data)
                    )

            _hunter_in_flight_count = len(in_flight)
            if candidates:
                remaining_due = schedule.due_candidates(
                    candidates,
                    scanner.active_candidates,
                    now=loop.time(),
                    in_flight=set(in_flight),
                    limit=max(1, len(candidates)),
                )
                _hunter_due_backlog = len(remaining_due)
            else:
                _hunter_due_backlog = 0

            if maintenance_finalize:
                # Let newly dispatched evaluations enter their await boundary before
                # maintenance I/O. Maintenance never consumes an evaluation slot.
                await asyncio.sleep(0)
                await asyncio.to_thread(
                    execution_decision_logger.record_universe_snapshot
                )
                validator.ws_manager.prune_stale_cache()

                if not candidates and not in_flight:
                    _hunter_last_progress_at = time.time()

                _hunter_last_completed_at = time.time()

                if loop.time() - last_heap_trim_at >= 300.0:
                    trim_result = await asyncio.to_thread(trim_process_heap)
                    last_heap_trim_at = loop.time()
                    if trim_result.get("malloc_trim_released"):
                        logger.debug(
                            "Hunter maintenance heap trim released free pages (gc=%s)",
                            trim_result.get("gc_collected"),
                        )

            if flush_requested and (flush_task is None or flush_task.done()):
                if flush_task is not None:
                    await asyncio.gather(flush_task, return_exceptions=True)
                flush_requested = False
                flush_task = asyncio.create_task(
                    asyncio.to_thread(execution_decision_logger.flush_evaluations)
                )

            if not _hunter_running:
                break

            # Newly dispatched tasks should run before the scheduler decides how
            # long it can sleep. This also lets very fast tasks free slots now.
            await asyncio.sleep(0)

            for symbol, task in tuple(in_flight.items()):
                if task.done():
                    in_flight.pop(symbol, None)
                    if not task.cancelled():
                        task.result()

            _hunter_in_flight_count = len(in_flight)
            if candidates:
                remaining_due = schedule.due_candidates(
                    candidates,
                    scanner.active_candidates,
                    now=loop.time(),
                    in_flight=set(in_flight),
                    limit=max(1, len(candidates)),
                )
                _hunter_due_backlog = len(remaining_due)
            else:
                _hunter_due_backlog = 0

            available_slots = max(
                0, DEFAULT_EVALUATION_CONCURRENCY - len(in_flight)
            )
            now = loop.time()
            delays = [max(0.0, next_maintenance_at - now)]
            if available_slots > 0:
                due_delay = schedule.seconds_until_next_due(
                    candidates,
                    now=now,
                    in_flight=set(in_flight),
                )
                if due_delay is not None:
                    delays.append(due_delay)
            wait_timeout = max(0.001, min(delays)) if delays else maintenance_interval

            stop_wait = asyncio.create_task(_hunter_stop_event.wait())
            wait_set = [stop_wait, *in_flight.values()]
            try:
                await asyncio.wait(
                    wait_set,
                    timeout=wait_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                if not stop_wait.done():
                    stop_wait.cancel()
                await asyncio.gather(stop_wait, return_exceptions=True)

    except asyncio.CancelledError:
        raise
    finally:
        if in_flight:
            await asyncio.gather(*in_flight.values(), return_exceptions=True)
        if flush_task is not None:
            await asyncio.gather(flush_task, return_exceptions=True)
        await asyncio.to_thread(execution_decision_logger.flush_evaluations)
        _hunter_in_flight_count = 0
        _hunter_due_backlog = 0


async def live_reference_loop(
    interval_seconds: int = 30,
):
    while True:
        try:
            await (
                scanner
                .refresh_live_references()
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            logger.warning(
                "Live reference refresh failed: %s",
                exc,
            )

        await asyncio.sleep(
            interval_seconds
        )


async def startup_event():
    global _hunter_task
    global _hunter_stop_event
    global _lbank_execution_shadow_worker
    global _signal_settlement_worker
    global _entry_outcome_resolution_worker
    global _entry_notification_worker
    global _entry_notification_probe

    _hunter_stop_event = asyncio.Event()

    if settings.live_trading_enabled:
        raise RuntimeError(
            "LIVE_TRADING_ENABLED must remain "
            "false for WaterfallHunter"
        )

    require_managed_schema(
        db.db_path,
        check_user_version=CURRENT_RUNTIME_SCHEMA_VERSION,
    )
    require_signal_metadata_completeness(db.db_path)

    await _bootstrap_watch_source_hints()

    _start_background_task(
        scanner.start_background_scanner(
            _CATALOG_REFRESH_INTERVAL_SECONDS
        )
    )

    _start_background_task(
        live_reference_loop()
    )

    _hunter_task = _start_background_task(
        hunter_loop(
            interval_seconds=60
        )
    )

    _start_background_task(
        feature_replay_worker.run_forever(
            interval_seconds=60.0,
        )
    )
    logger.info(
        "Observational feature-equivalent replay enabled "
        "(batch=3 interval=60s)"
    )

    _start_background_task(
        sse_broadcaster()
    )

    # Legacy bot disabled to avoid HTTP 409 conflict with enhanced bot
    # _start_background_task(
    #     notifier.start_interactive_bot()
    # )

    # Enhanced Telegram bot with /signals, /health, /top, /help and 12h health reports
    try:
        from waterfallhunter.core.telegram_enhanced import start_enhanced_bot
        _start_background_task(start_enhanced_bot(
            db_adapter=db,
            scanner=scanner,
            delegate=notifier,
        ))
        logger.info("Enhanced Telegram bot started with health reports and new commands.")
    except Exception as exc:
        logger.warning("Enhanced Telegram bot failed to start: %s", exc)

    _entry_notification_worker = _build_entry_notification_worker()
    if _entry_notification_worker is not None:
        transport = _entry_notification_worker.transport
        _entry_notification_probe = await transport.probe()
        if not _telegram_probe_allows_worker(_entry_notification_probe):
            logger.error(
                "Telegram bot/chat probe was permanently rejected; canonical delivery not started "
                "(bot=%s chat=%s status=%s).",
                _entry_notification_probe.get("bot_reachable"),
                _entry_notification_probe.get("chat_reachable"),
                _entry_notification_probe.get("status_code"),
            )
            _entry_notification_worker = None
        else:
            if _entry_notification_probe.get("reachable") is not True:
                logger.warning(
                    "Telegram startup probe was transiently unavailable; durable ENTRY_READY "
                    "delivery remains active and will retry through the outbox worker "
                    "(status=%s).",
                    _entry_notification_probe.get("status_code"),
                )
            _start_background_task(_entry_notification_loop())
            logger.info("Canonical ENTRY_READY Telegram delivery enabled.")
    else:
        _entry_notification_probe = {
            "configured": bool(notifier.enabled),
            "reachable": False,
            "bot_reachable": False,
            "chat_reachable": False,
            "status_code": None,
        }
        logger.warning(
            "Canonical Telegram delivery disabled: release gate/cutover is not active."
        )

    _lbank_execution_shadow_worker = (
        _build_lbank_execution_shadow_worker()
    )

    if (
        _lbank_execution_shadow_worker
        is not None
    ):
        logger.info(
            "LBank execution shadow enabled: "
            "batch=%s interval=%ss",
            (
                _lbank_execution_shadow_worker
                .batch_size
            ),
            settings.lbank_execution_shadow_interval_seconds,
        )

        _start_background_task(
            _lbank_execution_shadow_worker.run_forever(
                interval_seconds=(
                    settings
                    .lbank_execution_shadow_interval_seconds
                ),
            )
        )

    else:
        logger.info(
            "LBank execution shadow disabled."
        )

    _signal_settlement_worker = (
        _build_signal_settlement_worker()
    )
    _start_background_task(
        _signal_settlement_worker
        .run_forever(
            interval_seconds=900.0,
        )
    )
    logger.info(
        "Observational 24h signal settlement enabled "
        "(batch=3 interval=900s)"
    )

    _entry_outcome_resolution_worker = _build_entry_outcome_resolution_worker()
    _start_background_task(_entry_outcome_resolution_worker.run_forever())
    logger.info(
        "Entry outcome resolution enabled "
        "(provider_batch=36 local_batch=300 interval=900s)"
    )


async def shutdown_event():
    global _hunter_running
    global _hunter_task
    global _lbank_execution_shadow_worker
    global _signal_settlement_worker
    global _entry_outcome_resolution_worker
    global _entry_notification_worker

    _hunter_running = False
    _hunter_stop_event.set()

    feature_replay_worker.stop()

    scanner.stop()

    if (
        _lbank_execution_shadow_worker
        is not None
    ):
        _lbank_execution_shadow_worker.stop()

    if _signal_settlement_worker is not None:
        _signal_settlement_worker.stop()
    if _entry_outcome_resolution_worker is not None:
        _entry_outcome_resolution_worker.stop()

    hunter_task = _hunter_task
    if (
        hunter_task is not None
        and not hunter_task.done()
    ):
        try:
            await asyncio.wait_for(
                asyncio.shield(hunter_task),
                timeout=_HUNTER_SHUTDOWN_GRACE_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "Hunter shutdown drain timed out; "
                "cancelling remaining task"
            )

    tasks = list(
        _background_tasks
    )

    for task in tasks:
        task.cancel()

    if tasks:
        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

    if (
        _lbank_execution_shadow_worker
        is not None
    ):
        await (
            _lbank_execution_shadow_worker
            .close()
        )

        _lbank_execution_shadow_worker = None

    _signal_settlement_worker = None
    _entry_outcome_resolution_worker = None
    _hunter_task = None

    await scanner.close()
    await validator.close_all()


def _readiness_snapshot() -> tuple[
    bool,
    dict,
]:
    """
    Build the operational readiness packet.

    Shadow execution observation is intentionally informational only and does
    not gate readiness. Catalogue freshness and hunter progress remain the
    authoritative readiness requirements used by the existing /api/health
    contract.
    """

    now = time.time()

    catalog_age = (
        None
        if scanner.last_successful_refresh_at
        is None
        else (
            now
            - scanner.last_successful_refresh_at
        )
    )

    hunter_age = (
        None
        if _hunter_last_progress_at
        is None
        else (
            now
            - _hunter_last_progress_at
        )
    )

    catalog_fresh = (
        catalog_age is not None
        and catalog_age <= 25_200
    )

    hunter_fresh = (
        hunter_age is not None
        and hunter_age <= 180
    )

    ready = (
        catalog_fresh
        and hunter_fresh
    )

    if not ready:
        return (
            False,
            {
                "status": "degraded",
                "catalog_age_seconds": (
                    catalog_age
                ),
                "hunter_progress_age_seconds": (
                    hunter_age
                ),
            },
        )

    return (
        True,
        {
            "status": "healthy",
            "tracked": len(
                scanner.active_candidates
            ),
            "catalog_age_seconds": round(
                catalog_age,
                1,
            ),
            "hunter_progress_age_seconds": round(
                hunter_age,
                1,
            ),
            "lbank_execution_shadow": (
                _lbank_shadow_health_snapshot()
            ),
        },
    )


@app.get(
    "/api/health"
)
async def health_check():
    ready, payload = (
        _readiness_snapshot()
    )

    if not ready:
        raise HTTPException(
            status_code=503,
            detail=payload,
        )

    return payload


@app.get(
    "/livez"
)
async def liveness_check():
    return {
        "status": "alive",
    }


@app.get(
    "/readyz"
)
async def readiness_check():
    return await health_check()


@app.get(
    "/healthz"
)
async def healthz_check():
    return await health_check()


async def _notification_delivery_health_snapshot() -> dict:
    report = await asyncio.to_thread(
        notification_delivery_health,
        settings.registry_db_path,
        now=int(time.time()),
        outbox_table="entry_notification_outbox",
    )
    report["transport"] = {
        "provider": "telegram",
        "configured": bool(notifier.enabled),
        "worker_running": _entry_notification_worker is not None,
        "probe": _entry_notification_probe,
    }
    counts = report["counts"]
    for state in (
        "PENDING",
        "SENDING",
        "DELIVERED",
        "RETRY_WAIT",
        "DEAD_LETTER",
        "DELIVERY_UNCERTAIN",
    ):
        notification_delivery_state.labels(state=state).set(
            int(counts.get(state, 0))
        )
    age = report.get("oldest_pending_age_seconds")
    notification_oldest_pending_age.set(
        float(age) if age is not None else float("nan")
    )
    return report


@app.get(
    "/api/notification-delivery",
    responses={503: {"description": "Notification delivery state is unavailable"}},
)
async def notification_delivery_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _notification_delivery_health_snapshot()
    except NotificationDeliveryError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": str(exc)},
        ) from exc


@app.get(
    "/metrics"
)
async def metrics():
    active_candidates = await asyncio.to_thread(
        db.get_all_active_candidates
    )
    tracked_candidates.set(
        len(active_candidates)
    )
    _update_candidate_state_metrics(
        active_candidates
    )

    _update_websocket_metrics()
    _update_adaptive_pipeline_metrics()

    if (
        scanner.last_successful_refresh_at
        is not None
    ):
        catalog_last_refresh.set(
            scanner.last_successful_refresh_at
        )

    if (
        _hunter_last_completed_at
        is not None
    ):
        hunter_last_cycle.set(
            _hunter_last_completed_at
        )

    if (
        _hunter_last_progress_at
        is not None
    ):
        hunter_last_progress.set(
            _hunter_last_progress_at
        )

    _update_lbank_shadow_metrics()
    _update_signal_settlement_worker_metrics()
    try:
        await _update_signal_evidence_metrics()
    except Exception:
        logger.exception("Signal evidence metrics are unavailable")
    try:
        await _notification_delivery_health_snapshot()
    except NotificationDeliveryError:
        logger.exception("Notification delivery metrics are unavailable")

    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get(
    "/api/stream",
)
async def stream_candidates(
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID"),
    ] = None,
):
    q = _new_dashboard_client_queue()

    _sse_clients.add(
        q
    )

    async def event_generator():
        delivered_event_id = 0
        try:
            replay = _dashboard_event_buffer.replay_after(last_event_id)
            if replay is None:
                full_snapshot = await asyncio.to_thread(
                    _publish_dashboard_snapshot_safely,
                    full_snapshot=True,
                    only_if_changed=False,
                )
                if full_snapshot is None:
                    replay = []
                else:
                    queued: list[DashboardStreamEvent] = []
                    while True:
                        try:
                            queued.append(q.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                    replay = sorted(
                        {
                            int(event.event_id): event
                            for event in (*queued, full_snapshot)
                        }.values(),
                        key=lambda event: int(event.event_id),
                    )
            for event in replay:
                delivered_event_id = max(delivered_event_id, int(event.event_id))
                yield serialize_sse_event(event)

            while True:
                event = await q.get()
                if int(event.event_id) <= delivered_event_id:
                    continue
                delivered_event_id = int(event.event_id)
                yield serialize_sse_event(event)

        finally:
            _sse_clients.discard(
                q
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _get_dashboard_poll_snapshot() -> DashboardSnapshot:
    global _dashboard_preview_cache, _dashboard_poll_build_task

    latest = _dashboard_event_buffer.latest_snapshot()
    if (
        latest is not None
        and time.time() - latest.generated_at <= _DASHBOARD_PREVIEW_CACHE_SECONDS
    ):
        return latest

    now_monotonic = time.monotonic()
    cached = _dashboard_preview_cache
    if cached is not None:
        cached_buffer, cached_snapshot, cached_at = cached
        if (
            cached_buffer is _dashboard_event_buffer
            and now_monotonic - cached_at <= _DASHBOARD_PREVIEW_CACHE_SECONDS
        ):
            return cached_snapshot

    task = _dashboard_poll_build_task
    if task is None or task.done():
        generated_at = time.time()

        async def build() -> tuple[float, dict[str, Any]]:
            payload = await asyncio.to_thread(
                _get_live_dashboard_payload, evaluation_time=generated_at
            )
            return generated_at, payload

        task = asyncio.create_task(build())
        _dashboard_poll_build_task = task

    try:
        generated_at, payload = await asyncio.shield(task)
    finally:
        if task.done() and _dashboard_poll_build_task is task:
            _dashboard_poll_build_task = None

    snapshot = _dashboard_event_buffer.preview_snapshot(
        payload, generated_at=generated_at
    )
    _dashboard_preview_cache = (
        _dashboard_event_buffer,
        snapshot,
        time.monotonic(),
    )
    return snapshot


@app.get(
    "/api/candidates",
    response_model=DashboardSnapshot,
    response_model_exclude_none=False,
)
async def get_candidates(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await _get_dashboard_poll_snapshot()


@app.get(
    "/api/candidates/raw",
    response_model=DashboardSnapshot,
    response_model_exclude_none=False,
)
async def get_raw_candidates(response: Response):
    """Load full diagnostics on demand; never push them through the live SSE path."""
    response.headers["Cache-Control"] = "no-store"
    generated_at = time.time()
    raw_payload = await asyncio.to_thread(
        get_formatted_candidates, evaluation_time=generated_at
    )
    return _dashboard_event_buffer.preview_snapshot(
        raw_payload,
        generated_at=generated_at,
    )


# ─── New API Routes ───

@app.get("/api/backtest/results")
async def backtest_results():
    """Live outcome-tracking results — $100 capital, 30% per position, 4-18x isolated."""
    try:
        from waterfallhunter.core.backtester_v2 import BacktesterV2
        bt = BacktesterV2(db_path=_BACKTEST_DB_PATH)
        metrics = bt.compute_metrics()
        equity = bt.get_equity_curve()
        trades = bt.get_trade_history(limit=20)
        mistakes = bt.get_top_mistakes(limit=10)
        return {
            "stats": metrics,
            "equity": equity,
            "trades": trades,
            "mistakes": mistakes,
        }
    except Exception as exc:
        return {"error": str(exc), "stats": {}, "equity": [], "trades": [], "mistakes": []}
@app.get("/api/fundamental")
async def fundamental_score_endpoint(symbol: str = ""):
    """Fundamental score endpoint."""
    try:
        result = await fundamental_scorer.score(symbol)
        return result
    except Exception as exc:
        return {"error": str(exc), "fundamental_score": 0, "confidence": 0}
