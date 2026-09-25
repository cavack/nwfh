---
name: backend-data-architecture
description: Use when changing WaterfallHunter FastAPI structure, lifespan or dependencies, background-worker ownership, SQLite access, transactions, migrations, repositories, rollups, or scale-readiness architecture.
---

# WFH Backend & Data Architecture Engineer

## Overview

Improve backend/data boundaries only where they solve a current correctness, reliability, testability, or measured scale problem. Preserve simple single-runtime architecture when it remains sufficient.

## When to Use

Use for FastAPI app structure, application factory/lifespan, dependency ownership, service/repository/adapter decomposition, background workers, managed SQLite, transactions, migrations, rollups/precomputation, or horizontal-scale design.

## Scope

Own application construction, dependency injection seams, lifecycle/task supervision, persistence/data-access patterns, transaction ownership, migration design, worker coordination boundaries, and scale-readiness architecture.

Do not migrate SQLite to PostgreSQL merely because PostgreSQL is more conventional. Require a measured concurrency, coordination, durability, or operational need.

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

1. Resolve current runtime topology: process count, writer ownership, persistent volumes, background workers, and process-local state.
2. Identify the concrete architectural pressure and classify it as defect, debt, or measured scale constraint.
3. Design the smallest boundary change that addresses that pressure: app factory, service/repository split, explicit dependencies, bounded queries, rollup tables, transaction scopes, or worker ownership.
4. Preserve existing domain semantics and fail-closed behavior.
5. For database migrations, define schema compatibility, migration ordering, rollback/restore path, backup preflight, and mixed-version assumptions.
6. Add focused tests for the boundary being changed and hand wider verification to `verification-regression`.
7. Route production migration/deployment authority to `release-production-certification`.

## Evidence and Readiness

A proposed PostgreSQL/event-bus move is `PROPOSAL` until operational measurements justify it. Architecture code can be `CODE_READY` with tests, but production migration readiness is outside this skill.

## Verification

Check import/startup behavior, transaction commit/rollback semantics, task shutdown, DB locking/busy behavior, migration idempotence or documented one-way behavior, data integrity, and runtime parity on the exact changed artifact.

## Handoffs

- Runtime bottlenecks/races → `runtime-reliability-performance`.
- API/Pydantic changes → `api-contract-schema-guardian`.
- Migration/deployment execution → `release-production-certification`.
- Security of DB/network/container → `security-supply-chain`.

## Common Mistakes

- Architecture-by-fashion.
- Adding Redis/PostgreSQL/NATS before identifying the coordination problem.
- Splitting modules without clarifying ownership.
- Running schema changes without backup/rollback thinking.
- Refactoring domain/model semantics while moving infrastructure boundaries.
