# Council Tool / MCP Capability Matrix

The Council treats tools as capabilities discovered at run time. Credentials are never stored in this directory. A missing optional capability is `UNAVAILABLE`, not evidence that should be guessed.

## Required local capabilities

### `git`
Used for exact SHA/branch/worktree/diff history and clean-artifact verification.

Typical commands:

```bash
git fetch --prune origin
git rev-parse HEAD
git status --short --branch
git diff --check
git worktree list --porcelain
```

### `python`
Used for deterministic Council validation, research-artifact inspection and repository tests. Prefer the locked project environment for backend verification.

```bash
python scripts/wfh_council.py validate --json
python scripts/wfh_council.py doctor --json
PYTHONPATH="$PWD:$PWD/backend/src" python -m pytest -q
```

## Optional connected capabilities

### `github_connector`
Resolve current repo/main SHA, commits, PRs, issues, checks and reviews. Never use stale audit metadata when current GitHub state is available.
### `remote_desktop_commander_mcp`
Read/write isolated worktrees, run commands, inspect Docker/runtime evidence and analyze local research artifacts on the authorized host. Production mutation still follows release certification.

### `web_research`
Use current official exchange documentation and high-quality market-microstructure/backtest research. Record source/date and convert every imported idea into a falsifiable local hypothesis.

### `coderabbit`
Independent code-review capability when the CLI/account is authenticated. It may report issues but never owns strategy or production-promotion decisions.

### `mermaid`
Optional diagrams for architecture, decision flow, experiment lineage and release evidence. Diagram source must preserve signal-only safety semantics.

## Runtime and verification capabilities

### `docker`
Inspect/build the existing container stack and collect exact image/revision/resource evidence. Do not infer deployment state from a worktree alone.

### `prometheus`
Primary runtime metric source for analysis freshness, memory/RSS, evaluation latency, workers, provider failures and signal evidence. Grafana/Alertmanager are presentation/alert layers over observable facts.


### `grafana`
Presentation/query surface for Prometheus-backed runtime evidence; never the sole source of truth.

### `alertmanager`
Alert-routing capability for SLO/runtime incidents; alert state must be reconciled with current metrics/logs.

### `pytest`
Focused and repository-level regression execution in the project environment.

### `market_data_connectors`
Optional read-only exchange/market-data adapters. Each connector requires explicit identity, timestamp, freshness, units and historical-reproducibility checks.

### `playwright`
Browser/E2E verification of Decision Terminal, SSE/poll fallback, mobile behavior and user-visible contracts.

### `codeql`
Static security analysis in GitHub CI. Treat results as one security layer, not proof of absence of vulnerabilities.

### `sonar`
Static quality/security review when the repository integration reports results for the exact head.

## Optional market/research connectors

Binance/CCXT/public market connectors may be used for research only when the `market_evidence_forensics` owner proves contract identity, units, timestamps and freshness. CoinGecko/CoinMarketCap/LunarCrush-style metadata or attention sources are never hard bearish evidence by themselves.

An MCP server is not trusted because it is convenient. Each adapter must document read/write scope, credentials boundary, rate limits, timestamp semantics, retry behavior and whether evidence is reproducible historically.

## Local supporting commands

```bash
python scripts/validate_wfh_skills.py
python scripts/verify_repository_hygiene.py
pip-audit -r backend/requirements.txt
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend run test:e2e
docker stats --no-stream
```

Exact commands may differ with lockfile/CI policy; the owning canonical skill and current repository workflow are authoritative.

## Prohibited shortcuts

- No connector may place a live order through Council v1.
- No tool may expose/store credentials in artifacts or prompts.
- No web source may overwrite repository/runtime facts without local reproduction.
- No CodeRabbit/Sonar/CodeQL result substitutes for focused regression and runtime verification.
- No CI success is called `PRODUCTION_VERIFIED` without release certification.

## Council v2 capability and authorization model

Council v2 separates **presence** from **authorization**. Local executables may be `AVAILABLE`; connected capabilities may be `AUTHORIZED_READ` or `AUTHORIZED_WRITE` only when the active environment exposes that authorization. Otherwise they are `UNAVAILABLE` or `BLOCKED`.

The manifest's `production_mutation=false` is mandatory for every capability record. Production mutation remains a release workflow decision and is never inherited from an MCP/plugin's technical write surface.

External evidence precedence for engineering work is:

1. current exact repository object for repository claims;
2. current runtime/host evidence for runtime/deployment claims;
3. official external documentation for external API/protocol contracts;
4. peer-reviewed/high-quality research for falsifiable hypotheses;
5. secondary summaries only as navigation/context.

Current MCP guidance is treated as an external protocol contract, not as a WaterfallHunter domain rule. Authorization must be explicit, and missing capability/authorization is reported rather than guessed.
