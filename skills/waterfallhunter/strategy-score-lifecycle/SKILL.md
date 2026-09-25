---
name: strategy-score-lifecycle
description: Use when WaterfallHunter ScoreV2, Watch Score, evidence coverage, FinalRanking, lifecycle states, anti-chase, trigger geometry, regime logic, leverage semantics, or signal eligibility may change.
---

# WFH Strategy, Score & Lifecycle Validator

## Overview

Protect the internal coherence of WaterfallHunter decision logic. Treat threshold, weight, eligibility, lifecycle, ranking, anti-chase, and leverage changes as model/policy work rather than incidental implementation tweaks.

## When to Use

Use for ScoreV2, Watch Score/coverage, FinalRanking, WATCH/FUEL-RICH/PRE-TRIGGER/ARMED/TRIGGERED/EXHAUSTED/INVALIDATED semantics, anti-chase, regime/relative weakness, trigger geometry, leverage recommendations, or signal eligibility.

## Scope

Classify semantic changes explicitly as `MODEL_AFFECTING` and/or `POLICY_AFFECTING`. Preserve the distinction between unavailable evidence, failed evidence, and directional evidence. Logic review establishes coherence, not profitability.

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

1. Resolve current authoritative contracts, score/lifecycle code, tests, and research documentation.
2. State the hypothesis behind the proposed change and which decision boundary it alters.
3. Trace its effect through evidence completeness, score/coverage, lifecycle transitions, ranking, anti-chase, leverage, signal classification, persistence, and downstream consumers.
4. Refuse to hide a threshold/weight change inside UI, reliability, refactor, or cleanup work.
5. Add deterministic tests for boundary behavior, invariants, and missing/unavailable evidence.
6. Hand any claim of improved edge or promotion readiness to `scientific-backtest-validation`; no changed parameter is promoted from logic inspection alone.

## Evidence and Readiness

A coherent strategy change may become `CODE_READY` but is not scientifically promoted without independent validation. Use `PROPOSAL` for untested threshold ideas and `REPRODUCED_DEFECT` only for demonstrated logic violations.

## Verification

Check deterministic boundary cases, score ranges/coverage, transition causality, anti-chase vetoes, leverage/risk constraints, metadata/provenance, golden corpus or equivalent regression artifacts, and absence of unintended eligibility expansion.

## Handoffs

Input-data semantics → `market-data-evidence-quality`. Promotion/edge claims → `scientific-backtest-validation`. API representation → `api-contract-schema-guardian`. Full regression → `verification-regression`.

## Common Mistakes

- Lowering thresholds merely to increase signal count.
- Calling Watch Score a calibrated probability.
- Treating unavailable data as bearish/bullish evidence.
- Changing ranking/eligibility through a frontend patch.
- Claiming higher expected profit because the logic “looks better.”
