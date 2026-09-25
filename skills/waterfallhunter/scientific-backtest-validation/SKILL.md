---
name: scientific-backtest-validation
description: Use when evaluating WaterfallHunter strategy evidence, backtests, walk-forward results, holdout performance, leakage risk, parameter stability, regime robustness, uncertainty, or promotion readiness.
---

# WFH Scientific Backtest & Model Validation

## Overview

Evaluate whether WaterfallHunter strategy changes have robust out-of-sample evidence. Protect time ordering and untouched evaluation data from selection pressure.

## When to Use

Use for backtests, replay equivalence, walk-forward, holdout, calibration, leakage, embargo, bootstrap uncertainty, regime robustness, concentration, sensitivity, parameter search, or promotion decisions.

## Scope

Own point-in-time correctness, train/development/calibration/holdout separation, embargo, walk-forward protocol, block/bootstrap uncertainty, regime stratification, concentration guards, PF/EV/MDD/MAE/MFE, sensitivity, parameter stability, multiple-testing/selection bias, and promotion evidence.

The holdout is not an iterative tuning set.

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

1. Establish data provenance, timestamp semantics, survivorship/universe rules, and feature availability at decision time.
2. Reconstruct the full model-selection history. If the reported holdout influenced prior parameter choices, treat that holdout as contaminated and require a new untouched evaluation set or future forward evidence.
3. Define development/calibration/walk-forward windows and embargo before looking at final evaluation results.
4. Evaluate returns including realistic costs/slippage assumptions and report PF, EV, drawdown, win rate, MAE/MFE, sample count, and concentration together.
5. Quantify uncertainty with appropriate bootstrap/block methods and check regime/symbol concentration.
6. Run parameter-neighborhood/sensitivity checks; prefer stable plateaus to isolated optima.
7. Compare candidate versus baseline without using final holdout to iterate.
8. Issue a promotion disposition with explicit unmet gates and uncertainty.

## Evidence and Readiness

Strong headline metrics are insufficient. Repeated searches inflate selection bias. A contaminated holdout cannot certify promotion, even if performance is excellent. State `PROPOSAL`, `NOT_SUPPORTED`, or evidence-backed promotion recommendation without implying production deployment authority.

## Verification

Check point-in-time features, no lookahead, embargo, split integrity, untouched final evaluation, sample sufficiency, transaction-cost assumptions, bootstrap method, regime/concentration results, parameter stability, and reproducibility hashes/manifests where supported.

## Handoffs

Decision-logic changes → `strategy-score-lifecycle`. Raw data quality → `market-data-evidence-quality`. Reproducible test execution → `verification-regression`. Deployment gating → `release-production-certification`.

## Common Mistakes

- Tuning on the holdout after seeing its performance.
- Reporting win rate without PF/EV/drawdown/sample context.
- Ignoring symbol/regime concentration.
- Optimizing a single sharp parameter optimum.
- Treating a backtest as proof of live execution realizability.
