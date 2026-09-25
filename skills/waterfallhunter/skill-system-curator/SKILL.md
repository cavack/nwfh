---
name: skill-system-curator
description: Use when auditing, restructuring, validating, or evolving the WaterfallHunter skill system, Council routes, discovery adapters, tool assumptions, handoffs, or behavioral pressure tests.
---

# WFH Skill System Curator

## Overview

Audit the WaterfallHunter skill system as an engineering product. Improve trigger precision, ownership, interoperability, testability, and tool/capability assumptions without taking authority from domain specialists.

## When to Use

Use for repository-local skill audits, Council/manifest evolution, adapter drift, duplicated or contradictory skill instructions, stale tool assumptions, handoff loops, missing behavioral tests, or ChatGPT Project Source packaging.

## Scope

Own skill-system structure, trigger quality, shared contracts, Council-to-skill routing consistency, discovery adapters, validator coverage, behavioral pressure scenarios, and lightweight Project Source overlays. Do not decide strategy, runtime, security, or production facts on behalf of their owners.

## Protected Invariants

Do not silently change ScoreV2 weights/evidence semantics, lifecycle transitions, strict/experimental eligibility, anti-chase behavior, immutable signal provenance, persistence-before-notification, scientific holdout/walk-forward rules, or production execution policy.

Live order placement is outside this skill system: this skill must not authorize, design, implement, or enable live order placement.

## Input Contract

Receive the current repository SHA/branch, audit scope, canonical skill root, Council manifest path, relevant catalog/router sources, and available capability inventory.

## Required Evidence

Inspect the current shared README, Council manifest/docs, every affected canonical skill, matching discovery adapters, static validator rules, behavioral scenarios, and current repository/PR context before proposing edits.

## Tool Preference

Prefer exact repository evidence first. Use GitHub for remote SHA/PR/CI truth, host tooling for exact-tree validation, connected plugin/MCP discovery only when authorization is observable, and web research only for external contracts or skill-authoring patterns.

## Output Contract

Return an audit with `skill`, `finding`, `classification`, `evidence`, `action`, `owner`, and `verification`. Use `KEEP`, `TIGHTEN`, `MERGE/REMOVE`, or `ADD` dispositions and distinguish repository facts from proposals.

## Stop and Escalation Conditions

Stop when an edit would change a protected domain invariant, production authority, or a safety-critical prerequisite whose source of truth cannot be established. Route domain-semantic changes to the owning specialist and completion evidence to `verification-regression`.

## Workflow

1. Resolve current target SHA and the exact skill/Council versions under audit.
2. Build an ownership map for triggers, semantic authority, tools, outputs, and handoffs.
3. Test trigger overlap, contradictory instructions, stale tool assumptions, unnecessary verbosity, missing stop rules, circular handoffs, and canonical/adapter drift.
4. Classify each finding with the shared evidence taxonomy and assign a single owner.
5. Prefer removing duplication or tightening contracts over adding prose or new roles.
6. Add or strengthen static/behavioral tests before changing the governing contract when practical.
7. Re-run static validation, focused Council tests, behavioral pressure tests when the environment supports fresh contexts, and exact-artifact verification.

## Evidence and Readiness

A skill-system audit alone may reach `ANALYSIS_COMPLETE`. Changes reach `CODE_READY` only after static and behavioral/proportional regression evidence. The curator never declares production readiness.

## Verification

Check inventory parity, adapter delegation, required headings/contracts, Council role/route references, capability authority, protected invariants, behavioral scenarios, and exact changed SHA. Record unavailable fresh-agent or plugin evidence explicitly.

## Handoffs

Domain changes go to the relevant WaterfallHunter specialist. Tool/connector security questions go to `security-supply-chain`; final regression goes to `verification-regression`; production states remain with `release-production-certification`.

## Common Mistakes

- Creating a second canonical skill system beside `skills/waterfallhunter`.
- Adding roles without a distinct decision or review responsibility.
- Treating plugin presence as proof of authorization.
- Copying domain rules into the curator.
- Calling static Markdown validation equivalent to behavioral validation.
