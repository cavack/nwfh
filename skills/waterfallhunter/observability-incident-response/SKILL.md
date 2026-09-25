---
name: observability-incident-response
description: Use when WaterfallHunter needs structured logs, Prometheus metrics, Grafana or Alertmanager coverage, Sentry tracing, release correlation, SLI/SLOs, runtime incident analysis, root-cause timelines, or postmortem actions.
---

# WFH Observability & Incident Response

## Overview

Make WaterfallHunter failures detectable, diagnosable, and closable with evidence. Recovery is not root-cause closure.

## When to Use

Use for production/runtime incidents, unexplained restarts, OOM/latency/provider failures, structured logging, Prometheus metrics, Grafana/Alertmanager, Sentry, correlation IDs, release tagging, SLI/SLO design, or postmortems.

## Scope

Own incident evidence/timeline, telemetry gaps, structured logs, metrics, traces, release correlation, RSS/memory slope, worker/provider health, SLO/alert design, and closure criteria.

## Protected Invariants

Unless a separately authorized and validated strategy or policy change explicitly requires otherwise, do not incidentally change ScoreV2 weights or evidence semantics, lifecycle transitions, strict/experimental eligibility boundaries, anti-chase behavior, signal provenance or immutable-ledger semantics, persistence-before-notification ordering, scientific holdout/walk-forward rules, or production execution policy.

Current repository policy is observational and does not place orders. Live order placement is outside this skill system: this skill must not authorize, design, implement, or enable live order placement. Any future execution capability requires a separately reviewed safety design and repository-policy change before ordinary release gates apply.

## Input Contract

Receive the concrete task, current repository SHA/branch, affected semantic boundary, relevant source-of-truth paths, and known runtime or external-evidence context for this domain.

## Required Evidence

Read the current canonical implementation/contracts/tests for this domain plus relevant current PR/issue/runtime evidence. Historical reports remain context until revalidated.

## Tool Preference

Use the smallest authorized capability set that establishes the needed facts. Prefer repository/runtime evidence over secondary summaries; record unavailable optional tools instead of guessing.

## Output Contract

Report material findings using the shared evidence taxonomy, identify the owning file/semantic boundary, state blast radius and remaining unknowns, and give a precise verification or handoff requirement.

## Stop and Escalation Conditions

Stop rather than guess when a required source of truth, artifact identity, or safety-critical prerequisite cannot be established. Escalate protected-invariant changes to their owning strategy/scientific skills and production-readiness authority to `release-production-certification`.

## Workflow

1. Preserve timestamps, release SHA, logs, metrics, restart/container events, and relevant request/provider context.
2. Build a concise incident timeline and distinguish symptom, trigger, contributing factors, root cause, and unknown hypotheses.
3. Apply/record containment without calling it root-cause correction.
4. Route code diagnosis to the owning runtime/backend/frontend specialist.
5. Add telemetry that measures the failure mode directly: latency, in-flight work, queue depth, snapshot bytes, RSS slope, worker last-success, provider freshness, or similar.
6. Require regression coverage for the root cause when practical.
7. Define operational verification and an SLO/alert follow-up before closing the incident, or document an explicit waiver.

## Evidence and Readiness

An incident is not closed merely because a restart restored service. Closure requires detection evidence, root-cause disposition, mitigation/fix disposition, regression coverage disposition, and operational verification disposition.

## Verification

Confirm telemetry is emitted and queryable, alerts target actionable failure modes, release SHA/correlation can link events, post-fix runtime remains stable through an appropriate soak, and the original failure would now be detected earlier.

## Handoffs

OOM/concurrency/performance → `runtime-reliability-performance`. Backend data/task architecture → `backend-data-architecture`. UI incident → `frontend-dashboard-ux`. Final deploy/production state → `release-production-certification`.

## Common Mistakes

- Closing an incident after a restart.
- Alerting only on “backend down” while missing degradation signals.
- Logging unstructured blobs without symbol/request/release context.
- Adding dashboards without actionable thresholds.
- Treating low current RSS as proof a memory leak is gone.
