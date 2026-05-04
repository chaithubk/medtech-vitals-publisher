#!/usr/bin/env python3
"""Deterministic vendoring script for the MedTech telemetry contract schema.

Downloads the vitals JSON Schema from ``chaithubk/medtech-telemetry-contract``
at a specific git tag and writes it to ``contracts/vitals/v2.0.json``, also
updating the pin file ``contracts/VITALS_CONTRACT_VERSION.txt``.

Usage::

    # Use the tag recorded in contracts/VITALS_CONTRACT_VERSION.txt
    python scripts/vendor_telemetry_contract.py

    # Override with a specific tag
    python scripts/vendor_telemetry_contract.py --tag v2.1.0

    # Resolve and use the latest published tag automatically
    python scripts/vendor_telemetry_contract.py --tag latest
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_OWNER = "chaithubk"
REPO_NAME = "medtech-telemetry-contract"
SCHEMA_SOURCE_PATH = "schemas/vitals/v2.0.json"

REPO_ROOT = Path(__file__).parent.parent
CONTRACT_DIR = REPO_ROOT / "contracts"
VERSION_FILE = CONTRACT_DIR / "VITALS_CONTRACT_VERSION.txt"
SCHEMA_DEST = CONTRACT_DIR / "vitals" / "v2.0.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_pinned_tag() -> str:
    """Return the tag recorded in VITALS_CONTRACT_VERSION.txt."""
    return VERSION_FILE.read_text().strip()


def _fetch_latest_tag() -> str:
    """Return the latest published tag name from the contract repo via GitHub API."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data["tag_name"]
    except Exception:
        # Fall back to listing tags if there are no GitHub releases
        url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/tags"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            tags = json.loads(resp.read())
            if not tags:
                raise RuntimeError(f"No tags found in {REPO_OWNER}/{REPO_NAME}")
            return tags[0]["name"]


def _download_schema(tag: str) -> str:
    """Download the raw schema JSON for *tag* and return it as a string."""
    url = (
        f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}"
        f"/{tag}/{SCHEMA_SOURCE_PATH}"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


def _pretty_json(raw: str) -> str:
    """Normalise JSON to 2-space indented form with a trailing newline."""
    return json.dumps(json.loads(raw), indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Vendor the telemetry contract schema into this repository."
    )
    parser.add_argument(
        "--tag",
        default=None,
        help=(
            "Contract repo tag to vendor (e.g. v2.0.0). "
            "Use 'latest' to auto-resolve. "
            "Defaults to the version in contracts/VITALS_CONTRACT_VERSION.txt."
        ),
    )
    args = parser.parse_args(argv)

    # Resolve the desired tag
    if args.tag is None:
        tag = _read_pinned_tag()
        print(f"Using pinned tag from {VERSION_FILE.relative_to(REPO_ROOT)}: {tag}")
    elif args.tag.lower() == "latest":
        tag = _fetch_latest_tag()
        print(f"Resolved latest tag: {tag}")
    else:
        tag = args.tag
        print(f"Using requested tag: {tag}")

    # Download the schema
    print(f"Downloading schema from {REPO_OWNER}/{REPO_NAME}@{tag} ...")
    raw = _download_schema(tag)
    normalised = _pretty_json(raw)

    # Compare with the existing vendored copy
    changed_schema = False
    if SCHEMA_DEST.exists():
        existing = SCHEMA_DEST.read_text()
        if existing == normalised:
            print(f"  contracts/vitals/v2.0.json — no change")
        else:
            changed_schema = True
            print(f"  contracts/vitals/v2.0.json — UPDATED")
    else:
        changed_schema = True
        print(f"  contracts/vitals/v2.0.json — CREATED")

    if changed_schema:
        SCHEMA_DEST.parent.mkdir(parents=True, exist_ok=True)
        SCHEMA_DEST.write_text(normalised)

    # Update the pin file
    current_pin = _read_pinned_tag() if VERSION_FILE.exists() else ""
    if current_pin != tag:
        VERSION_FILE.write_text(tag + "\n")
        print(f"  contracts/VITALS_CONTRACT_VERSION.txt — updated {current_pin!r} → {tag!r}")
    else:
        print(f"  contracts/VITALS_CONTRACT_VERSION.txt — no change ({tag})")

    if not changed_schema and current_pin == tag:
        print("Everything already up to date.")
    else:
        print("Done. Commit the changed files and open a PR.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
