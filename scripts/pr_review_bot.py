from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY", "")
PR_NUMBER = os.getenv("PR_NUMBER", "")
BASE_SHA = os.getenv("BASE_SHA", "")

MAX_DIFF_CHARS = 3000
MAX_BODY_CHARS = 800
BOT_MARKER = "🤖 PR Review Bot (Ollama)"


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


def build_prompt(
    title: str,
    body: str,
    diff: str,
    changed_files: list[str],
    commit_messages: str,
) -> str:
    src_changed = any(f.startswith(("src/", "scripts/")) for f in changed_files)
    tests_changed = any(f.startswith("tests/") or "test_" in f for f in changed_files)
    contract_changed = any("contracts/" in f for f in changed_files)
    files_summary = "\n".join(changed_files[:30]) or "(none)"

    return f"""You are a code review assistant analyzing a pull request.

OUTPUT STRICT JSON (no prose, no markdown fences):
{{
  "pr_description": {{
    "score": "good|needs_improvement|poor",
    "feedback": "one concise sentence"
  }},
  "commit_messages": {{
    "score": "good|needs_improvement|poor",
    "feedback": "one concise sentence"
  }},
  "test_gap": {{
    "detected": true or false,
    "details": "one concise sentence"
  }},
  "contract_change": {{
    "detected": true or false,
    "summary": "one concise sentence or N/A"
  }}
}}

PR TITLE: {title}

PR DESCRIPTION:
{truncate(body, MAX_BODY_CHARS) or "(empty)"}

COMMIT MESSAGES:
{truncate(commit_messages, 500) or "(none)"}

CHANGED FILES:
{files_summary}

SOURCE CHANGED: {src_changed}
TESTS CHANGED: {tests_changed}
CONTRACT FILES CHANGED: {contract_changed}

DIFF (partial):
{truncate(diff, MAX_DIFF_CHARS)}

SCORING RULES:
- pr_description "poor" only if completely empty or nonsensical
- test_gap detected=true ONLY when SOURCE CHANGED=True AND TESTS CHANGED=False
- contract_change detected=true ONLY when CONTRACT FILES CHANGED=True
- Keep all feedback under 25 words
"""


# -------- Ollama --------


def invoke_ollama(prompt: str) -> tuple[bool, str, str]:
    api_err = ""
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
                    "num_predict": 512,
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
            if isinstance(text, str) and text.strip():
                return True, text, ""
            api_err = f"empty response: {str(envelope)[:150]}"
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        api_err = f"{type(exc).__name__}: {str(exc)[:150]}"

    # CLI fallback
    try:
        completed = subprocess.run(
            ["ollama", "run", OLLAMA_MODEL],
            input=prompt,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return True, completed.stdout, ""
        cli_err = (completed.stderr or "empty output")[:150]
    except OSError as exc:
        cli_err = str(exc)[:150]

    return False, "", f"API: {api_err} | CLI: {cli_err}"


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


# -------- Main --------

diff = get_pr_diff()
changed_files = get_changed_files()
commit_messages = get_commit_messages()
pr_title, pr_body = get_pr_meta()

prompt = build_prompt(pr_title, pr_body, diff, changed_files, commit_messages)

ok, result_text, error = invoke_ollama(prompt)

parsed: dict = {}
if ok:
    try:
        parsed = extract_json(result_text)
    except ValueError:
        ok = False
        error = f"parse failed: {result_text[:200]}"

comment = format_comment(parsed, ok, error)
upsert_pr_comment(comment)
print("PR review bot complete.")
