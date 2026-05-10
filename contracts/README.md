# Vendored Telemetry Contract

## Source of Truth

The canonical telemetry contract lives in the central contract repository:

**Repository:** [chaithubk/medtech-telemetry-contract](https://github.com/chaithubk/medtech-telemetry-contract)  
**Canonical schema path:** `schemas/vitals/vitals.schema.json`

This repository vendors a pinned local copy for reproducible builds and offline
environments (for example Yocto/QEMU images).

## Pinning Model

Primary machine-readable pin metadata:

- `contracts/vitals/contract-pin.json`

Primary vendored schema file used by runtime/tests/packaging:

- `contracts/vitals/vitals.schema.json`

## File Naming Strategy

We intentionally use a stable local filename (`contracts/vitals/vitals.schema.json`)
as the single schema consumed by publisher runtime checks and CI tests.

- The exact upstream identity is tracked in `contracts/vitals/contract-pin.json`:
	contract repo, tag/version, commit SHA, canonical schema path, sync
	timestamp, compatibility classification, and schema diff summary.

This avoids fragile opaque updates and makes contract bumps auditable.

## Submodule vs Vendoring

Consumer repos may use a contract repo submodule for local developer workflow,
but runtime and Yocto integration must not depend on submodule initialization.

The production approach remains:

- Pin immutable contract source (tag + SHA)
- Vendor schema artifact into this repository
- Install schema into image with dedicated contract packaging path

## Pinned Version

To inspect the current pin metadata:

```bash
cat contracts/vitals/contract-pin.json
```

## Update Procedure

**Do not manually edit contract artifacts.**  
Always update it via the vendoring workflow or script:

### Option A — GitHub Actions (recommended)

1. Go to **Actions → Vendor Telemetry Contract** in this repository.
2. Click **Run workflow** and optionally specify a tag (defaults to the latest tag in the contract repo).
3. The workflow opens/updates a PR with schema + metadata updates and compatibility notes.
4. Review and merge the PR.

### Option B — Local script

```bash
# Re-fetch the currently pinned tag from contracts/vitals/contract-pin.json
python scripts/vendor_telemetry_contract.py

# Update to latest release/tag (auto-detected)
python scripts/vendor_telemetry_contract.py --tag latest

# Pin to a specific tag
python scripts/vendor_telemetry_contract.py --tag v2.1.0
```

The script updates schema + metadata together and can emit a PR-ready summary.

## Policy

> **Publisher payload must validate against this schema.**

The CI test suite includes `tests/test_contract_schema_v2.py`, which generates a real v2 payload
using the production code path and validates it against `contracts/vitals/vitals.schema.json` using
`jsonschema`.  Any payload field that violates the contract will cause the test (and therefore CI)
to fail immediately.

CI also validates metadata/schema consistency in `tests/test_contract_pin_metadata.py`.

## Update Trigger Strategy

Primary mechanism: release-driven repository dispatch trigger.

Fallback mechanism: scheduled drift check (weekly) if release events are missed.

The update PR includes:

- pinned tag and commit SHA
- compatibility class (`patch`, `minor`, `breaking`, or `unknown`)
- schema diff summary
- explicit note on whether app code changes are likely required
