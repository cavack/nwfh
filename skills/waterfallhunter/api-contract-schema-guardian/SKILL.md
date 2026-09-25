---
name: api-contract-schema-guardian
description: Use when WaterfallHunter API, Pydantic, OpenAPI, SSE, polling, dashboard payloads, generated TypeScript, contract versions, schema versions, or unavailable/partial semantics may drift across consumers.
---

# WFH API Contract & Schema Guardian

## Overview

Keep WaterfallHunter's backend, stream/poll transports, generated types, and frontend consumers aligned through canonical versioned contracts instead of ad-hoc compatibility patches.

## When to Use

Use for request/response/event changes, nested dashboard fields, Pydantic models, OpenAPI, generated TypeScript, SSE/poll parity, schema/contract versions, or PASS/FAIL/UNAVAILABLE/partial semantics.

## Scope

Own canonical API and event semantics, nested typing for core product data, compatibility analysis, versioning decisions, generated consumer types, and transport parity. Do not let `Record<string, unknown>` become a permanent excuse for schema drift.

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

1. Identify the canonical producer and every consumer of the field/event being changed.
2. Read current Pydantic/OpenAPI/event definitions and generated/runtime frontend validation.
3. Decide whether the change is backward compatible, requires a deprecation bridge, or requires a contract/schema version change.
4. Model important nested structures explicitly; keep unknown extension bags only where they are intentionally extensible.
5. Keep SSE and polling representations semantically equivalent unless a documented transport difference exists.
6. Generate/update TypeScript from the canonical schema when the project tooling supports it; avoid hand-maintained divergent copies.
7. Add contract tests that fail if producer and consumer drift.

## Evidence and Readiness

A consumer-only patch that tolerates a renamed canonical field is normally `DEBT` or a temporary migration bridge, not the final contract fix. Contract work reaches `CODE_READY` only when producer, version semantics, generated types, and affected consumers are verified together.

## Verification

Check valid and invalid payloads, nested fields, extra-field policy, version markers, serialization of unavailable/partial data, SSE/poll parity, generated artifact freshness, and frontend type/runtime checks.

## Handoffs

- Backend model implementation → `backend-data-architecture` where architecture is involved.
- UI consumption → `frontend-dashboard-ux`.
- Strategy meaning embedded in fields → `strategy-score-lifecycle`.
- Cross-layer regression → `verification-regression`.

## Common Mistakes

- Renaming backend data and patching React only.
- Leaving core nested payloads permanently untyped.
- Treating missing evidence as false/failed because a field is absent.
- Versioning every cosmetic change or failing to version a semantic break.
- Testing polling while forgetting SSE, or the reverse.
