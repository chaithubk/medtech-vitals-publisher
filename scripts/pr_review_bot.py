from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY", "")
PR_NUMBER = os.getenv("PR_NUMBER", "")
BASE_SHA = os.getenv("BASE_SHA", "")

MAX_DIFF_CHARS = 3000
MAX_BODY_CHARS = 800
BOT_MARKER = "🤖 PR Review Bot (Ollama)"
NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "256"))


# -------- Git helpers --------


def run_text(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        return completed.stdout if completed.returncode == 0 else ""
    except OSError:
        return ""


def get_pr_diff() -> str:
    if BASE_SHA:
        diff = run_text(["git", "diff", BASE_SHA, "HEAD"])
        if diff.strip():
            return diff
    return run_text(["git", "diff", "HEAD~3..HEAD"])


def get_changed_files() -> list[str]:
    if BASE_SHA:
        out = run_text(["git", "diff", "--name-only", BASE_SHA, "HEAD"])
    else:
        out = run_text(["git", "diff", "--name-only", "HEAD~3..HEAD"])
    return [f.strip() for f in out.splitlines() if f.strip()]


def get_commit_messages() -> str:
    if BASE_SHA:
        return run_text(["git", "log", "--pretty=format:%s", f"{BASE_SHA}..HEAD"])
    return run_text(["git", "log", "--pretty=format:%s", "HEAD~5..HEAD"])


# -------- GitHub API helpers --------


def _github_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_pr_meta() -> tuple[str, str]:
    if not (GITHUB_TOKEN and GITHUB_REPO and PR_NUMBER):
        return os.getenv("PR_TITLE", "(unknown)"), ""
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}" f"/pulls/{PR_NUMBER}"
        req = urllib.request.Request(url, headers=_github_headers())
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return (
                data.get("title") or "",
                data.get("body") or "",
            )
    except Exception:
        return os.getenv("PR_TITLE", "(unknown)"), ""


def find_existing_bot_comment() -> str | None:
    if not (GITHUB_TOKEN and GITHUB_REPO and PR_NUMBER):
        return None
    try:
        url = (
            f"https://api.github.com/repos/{GITHUB_REPO}"
            f"/issues/{PR_NUMBER}/comments?per_page=100"
        )
        req = urllib.request.Request(url, headers=_github_headers())
        with urllib.request.urlopen(req, timeout=10) as resp:
            for comment in json.loads(resp.read()):
                is_bot = comment.get("user", {}).get("login") == "github-actions[bot]"
                has_marker = BOT_MARKER in (comment.get("body") or "")
                if is_bot and has_marker:
                    return str(comment["id"])
    except Exception:
        pass
    return None


def upsert_pr_comment(body: str) -> None:
    if not (GITHUB_TOKEN and GITHUB_REPO and PR_NUMBER):
        print("GitHub context missing — printing comment to stdout:\n")
        print(body)
        return

    existing_id = find_existing_bot_comment()
    if existing_id:
        url = (
            f"https://api.github.com/repos/{GITHUB_REPO}"
            f"/issues/comments/{existing_id}"
        )
        method = "PATCH"
    else:
        url = (
            f"https://api.github.com/repos/{GITHUB_REPO}"
            f"/issues/{PR_NUMBER}/comments"
        )
        method = "POST"

    payload = json.dumps({"body": body}).encode("utf-8")
    headers = {**_github_headers(), "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            action = "Updated" if existing_id else "Posted"
            print(f"{action} PR comment: HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        print(f"Failed to post comment: {exc.code} {exc.reason}")


# -------- Prompt --------


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (truncated)"


def deterministic_checks(changed_files: list[str]) -> dict[str, object]:
    src_changed = any(f.startswith(("src/", "scripts/")) for f in changed_files)
    tests_changed = any(f.startswith("tests/") or "test_" in f for f in changed_files)
    contract_files = [f for f in changed_files if "contracts/" in f]
    test_gap = src_changed and not tests_changed

    if test_gap:
        test_gap_details = "Source files changed but no test files changed in this PR."
    elif src_changed:
        test_gap_details = "Source and test files both changed."
    else:
        test_gap_details = "No source changes detected."

    if contract_files:
        changed_contracts = ", ".join(contract_files[:5])
        contract_summary = f"Contract files updated: {changed_contracts}"
    else:
        contract_summary = "No contract files changed."

    return {
        "src_changed": src_changed,
        "tests_changed": tests_changed,
        "contract_changed": bool(contract_files),
        "test_gap": {
            "detected": test_gap,
            "details": test_gap_details,
        },
        "contract_change": {
            "detected": bool(contract_files),
            "summary": contract_summary,
        },
    }


def build_prompt(
    title: str,
    body: str,
    diff: str,
    changed_files: list[str],
    commit_messages: str,
) -> str:
    files_summary = "\n".join(changed_files[:30]) or "(none)"

    return f"""You are a code review assistant analyzing a pull request.

Return STRICT JSON only (no prose, no markdown fences):
{{
  "pr_description": {{
    "score": "good|needs_improvement|poor",
        "feedback": "specific feedback referencing this PR"
  }},
  "commit_messages": {{
    "score": "good|needs_improvement|poor",
        "feedback": "specific feedback referencing this PR"
  }}
}}

IMPORTANT:
- Do not output placeholders like "one concise sentence".
- Use concrete details from title, description, commits, and diff.
- Keep each feedback under 25 words.

PR TITLE: {title}

PR DESCRIPTION:
{truncate(body, MAX_BODY_CHARS) or "(empty)"}

COMMIT MESSAGES:
{truncate(commit_messages, 500) or "(none)"}

CHANGED FILES:
{files_summary}

DIFF (partial):
{truncate(diff, MAX_DIFF_CHARS)}

SCORING RULES:
- pr_description "poor" only if completely empty or nonsensical
- commit_messages "poor" only when commit subjects are mostly vague
    and not meaningful
"""


# -------- Ollama --------


def invoke_ollama(prompt: str) -> tuple[bool, str, str, dict[str, object]]:
    api_err = ""
    meta: dict[str, object] = {
        "model": OLLAMA_MODEL,
        "path": "api",
        "duration_ms": 0,
        "prompt_chars": len(prompt),
    }
    started = time.monotonic()
    try:
        payload = json.dumps(
            {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0,
                    "num_ctx": 4096,
                    "num_predict": NUM_PREDICT,
                },
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url=f"{OLLAMA_HOST.rstrip('/')}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            envelope = json.loads(resp.read().decode("utf-8"))
            text = envelope.get("response", "")
            meta["duration_ms"] = int((time.monotonic() - started) * 1000)
            meta["eval_count"] = envelope.get("eval_count", 0)
            meta["prompt_eval_count"] = envelope.get("prompt_eval_count", 0)
            if isinstance(text, str) and text.strip():
                return True, text, "", meta
            api_err = f"empty response: {str(envelope)[:150]}"
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        meta["duration_ms"] = int((time.monotonic() - started) * 1000)
        api_err = f"{type(exc).__name__}: {str(exc)[:150]}"

    # CLI fallback
    meta["path"] = "cli"
    started = time.monotonic()
    try:
        completed = subprocess.run(
            ["ollama", "run", OLLAMA_MODEL],
            input=prompt,
            check=False,
            capture_output=True,
            text=True,
        )
        meta["duration_ms"] = int((time.monotonic() - started) * 1000)
        if completed.returncode == 0 and completed.stdout.strip():
            return True, completed.stdout, "", meta
        cli_err = (completed.stderr or "empty output")[:150]
    except OSError as exc:
        meta["duration_ms"] = int((time.monotonic() - started) * 1000)
        cli_err = str(exc)[:150]

    return False, "", f"API: {api_err} | CLI: {cli_err}", meta


# -------- Parsing --------


def extract_json(text: str) -> dict:
    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, re.I):
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError("No valid JSON found in model output")


def normalize_qualitative_section(
    parsed: dict,
    key: str,
    fallback_feedback: str,
) -> dict[str, str]:
    section = parsed.get(key, {})
    score = str(section.get("score", "needs_improvement")).strip().lower()
    feedback = str(section.get("feedback", "")).strip()
    placeholder_phrases = {
        "one concise sentence",
        "n/a",
        "placeholder",
    }

    if score not in {"good", "needs_improvement", "poor"}:
        score = "needs_improvement"
    if not feedback or feedback.lower() in placeholder_phrases:
        feedback = fallback_feedback

    return {"score": score, "feedback": feedback}


# -------- Comment formatting --------


SCORE_EMOJI = {
    "good": "✅",
    "needs_improvement": "⚠️",
    "poor": "❌",
}


def score_line(label: str, score: str) -> str:
    emoji = SCORE_EMOJI.get(score, "ℹ️")
    return f"### {emoji} {label} — {score.replace('_', ' ').title()}"


def format_comment(parsed: dict, ok: bool, error: str) -> str:
    if not ok:
        return (
            f"## {BOT_MARKER}\n\n"
            f"⚠️ Bot could not complete analysis: `{error}`\n\n"
            "*Non-blocking — no action required.*"
        )

    lines = [f"## {BOT_MARKER}\n"]

    section = parsed.get("pr_description", {})
    score = section.get("score", "unknown")
    lines.append(score_line("PR Description", score))
    if fb := section.get("feedback"):
        lines.append(f"> {fb}\n")

    section = parsed.get("commit_messages", {})
    score = section.get("score", "unknown")
    lines.append(score_line("Commit Messages", score))
    if fb := section.get("feedback"):
        lines.append(f"> {fb}\n")

    section = parsed.get("test_gap", {})
    if section.get("detected"):
        lines.append("### ⚠️ Test Gap Detected")
    else:
        lines.append("### ✅ Test Coverage")
    if detail := section.get("details"):
        lines.append(f"> {detail}\n")

    section = parsed.get("contract_change", {})
    if section.get("detected"):
        lines.append("### 📋 Contract Change Detected")
        if summary := section.get("summary"):
            lines.append(f"> {summary}\n")
    else:
        lines.append("### ℹ️ No Contract Changes\n")

    lines.append(
        "---\n*Generated by `llama3.2:1b` · "
        "Heuristic only — not authoritative. "
        "Human review required.*"
    )
    return "\n".join(lines)


def format_comment_v2(
    qualitative: dict,
    deterministic: dict[str, object],
    ok: bool,
    error: str,
    meta: dict[str, object],
) -> str:
    if not ok:
        return (
            f"## {BOT_MARKER}\n\n"
            f"⚠️ Bot could not complete qualitative analysis: `{error}`\n\n"
            "Deterministic checks still ran.\n"
            f"- Test gap: {deterministic['test_gap']['detected']}\n"
            "- Contract change: "
            f"{deterministic['contract_change']['detected']}\n\n"
            "*Non-blocking — no action required.*"
        )

    lines = [f"## {BOT_MARKER}\n"]

    section = qualitative.get("pr_description", {})
    lines.append(score_line("PR Description", section.get("score", "unknown")))
    lines.append(f"> {section.get('feedback', 'No feedback generated.')}\n")

    section = qualitative.get("commit_messages", {})
    commit_score = section.get("score", "unknown")
    lines.append(score_line("Commit Messages", commit_score))
    lines.append(f"> {section.get('feedback', 'No feedback generated.')}\n")

    test_gap = deterministic["test_gap"]
    if test_gap["detected"]:
        lines.append("### ⚠️ Test Gap Detected")
    else:
        lines.append("### ✅ Test Coverage")
    lines.append(f"> {test_gap['details']}\n")

    contract_change = deterministic["contract_change"]
    if contract_change["detected"]:
        lines.append("### 📋 Contract Change Detected")
    else:
        lines.append("### ℹ️ No Contract Changes")
    lines.append(f"> {contract_change['summary']}\n")

    lines.append("### 📊 Run Diagnostics")
    lines.append(f"- Model: {meta.get('model', OLLAMA_MODEL)}")
    lines.append(f"- Path: {meta.get('path', 'unknown')}")
    lines.append(f"- Duration: {meta.get('duration_ms', 0)} ms")
    if meta.get("eval_count"):
        lines.append(f"- Eval tokens: {meta.get('eval_count')}")
    if meta.get("prompt_eval_count"):
        lines.append(f"- Prompt tokens: {meta.get('prompt_eval_count')}")

    lines.append(
        "---\n*Qualitative checks by local Llama model. "
        "Structural checks are deterministic. Human review required.*"
    )
    return "\n".join(lines)


# -------- Main --------

diff = get_pr_diff()
changed_files = get_changed_files()
commit_messages = get_commit_messages()
pr_title, pr_body = get_pr_meta()
deterministic = deterministic_checks(changed_files)

prompt = build_prompt(pr_title, pr_body, diff, changed_files, commit_messages)

ok, result_text, error, meta = invoke_ollama(prompt)

parsed: dict = {}
qualitative = {
    "pr_description": {
        "score": "needs_improvement",
        "feedback": "Model did not provide usable PR description feedback.",
    },
    "commit_messages": {
        "score": "needs_improvement",
        "feedback": "Model did not provide usable commit message feedback.",
    },
}
if ok:
    try:
        parsed = extract_json(result_text)
        qualitative["pr_description"] = normalize_qualitative_section(
            parsed,
            "pr_description",
            (
                "Please add more concrete context about purpose, "
                "scope, and validation."
            ),
        )
        qualitative["commit_messages"] = normalize_qualitative_section(
            parsed,
            "commit_messages",
            "Use explicit commit subjects describing what changed and why.",
        )
    except ValueError:
        ok = False
        error = f"parse failed: {result_text[:200]}"

comment = format_comment_v2(qualitative, deterministic, ok, error, meta)
upsert_pr_comment(comment)
print("PR review bot complete.")
