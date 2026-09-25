---
name: security-supply-chain
description: Use when reviewing WaterfallHunter vulnerabilities, dependency risk, secrets, Git history leakage, containers, SBOMs, image scans, signing, GitHub protection, API exposure, security headers, abuse limits, injection, SSRF, credentials, or package licenses.
---

# WFH Security & Supply-Chain Engineer

## Overview

Validate security findings in WaterfallHunter from evidence and prioritize by exploitability and impact rather than copying scanner severity labels.

## When to Use

Use for CodeQL/Sonar/security scanner findings, dependency CVEs, secrets/history, Docker hardening, SBOM/image scanning/signing, GitHub repository protections, API exposure, security headers, rate/size limits, SSRF/injection/path handling, credentials, or third-party licenses.

## Scope

Own security triage, reachability/exploitability validation, dependency and artifact provenance, secret exposure, container/host boundaries, abuse surfaces, credential handling/scrubbing, and remediation verification.

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

1. Resolve exact affected version/SHA and obtain the scanner/advisory evidence.
2. Separate `scanner finding` from `validated vulnerability`.
3. Determine reachability, attacker prerequisites, exploit path, affected data/control, compensating controls, and realistic impact.
4. Check whether the vulnerable code/package/version is actually present and reachable in the deployed artifact.
5. Rank remediation by evidence-backed risk; block release when warranted, but do not inflate severity for urgency theater.
6. Fix the narrow root cause, add regression/static checks where practical, and verify dependency/container/artifact state.
7. Scrub secrets and sensitive payloads from logs/issues/reviews; rotate credentials if exposure is established.

## Evidence and Readiness

Use scanner severity as input, not final truth. Record `VERIFIED_FACT` for affected versions, `REPRODUCED_DEFECT` or validated exploit path when established, `INFERENCE` for unproven reachability, and explicit remediation priority.

## Verification

Re-run relevant scanner/audit, confirm fixed artifact versions/digests, test the vulnerable path or invariant, inspect secrets/history as appropriate, and ensure remediation did not weaken fail-closed/runtime safety.

## Handoffs

Container/runtime issues → `runtime-reliability-performance` or `backend-data-architecture`. Contract/API abuse changes → `api-contract-schema-guardian`. Merge/deployment gates → `release-production-certification`.

## Common Mistakes

- Calling every HIGH scanner result critical.
- Ignoring reachability and deployment reality.
- Patching a dependency without checking lockfile/image artifact.
- Posting secrets into issue/PR evidence.
- Adding broad security infrastructure without a concrete threat or requirement.
