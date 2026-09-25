---
name: frontend-dashboard-ux
description: Use when changing WaterfallHunter Next.js or React behavior, dashboard information hierarchy, mobile or responsive UI, SSE/poll UX, accessibility, RTL/i18n foundations, rendering performance, or visual regression coverage.
---

# WFH Frontend Engineering & Dashboard UX

## Overview

Build a responsive, evidence-first dashboard that consumes canonical backend semantics rather than reimplementing trading/ranking logic in React.

## When to Use

Use for Next.js/React changes, dashboard layout, state management, SSE/poll connection UX, mobile behavior, information hierarchy, accessibility, RTL/i18n foundations, rendering/network performance, or visual regression.

Declare one or more modes: `ENGINEERING`, `UX`, `ACCESSIBILITY`, `PERFORMANCE`.

## Scope

Own frontend state/rendering, interaction and information architecture, responsive/mobile behavior, accessibility semantics, client transport behavior, locale/layout foundations, and browser performance. Backend ranking, eligibility, scoring, and evidence meaning remain canonical server concerns.

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

1. Resolve the current frontend contract and the actual user task/state that is confusing or failing.
2. Separate transport state (`connecting`, stream open, polling, offline) from data freshness (`none`, fresh, stale) when connection semantics matter.
3. Prefer normalized state and selector-level updates over rerendering large full snapshots when data volume warrants it.
4. Consume canonical backend rank/eligibility/display fields; do not copy Score/FinalRanking formulas into React as the default fix.
5. Design information hierarchy around what matters now, why, and evidence quality; move deep research/ops detail behind appropriate sections rather than deleting evidence.
6. Validate mobile/touch, keyboard/focus, semantic markup/ARIA, reduced motion, and loading/empty/error/stale states.
7. Measure parse/render/network/GC cost for realtime paths and add visual/E2E coverage proportional to impact.

## Evidence and Readiness

Visual preference is `PROPOSAL` unless tied to explicit user requirements or usability evidence. UI correctness defects should be reproduced. Frontend code is not `CODE_READY` until type/build and relevant browser tests pass.

## Verification

Check typecheck/build, component/unit behavior where present, Playwright user flows, SSE fallback/reconnect states, responsive breakpoints, accessibility, screenshot regression when visual behavior changes, and that business logic was not duplicated client-side.

## Handoffs

Payload/semantic changes → `api-contract-schema-guardian`. Runtime stream/backpressure problems → `runtime-reliability-performance`. Model/ranking semantics → `strategy-score-lifecycle`. Wider verification → `verification-regression`.

## Common Mistakes

- Copying backend ranking logic into React.
- Showing “live” merely because the socket opened before valid data arrived.
- Hiding unavailable evidence instead of explaining it.
- Solving desktop layout while breaking mobile/touch.
- Adding state libraries without a demonstrated state/update problem.
