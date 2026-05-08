# Vendored Telemetry Contract

## Source of Truth

The canonical telemetry contract lives in the central contract repository:

**Repository:** [chaithubk/medtech-telemetry-contract](https://github.com/chaithubk/medtech-telemetry-contract)  
**Pinned tag:** See [`VITALS_CONTRACT_VERSION.txt`](./VITALS_CONTRACT_VERSION.txt)  
**Schema path in source repo:** `schemas/vitals/v<MAJOR>.<MINOR>.json` (resolved from pinned tag)

The file `contracts/vitals/current.json` in this repository is the **active vendored local copy**,
pinned to a specific release tag for reproducible builds and offline environments (e.g. Yocto/QEMU).

## File Naming Strategy

We intentionally use a stable filename (`contracts/vitals/current.json`) as the
single schema consumed by publisher runtime checks and CI tests.

- The **exact upstream revision** is tracked in `contracts/VITALS_CONTRACT_VERSION.txt`.
- This avoids code churn whenever upstream moves from `v2.0.json` to `v2.1.json`.
- Since this service supports only the latest contract revision, we do not
	maintain backward-compatibility selectors in runtime code.

Versioned files (for example `v2.0.json`) may exist for historical context, but
`current.json` is the only file used by publisher execution and automated tests.

## Pinned Version

The currently vendored version is recorded in `contracts/VITALS_CONTRACT_VERSION.txt`.
To see the current pin:

```bash
cat contracts/VITALS_CONTRACT_VERSION.txt
```

## Update Procedure

**Do not manually edit `contracts/vitals/current.json`.**  
Always update it via the vendoring workflow or script:

### Option A — GitHub Actions (recommended)

1. Go to **Actions → Vendor Telemetry Contract** in this repository.
2. Click **Run workflow** and optionally specify a tag (defaults to the latest tag in the contract repo).
3. The workflow will open a PR with the updated schema and version pin.
4. Review and merge the PR.

### Option B — Local script

```bash
# Re-fetch the currently pinned tag from contracts/VITALS_CONTRACT_VERSION.txt
python scripts/vendor_telemetry_contract.py

# Update to latest release/tag (auto-detected)
python scripts/vendor_telemetry_contract.py --tag latest

# Pin to a specific tag
python scripts/vendor_telemetry_contract.py --tag v2.1.0
```

The script updates `contracts/vitals/current.json` and `contracts/VITALS_CONTRACT_VERSION.txt`, then
prints a diff summary. Commit and open a PR as usual.

## Policy

> **Publisher payload must validate against this schema.**

The CI test suite includes `tests/test_contract_schema_v2.py`, which generates a real v2 payload
using the production code path and validates it against `contracts/vitals/current.json` using
`jsonschema`.  Any payload field that violates the contract will cause the test (and therefore CI)
to fail immediately.

## Drift Detection

A scheduled GitHub Actions workflow (`.github/workflows/contract-drift-check.yml`) runs daily and
compares the latest tag in `chaithubk/medtech-telemetry-contract` against the pinned version here.
If a newer tag is available, the workflow automatically triggers the
**Vendor Telemetry Contract** workflow to open or update a PR with:

- `contracts/vitals/current.json` refreshed from the new tag
- `contracts/VITALS_CONTRACT_VERSION.txt` bumped
- `src/__init__.py` patch revision bumped
