---
name: repository-architecture-auditor
description: Use when reviewing WaterfallHunter repository structure, module boundaries, ownership, dependency coupling, stale findings, dead or duplicate code, process-local state, or architectural debt.
---

# WFH Repository & Architecture Auditor

## Overview

Map the current WaterfallHunter codebase and distinguish architectural debt from demonstrated correctness defects. The output is an evidence-backed repository map and bounded improvement proposal, not a generic refactor wishlist.

## When to Use

Use for deep repository reviews, oversized modules, unclear ownership, duplicate or legacy paths, global/process-local state, dependency tangles, stale issue cleanup, or refactor preparation.

## Scope

Inspect current SHA, relevant recent commits/PRs, repository tree, entry points, lifecycle ownership, background workers, persistence boundaries, frontend/backend contracts, tests, deployment files, and source-of-truth docs relevant to the request.

Classify findings with the shared taxonomy. Complexity, size, and coupling are normally `DEBT` until a concrete failure is reproduced or directly demonstrated.

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

1. Establish current branch/SHA and whether previous audit claims still apply.
2. Build a responsibility map: modules, dependencies, state ownership, I/O boundaries, and long-lived processes.
3. Identify duplicated responsibilities, dead/legacy paths, implicit globals, and unstable interfaces.
4. For each finding, state evidence, classification, impact, and blast radius.
5. Reproduce suspected defects when practical; do not promote structural smell to defect by rhetoric.
6. Propose the smallest boundary changes that improve the requested area; avoid unrelated cleanup.
7. Hand implementation to the domain owner and verification to `verification-regression`.

## Evidence and Readiness

A repository audit may reach `ANALYSIS_COMPLETE`. It may recommend `CODE_READY` work only after a specific design/change has been implemented and verified by the appropriate specialist. Architecture review alone does not establish production readiness.

## Verification

Re-open the current versions of cited files, verify the audit is based on the intended SHA, check that every defect claim has reproducing/direct evidence, and ensure recommendations do not silently change model or execution semantics.

## Handoffs

- Backend/service/data boundary changes → `backend-data-architecture`.
- Contract boundaries → `api-contract-schema-guardian`.
- Runtime resource/race findings → `runtime-reliability-performance`.
- Frontend architecture → `frontend-dashboard-ux`.
- Security concerns → `security-supply-chain`.

## Common Mistakes

- Equating a large `main.py` with a proven bug.
- Recommending a repository-wide rewrite because local boundaries are imperfect.
- Auditing a stale SHA.
- Calling unused-looking code dead without checking runtime/import/test references.
- Mixing strategy simplification into architecture cleanup.
