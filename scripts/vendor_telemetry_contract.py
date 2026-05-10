#!/usr/bin/env python3
"""Vendor the MedTech telemetry contract schema at a pinned upstream revision.

This script fetches the canonical schema path from the contract repository,
writes a local vendored copy, and updates structured pin metadata for
traceability.

Primary outputs:

- ``contracts/vitals/vitals.schema.json``
- ``contracts/vitals/contract-pin.json``

"""

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_OWNER = "chaithubk"
REPO_NAME = "medtech-telemetry-contract"
REPO_ROOT = Path(__file__).parent.parent
CONTRACT_DIR = REPO_ROOT / "contracts"
VITALS_DIR = CONTRACT_DIR / "vitals"
SCHEMA_DEST = VITALS_DIR / "vitals.schema.json"
METADATA_DEST = VITALS_DIR / "contract-pin.json"
UPSTREAM_SCHEMA_PATH = "schemas/vitals/vitals.schema.json"


def source_schema_candidates_for_tag(tag: str) -> list[str]:
    """Return preferred upstream schema paths for a specific tag."""
    candidates = [UPSTREAM_SCHEMA_PATH]
    if tag.startswith("v") and tag.count(".") == 2:
        major_minor = tag[1:].rsplit(".", 1)[0]
        candidates.append(f"schemas/vitals/v{major_minor}.json")
    return candidates


def _read_pinned_tag() -> str:
    """Return the pinned contract tag from metadata."""
    if not METADATA_DEST.exists():
        raise RuntimeError(
            "No existing pin found. Expected contracts/vitals/contract-pin.json."
        )
    data = json.loads(METADATA_DEST.read_text(encoding="utf-8"))
    tag = str(data.get("tag", "")).strip()
    if not tag:
        raise RuntimeError("Missing 'tag' in contracts/vitals/contract-pin.json")
    return tag


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
        req = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            tags = json.loads(resp.read())
            if not tags:
                raise RuntimeError(f"No tags found in {REPO_OWNER}/{REPO_NAME}")
            return tags[0]["name"]


def _github_api_json(url: str) -> Any:
    """Call GitHub REST API and return decoded JSON payload."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _fetch_commit_sha_for_tag(tag: str) -> str:
    """Resolve commit SHA for a given tag using the GitHub API."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits/{tag}"
    data = _github_api_json(url)
    sha = str(data.get("sha", "")).strip()
    if not sha:
        raise RuntimeError(f"Could not resolve commit SHA for tag {tag}")
    return sha


def _fetch_release_breaking_hint(tag: str) -> bool | None:
    """Best-effort breaking hint from release notes; returns None if unavailable."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/tags/{tag}"
    try:
        release = _github_api_json(url)
    except urllib.error.HTTPError:
        return None
    body = str(release.get("body") or "")
    if not body:
        return None
    lowered = body.lower()
    return "breaking" in lowered or "incompatible" in lowered


def _download_schema(tag: str) -> tuple[str, str]:
    """Download schema JSON for *tag* and return ``(raw_json, source_path)``."""
    last_exc: Exception | None = None
    for source_path in source_schema_candidates_for_tag(tag):
        url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{tag}/{source_path}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8"), source_path
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code == 404:
                continue
            raise
    raise RuntimeError(
        f"Could not locate schema path in {REPO_OWNER}/{REPO_NAME}@{tag}. "
        f"Tried: {', '.join(source_schema_candidates_for_tag(tag))}. Last error: {last_exc}"
    )


def _pretty_json(raw: str) -> str:
    """Normalise JSON to 2-space indented form with a trailing newline."""
    return json.dumps(json.loads(raw), indent=2, ensure_ascii=False) + "\n"


def _parse_semver_tag(tag: str) -> tuple[int, int, int] | None:
    """Return (major, minor, patch) for v-prefixed tags; otherwise None."""
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _schema_diff(
    old_schema: dict[str, Any] | None, new_schema: dict[str, Any]
) -> dict[str, list[str]]:
    """Compute high-value schema diff slices for review and compatibility hints."""
    old_schema = old_schema or {}
    old_required = set(old_schema.get("required", []))
    new_required = set(new_schema.get("required", []))
    old_props = (
        old_schema.get("properties", {})
        if isinstance(old_schema.get("properties", {}), dict)
        else {}
    )
    new_props = (
        new_schema.get("properties", {})
        if isinstance(new_schema.get("properties", {}), dict)
        else {}
    )

    type_changed: list[str] = []
    for key in sorted(set(old_props.keys()) & set(new_props.keys())):
        old_type = (
            old_props.get(key, {}).get("type")
            if isinstance(old_props.get(key, {}), dict)
            else None
        )
        new_type = (
            new_props.get(key, {}).get("type")
            if isinstance(new_props.get(key, {}), dict)
            else None
        )
        if old_type != new_type:
            type_changed.append(key)

    return {
        "required_added": sorted(new_required - old_required),
        "required_removed": sorted(old_required - new_required),
        "properties_added": sorted(set(new_props.keys()) - set(old_props.keys())),
        "properties_removed": sorted(set(old_props.keys()) - set(new_props.keys())),
        "type_changed": type_changed,
    }


def _compatibility_from_semver_and_diff(
    previous_tag: str | None, target_tag: str, diff: dict[str, list[str]]
) -> tuple[str, bool, str]:
    """Return (classification, breaking, source) for compatibility metadata."""
    breaking_by_diff = bool(
        diff["required_removed"] or diff["properties_removed"] or diff["type_changed"]
    )
    if breaking_by_diff:
        return "breaking", True, "schema_diff"

    if previous_tag:
        prev = _parse_semver_tag(previous_tag)
        tgt = _parse_semver_tag(target_tag)
        if prev and tgt:
            if tgt[0] > prev[0]:
                return "breaking", True, "semver"
            if tgt[1] > prev[1]:
                return "minor", False, "semver"
            if tgt[2] > prev[2]:
                return "patch", False, "semver"
            if tgt == prev:
                return "patch", False, "semver"

    return "unknown", False, "insufficient_data"


def _build_summary_markdown(
    previous_tag: str | None,
    target_tag: str,
    metadata: dict[str, Any],
) -> str:
    """Render human-readable update summary for automated vendor PR body."""
    diff = metadata["schema_diff"]
    compat = metadata["compatibility"]
    app_changes = "Yes" if metadata["application_code_changes_required"] else "No"
    breaking = "Yes" if compat["breaking"] else "No"

    return "\n".join(
        [
            "## Contract Pin Update",
            "",
            f"- Previous tag: `{previous_tag or 'none'}`",
            f"- New tag: `{target_tag}`",
            f"- Commit SHA: `{metadata['commit_sha']}`",
            f"- Canonical schema path: `{metadata['schema_path']}`",
            f"- Compatibility classification: `{compat['classification']}`",
            f"- Breaking change: **{breaking}**",
            f"- Application code changes likely needed: **{app_changes}**",
            "",
            "## Schema Diff Summary",
            "",
            f"- Required fields added: {len(diff['required_added'])}",
            f"- Required fields removed: {len(diff['required_removed'])}",
            f"- Properties added: {len(diff['properties_added'])}",
            f"- Properties removed: {len(diff['properties_removed'])}",
            f"- Property types changed: {len(diff['type_changed'])}",
            "",
            "## Compatibility Notes",
            "",
            "- `breaking`: requires migration checklist + consumer code review.",
            "- `minor`: review payload handling and optional field usage.",
            "- `patch`: low-risk compatibility update, still verify tests.",
            "",
            "## Changed Files",
            "",
            "- `contracts/vitals/vitals.schema.json`",
            "- `contracts/vitals/contract-pin.json`",
        ]
    )


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
            "Defaults to the version in contracts/vitals/contract-pin.json."
        ),
    )
    parser.add_argument(
        "--summary-file",
        default=None,
        help="Optional path to write a Markdown summary for PR descriptions.",
    )
    args = parser.parse_args(argv)

    # Resolve the desired tag
    if args.tag is None:
        tag = _read_pinned_tag()
        print(f"Using pinned tag from {METADATA_DEST.relative_to(REPO_ROOT)}: {tag}")
    elif args.tag.lower() == "latest":
        tag = _fetch_latest_tag()
        print(f"Resolved latest tag: {tag}")
    else:
        tag = args.tag
        print(f"Using requested tag: {tag}")

    previous_tag: str | None = None
    if METADATA_DEST.exists():
        metadata_old = json.loads(METADATA_DEST.read_text(encoding="utf-8"))
        previous_tag = str(metadata_old.get("tag") or "").strip() or None

    # Download the schema
    print(f"Downloading schema from {REPO_OWNER}/{REPO_NAME}@{tag} ...")
    raw, source_path = _download_schema(tag)
    print(f"Resolved source schema path: {source_path}")
    normalised = _pretty_json(raw)
    new_schema = json.loads(normalised)

    old_schema_dict: dict[str, Any] | None = None
    if SCHEMA_DEST.exists():
        try:
            old_schema_dict = json.loads(SCHEMA_DEST.read_text(encoding="utf-8"))
        except Exception:
            old_schema_dict = None

    # Compare with the existing vendored copy
    schema_dest = SCHEMA_DEST
    changed_schema = False
    if schema_dest.exists():
        existing = schema_dest.read_text()
        if existing == normalised:
            print(f"  {schema_dest.relative_to(REPO_ROOT)} — no change")
        else:
            changed_schema = True
            print(f"  {schema_dest.relative_to(REPO_ROOT)} — UPDATED")
    else:
        changed_schema = True
        print(f"  {schema_dest.relative_to(REPO_ROOT)} — CREATED")

    if changed_schema:
        schema_dest.parent.mkdir(parents=True, exist_ok=True)
        schema_dest.write_text(normalised, encoding="utf-8")

    commit_sha = _fetch_commit_sha_for_tag(tag)
    diff = _schema_diff(old_schema_dict, new_schema)
    classification, breaking, compat_source = _compatibility_from_semver_and_diff(
        previous_tag, tag, diff
    )
    breaking_hint = _fetch_release_breaking_hint(tag)
    if breaking_hint is True and not breaking:
        classification = "breaking"
        breaking = True
        compat_source = "release_notes"

    metadata = {
        "contract_repo": f"{REPO_OWNER}/{REPO_NAME}",
        "tag": tag,
        "commit_sha": commit_sha,
        "schema_path": UPSTREAM_SCHEMA_PATH,
        "resolved_schema_path": source_path,
        "local_schema": str(SCHEMA_DEST.relative_to(REPO_ROOT)),
        "synced_at_utc": dt.datetime.now(dt.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "compatibility": {
            "classification": classification,
            "breaking": breaking,
            "source": compat_source,
            "release_notes_breaking_hint": breaking_hint,
        },
        "schema_diff": diff,
        "application_code_changes_required": bool(
            diff["required_added"]
            or diff["required_removed"]
            or diff["properties_removed"]
            or diff["type_changed"]
        ),
    }

    if METADATA_DEST.exists():
        existing_metadata = json.loads(METADATA_DEST.read_text(encoding="utf-8"))
        metadata_no_time = {k: v for k, v in metadata.items() if k != "synced_at_utc"}
        existing_no_time = {
            k: v for k, v in existing_metadata.items() if k != "synced_at_utc"
        }
        if metadata_no_time == existing_no_time and existing_metadata.get(
            "synced_at_utc"
        ):
            metadata["synced_at_utc"] = existing_metadata["synced_at_utc"]

    METADATA_DEST.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  {METADATA_DEST.relative_to(REPO_ROOT)} — UPDATED")

    if args.summary_file:
        summary = _build_summary_markdown(previous_tag, tag, metadata)
        summary_path = Path(args.summary_file)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(summary + "\n", encoding="utf-8")
        print(f"  {summary_path} — wrote PR summary")

    if not changed_schema:
        print("Everything already up to date.")
    else:
        print("Done. Commit the changed files and open a PR.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
