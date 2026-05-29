from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() == "true"


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


STRICT_MODE = env_flag("DOCS_STRICT_MODE", False)
MIN_CONFIDENCE = env_float("DOCS_MIN_CONFIDENCE", 0.70)


def exit_with_policy(message: str) -> None:
    print(message)
    raise SystemExit(1 if STRICT_MODE else 0)


def run_text(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def get_diff(env_var_name: str, default_window: int, max_window: int) -> str:
    raw_window = os.getenv(env_var_name, str(default_window))
    try:
        commit_window = int(raw_window)
    except ValueError:
        commit_window = default_window

    safe_window = min(max(commit_window, 1), max_window)
    primary = run_text(["git", "diff", "--unified=2", f"HEAD~{safe_window}..HEAD"])
    if primary.strip():
        return primary
    return run_text(["git", "show", "--pretty=format:", "--unified=2", "HEAD"])


def collect_markdown_files(root_dir: str) -> list[Path]:
    root = Path(root_dir)
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.md") if path.is_file())


def read_docs() -> str:
    output_parts: list[str] = []

    readme_path = Path("README.md")
    if readme_path.exists():
        readme_content = readme_path.read_text(encoding="utf-8")
        output_parts.append(f"FILE: README.md\n{readme_content}\n")

    for file_path in collect_markdown_files("docs"):
        file_content = file_path.read_text(encoding="utf-8")
        output_parts.append(f"FILE: {file_path.as_posix()}\n{file_content}\n")

    return "\n".join(output_parts)


def extract_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model output")
    return text[start : end + 1].replace("\r\n", "\n")


def extract_balanced_json_objects(text: str) -> list[str]:
    objects: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False

    for index, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == "{":
            if depth == 0:
                start = index
            depth += 1
            continue

        if ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                objects.append(text[start : index + 1].replace("\r\n", "\n"))
                start = -1

    return objects


def parse_model_json(text: str) -> dict[str, Any]:
    candidates: list[str] = []
    fenced_regex = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

    for match in fenced_regex.finditer(text):
        candidate = match.group(1).strip()
        if candidate:
            candidates.append(candidate)

    trimmed = text.strip()
    if trimmed.startswith("{") and trimmed.endswith("}"):
        candidates.append(trimmed)

    candidates.extend(extract_balanced_json_objects(text))

    if not candidates:
        candidates.append(extract_json_object(text))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("UPDATED_FILES"), dict):
            return parsed

    raise ValueError("No valid JSON candidate matched expected schema")


def output_preview(text: str, max_len: int = 1200) -> str:
    if not text:
        return "<empty>"
    return " ".join(text[:max_len].split())


def is_allowed_doc_path(file_path: str) -> bool:
    normalized = Path(file_path.replace("\\", "/"))
    normalized_text = normalized.as_posix()
    if normalized.is_absolute() or normalized_text.startswith("../"):
        return False
    if normalized_text == "README.md":
        return True
    return normalized_text.startswith("docs/") and normalized_text.endswith(".md")


def validate_model_payload(parsed: dict[str, Any], diff: str) -> None:
    if not isinstance(parsed.get("UPDATED_FILES"), dict):
        exit_with_policy("Model response shape invalid.")

    raw_confidence = parsed.get("CONFIDENCE")
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = None

    if STRICT_MODE and confidence is None:
        exit_with_policy("Missing or invalid CONFIDENCE in model output.")
    if confidence is not None and confidence < MIN_CONFIDENCE:
        exit_with_policy(
            f"Model confidence {confidence} below threshold {MIN_CONFIDENCE}."
        )
    if STRICT_MODE and diff.strip() and not parsed["UPDATED_FILES"]:
        exit_with_policy("Empty UPDATED_FILES in strict mode with non-empty diff.")


def write_updates(updated_files: dict[str, Any], diff: str) -> int:
    applied_writes = 0

    for file_name, content in updated_files.items():
        if not is_allowed_doc_path(file_name):
            print(f"Skipping unsafe target path: {file_name}")
            continue
        if not isinstance(content, str):
            continue

        path = Path(file_name)
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)

        path.write_text(content, encoding="utf-8")
        applied_writes += 1

    if STRICT_MODE and diff.strip() and applied_writes == 0:
        exit_with_policy("No writable documentation changes applied in strict mode.")

    return applied_writes
