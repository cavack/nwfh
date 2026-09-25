---
name: market-data-evidence-quality
description: Use when WaterfallHunter exchange or provider data, symbol identity, timestamps, freshness, candles, mark/index/last prices, funding, open interest, taker flow, order books, cross-exchange evidence, or provider disagreement needs validation.
---

# WFH Market Data & Evidence Quality Engineer

## Overview

Ensure strategy decisions consume correctly identified, temporally valid, complete-enough market evidence with explicit unavailable semantics.

## When to Use

Use for exchange/provider adapters, symbol/contract mapping, timestamp causality, stale feeds, candle packets, mark/index/last prices, funding, OI, taker flow, order book/depth, execution observations, cross-exchange confirmation, or provider disagreement.

## Scope

Own evidence identity, freshness, completeness, temporal ordering, unit/scale normalization, provider semantics, disagreement/outlier handling, and fail-closed unavailable behavior. Do not invent directional meaning for missing data.

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

1. Resolve exact contract identity: venue, instrument type, quote/margin/settlement asset, symbol mapping, and relevant market metadata.
2. Validate observed timestamps, decision timestamps, candle closure, and causal ordering.
3. Check freshness and completeness independently for each evidence domain.
4. Reconcile mark/index/last/reference prices and document which is authoritative for each calculation.
5. Validate derivatives units/sign conventions for funding, OI, taker flow, ratios, and deltas.
6. Validate order-book depth/spread/slippage sampling and stale-book handling.
7. Treat provider disagreement as evidence quality/context, not something to average away blindly.
8. Emit explicit PASS/FAIL/UNAVAILABLE/PARTIAL status according to the canonical contract; fail closed where strict gates require evidence.

## Evidence and Readiness

`UNAVAILABLE` means evidence was not validly established. It is neither bearish nor bullish and must not be silently converted to zero/false unless a documented validated model rule explicitly does so.

## Verification

Test symbol mapping, timestamps, stale boundaries, closed-candle rules, unit/sign conversion, missing fields, provider disagreement, extreme/outlier values, non-finite values, and downstream score behavior for unavailable packets.

## Handoffs

Evidence meaning used by scoring → `strategy-score-lifecycle`. Contract representation → `api-contract-schema-guardian`. Provider/runtime reliability → `runtime-reliability-performance`. Regression suite → `verification-regression`.

## Common Mistakes

- Counting missing funding as a bearish confirmation.
- Mixing spot and perpetual symbols/contracts.
- Comparing prices observed at materially different times.
- Using best bid/ask/reference inconsistently and double-counting slippage.
- Treating stale data as fresh because a request succeeded.
