# WaterfallHunter

Professional real-time crypto signal intelligence system with multi-source cascade verification, AI advisory (TypeSafe/Jev), and backtesting.

## Architecture

```
LBank API → Catalog (149 symbols) → Multi-Source Scanner → Cascade Intelligence
    → Entry Decision Engine → AI Advisory (TypeSafe/Jev) → Telegram + Dashboard
```

## Key Components

| Component | Description |
|-----------|-------------|
| Multi-Source Scanner | Real-time scanning of the active LBank futures catalogue (refreshed every 15 minutes) |
| Cascade Intelligence | A secondary flow/liquidity confirmation: trade flow (3), derivatives (3), liquidity (2), liquidation flow (2). PASS needs at least 4 available points and 50% of those points. It overlaps with primary order-flow, derivatives and execution evidence, so it is a configurable confirmation gate, not independent proof. |
| Entry Decision Engine | Produces ENTRY_READY / FORMING / LATE / NO_TRADE decisions |
| AI Advisory | TypeSafe System One (Jev) — observational only, no veto power |
| Outcome recorder | Opens on every canonical ENTRY_READY; settles against live LBank prices at stop, targets, or a 24h timeout. It charges round-trip fees and is not a validated performance claim. |
| Risk Manager | Dynamic 4x–18x **isolated** leverage advisory based on canonical readiness, stop distance, ATR, friction and execution suitability |
| Telegram Bot | Signal alerts + /signals, /health, /top, /help commands |

## Decision Levels

| Level | Score | Cascade | Description |
|-------|-------|---------|-------------|
| ENTRY_READY | ≥70 | PASS | Ready to enter — Telegram alert sent |
| FORMING | >= 55 and < 70 | PASS | Forming — visible on dashboard |
| NO_TRADE | <55 or FAIL | — | Conditions not met |
| LATE | — | — | Signal too late — do not chase |
| INVALIDATED | — | — | Structure broken |

## Decision Model

The engine is an additive, bounded 100-point readiness score — not the
weighted-average formula that older versions of this README described:

| Evidence | Maximum | Notes |
|----------|--------:|-------|
| 4h structure | 20 | Hype context, lower high, failed pullback, bearish close, volume acceleration |
| 1h / 15m / 5m timing | 15 | 5 points for each confirming lower timeframe |
| Order flow | 20 | Taker ratio, sell-flow imbalance, footprint and microstructure approval |
| Derivatives | 15 | Funding, OI, top-trader ratio and taker-ratio change |
| Execution | 10 | Approval, measured spread/slippage and depth |
| Cross-exchange | 5 | Breakdown confirmation from a second venue |
| Price location | 5 | Relative to VWAP |
| Cascade | 10 | Secondary confirmation; see the overlap caveat above |

`fundamental_scorer.py` is currently an informational endpoint and has **zero
weight** in the live decision. The AI advisory is observational and has **zero
weight**; only the deterministic order-book veto can hard-block. Neither is
represented as score points until a replay/walk-forward study proves a
contribution.

The current policy is operator-adjustable from the protected dashboard. Every
change applies to new signals only and is recorded with its prior value. The
shipped defaults are ENTRY_READY >= 70, FORMING >= 55, 55% evidence coverage,
2.5 ATR anti-chase, 600s analysis freshness and 60s reference freshness.

Anti-Chase applies only after readiness classification and does not turn sub-`FORMING` evidence into `LATE`; `late_origin` records whether a terminal `LATE` outcome came from anti-chase or lifecycle exhaustion.

## Observational Research

The engine collects two kinds of evidence without allowing them to alter a
signal: free Fundamental data and decision/outcome calibration data.

- **Fundamental** runs only for `FORMING` and `ENTRY_READY` candidates, uses
  free DexScreener and CoinGecko sources only, caches for five minutes, and is
  limited to two concurrent requests. It is persisted against the immutable
  decision event for later outcome analysis. It has zero score weight and zero
  gate authority until a replay/walk-forward/holdout study supports one.
- **OI / taker / cascade** thresholds are not changed from an aggregate chart.
  `scripts/regime_gate_analysis.py` requires a bootstrap confidence interval
  and chronological train/holdout agreement before it calls a relationship
  promising. It currently identifies OI >= -0.13% as promising, taker flow as
  inconclusive, and cascade as unanalysable in the legacy ledger because its
  state was not historically captured. Those are research hypotheses, not live
  gate changes.

There is deliberately no performance table here. Six observed outcomes are not a
statistical result. Live outcomes and outcome-tracking metrics are visible in the
protected dashboard, and performance claims require a recorded replay,
walk-forward and holdout protocol.

## AI Configuration

- **Provider**: TypeSafe System One (Jev) — `POST https://api.typesafe.ai/v1/systemone`
- **Model**: `jev-latest` alias; pin a versioned id such as `jev-1.13.0` to freeze behaviour
- **Timeout**: 30s (concurrency bounded at 2)
- **Key**: `TYPESAFE_API_KEY` — create one at https://console.typesafe.ai/keys
- **Judgements**: one batched call per ENTRY_READY signal — a `noul` ("does the
  evidence support the short?") and a `score` ("how strong is it?"). The verdict
  and the note are derived from those typed answers, not parsed from prose.

## Quick Start

### Prerequisites
- Docker & Docker Compose
- A TypeSafe API key (https://console.typesafe.ai/keys)

### Deployment

```bash
# Clone
git clone https://github.com/cavack/WaterfallHunter.git
cd WaterfallHunter

# Configure environment
cp .env.example .env
# Edit .env with your Telegram token, TypeSafe API key, etc.

# Build and start
docker-compose up -d --build

# Or use the Makefile
make up
```

### Environment Variables

See `.env.example` for all required variables:
- `TELEGRAM_TOKEN` — Telegram bot token
- `TELEGRAM_CHAT_ID` — Telegram chat ID
- `TYPESAFE_API_KEY` — TypeSafe API key (advisory is skipped when unset)
- `TYPESAFE_MODEL` — TypeSafe model name or alias (default: jev-latest)
- `COINGLASS_API_KEY` — Coinglass API key
- `BACKTESTER_INITIAL_CAPITAL` — Backtest capital (default: 100)
- `BACKTESTER_MAX_LEVERAGE` — Max leverage (default: 14)
- `BACKTESTER_MAX_EXPOSURE_PCT` — Max exposure % (default: 30)
- `BACKTESTER_MAX_POSITIONS` — Max simultaneous positions (default: 3)

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | System health check |
| `/api/candidates` | GET | Candidate list with signals |
| `/api/recent-signals` | GET | Recent signals |
| `/api/backtest/results` | GET | Backtester V2 results |
| `/api/ai-advisory?symbol=X` | GET | AI advisory for a symbol |
| `/api/fundamental?symbol=X` | GET | Fundamental score |

## Database

SQLite database with key tables:
- `lbank_signal_ledger` — 3,383 real signals with entry/SL/TP
- `lbank_signal_outcomes` — 2,573 real outcomes
- `entry_decision_events` — Decision records
- `bt_v2_trades` — Backtest trades
- `bt_v2_equity_curve` — Backtest equity curve

## Docker Services

| Container | Port | Description |
|-----------|------|-------------|
| waterfall-backend | 8000 (internal) | API + Scanner + Decision Engine |
| waterfall-frontend | 3000 | Next.js Dashboard |
| waterfall-watchdog | — | Health monitoring |
| waterfall-prometheus | 9090 | Metrics |
| waterfall-grafana | 3001 | Visualization |
| waterfallhunter-alertmanager | 9093 | Alert routing |

## License

See [LICENSE](LICENSE).
