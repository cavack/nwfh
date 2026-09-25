---
name: verification-regression
description: Use when verifying a WaterfallHunter bugfix, feature, refactor, contract change, model change, frontend change, concurrency fix, or release candidate before claiming completion.
---

# WFH Verification & Regression Engineer

## Overview

Turn a change claim into exact, blast-radius-proportional evidence. A targeted GREEN test proves one invariant, not the whole system.

## When to Use

Use whenever implementation is about to be called fixed, complete, safe, merge-ready, or regression-free.

## Scope

Own the verification matrix: unit, property-based, repository/DB integration, API/contract, concurrency/race, frontend unit/component, Playwright E2E, accessibility, screenshot/visual regression, performance, memory/soak, deterministic replay, container/runtime parity, and exact-artifact identity as relevant.

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

1. Identify the exact changed SHA/artifact and list changed files plus semantic blast radius.
2. For reproduced defects, preserve RED evidence and ensure the regression exercises the original failure.
3. Run the narrow test that proves the intended invariant.
4. Expand to neighboring integration/contract/concurrency/browser paths according to blast radius.
5. Run repository-level gates that the project relies on for the changed area.
6. Check negative/error/unavailable cases, not only happy paths.
7. Re-read final diff and ensure the verified artifact is the artifact being claimed.
8. Report passed checks, failed checks, skipped checks, and remaining unknowns separately.

## Evidence and Readiness

One targeted test cannot establish whole-change readiness. `CODE_READY` requires the proportional verification matrix to pass on the exact changed artifact. Merge/deployment/production states belong to release certification.

## Verification

The verification report itself must include commands/checks, exact SHA, result counts/statuses, environment distinctions, and any check that could not be run. Do not replace failed evidence with assumptions.

## Handoffs

Domain-specific failures return to the owning specialist. CI/review/migration/deployment gates → `release-production-certification`. Runtime soak/telemetry interpretation → `runtime-reliability-performance` or `observability-incident-response`.

## Common Mistakes

- `one targeted test passed => change complete`.
- Testing a previous commit instead of current head.
- Running unit tests but skipping affected contracts/browser/concurrency behavior.
- Ignoring skipped or environment-specific failures.
- Claiming “no regressions” without a defined regression scope.
