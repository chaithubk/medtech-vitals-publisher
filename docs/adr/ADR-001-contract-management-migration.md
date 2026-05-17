
# ADR-001 — Contract Management Migration (Legacy Files → Metadata Pinning)

| Field | Value |
|---|---|
| **ADR ID** | ADR-001 |
| **Title** | Contract Management Migration (Legacy Files → Metadata Pinning) |
| **Status** | Accepted |
| **Date** | 2026-05-10 |
| **Deciders** | Vitals Publisher Maintainers |
| **Affected Repos** | `medtech-vitals-publisher` |

---


## Context

Previously, contract updates were managed with two legacy artifacts:
- `contracts/vitals/current.json` (active local schema copy)
- `contracts/VITALS_CONTRACT_VERSION.txt` (plain-text pinned version)

This model created several issues:
- Version pin and schema content could become inconsistent
- Pull requests did not clearly communicate compatibility impact
- Update identity was incomplete (no commit SHA/path provenance)
- Consumers depended on periodic drift checks instead of release-driven updates


---

## Decision

Adopt a metadata-first contract management model with canonical schema location and release-triggered automation.

**New standard:**
- `contracts/vitals/vitals.schema.json` is the only local runtime/CI schema artifact
- `contracts/vitals/contract-pin.json` is the authoritative pin record (repo, tag, commit SHA, source path, compatibility summary, schema diff summary, sync timestamp)
- Consumer workflows accept `repository_dispatch` on contract release events, with scheduled drift-check as fallback
- Vendoring script updates schema + metadata together and emits reviewer-friendly compatibility/diff information


---

## Before vs After

| Aspect | Before | After |
|---|---|---|
| Pinning | Dual-source (text file + schema file) | Single source of truth (`contract-pin.json`) |
| Path Semantics | Legacy (`current.json`) in runtime/packaging | Stable canonical schema file (`vitals.schema.json`) across runtime, tests, Docker, CI |
| Review Visibility | Limited for upgrade risk | Structured compatibility classification (`patch`/`minor`/`breaking`/`unknown`) and schema-diff summary for every update |
| Update Path | Periodic/manual drift checks | Release-driven update path for faster, cleaner propagation |


---

## Problems Solved

- Eliminates legacy file ambiguity and dual-source drift
- Improves traceability (exact upstream tag + commit SHA + path)
- Improves PR review quality via explicit compatibility/diff metadata
- Standardizes schema consumption path across environments


---

## Problems This Continues to Solve

- Scales consistently to additional consumer repositories
- Reduces operational delay compared with polling-only update models
- Supports safer contract upgrades with clear breaking-change signals


---

## Consequences

### Positive
- Cleaner consumer template for future migrations
- Better governance and audit posture for contract updates

### Negative
- Requires contract repo release-dispatch workflow and token management
- Metadata quality now depends on vendoring automation correctness (covered by tests/CI)


---

## Validation

Migration completion checks include:
- No references to `contracts/vitals/current.json` or `contracts/VITALS_CONTRACT_VERSION.txt`
- CI and tests pass with canonical schema and metadata-only model
- Release dispatch from contract repo successfully triggers consumer workflow path
