from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request

from docs_updater_common import exit_with_policy
from docs_updater_common import get_diff
from docs_updater_common import output_preview
from docs_updater_common import parse_model_json
from docs_updater_common import read_docs
from docs_updater_common import validate_model_payload
from docs_updater_common import write_updates

diff = get_diff("OLLAMA_DIFF_COMMITS", default_window=3, max_window=20)
docs_content = read_docs()

prompt = f"""
You are a senior software architect and technical writer.

Analyze the git diff and update all documentation.

TASKS:
1. Understand code changes from diff
2. Identify impacted areas
3. Update documentation accordingly

RULES:
- Only update necessary files
- Preserve useful content
- Be precise
- Do NOT hallucinate
- Respond with JSON only (no markdown code fences, no commentary)

OUTPUT STRICT JSON:
{{
  "CONFIDENCE": 0.0,
  "UPDATED_FILES": {{
    "README.md": "...",
    "docs/file.md": "..."
  }}
}}

DIFF:
{diff}

DOCS:
{docs_content}
"""

model = os.getenv("OLLAMA_MODEL", "llama3")
ollama_host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")


def invoke_ollama_via_api() -> tuple[bool, str, str]:
    try:
        request = urllib.request.Request(
            url=f"{ollama_host.rstrip('/')}/api/generate",
            data=json.dumps(
                {
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0,
                        "num_ctx": 4096,
                        "num_predict": 2048,
                    },
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")
        except Exception:
            detail = str(exc)
        return False, "", f"HTTPError: {output_preview(detail)}"
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return False, "", f"{type(exc).__name__}: {output_preview(str(exc))}"

    try:
        envelope = json.loads(body)
    except json.JSONDecodeError:
        return False, "", f"Invalid JSON envelope: {output_preview(body)}"

    result_text = envelope.get("response", "")
    if not isinstance(result_text, str) or not result_text.strip():
        preview = output_preview(json.dumps(envelope))
        return False, "", f"Missing response field: {preview}"

    return True, result_text, ""


def invoke_ollama_via_cli() -> tuple[bool, str, str]:
    try:
        completed = subprocess.run(
            ["ollama", "run", model],
            input=prompt,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return False, "", f"{type(exc).__name__}: {output_preview(str(exc))}"

    if completed.returncode != 0:
        preview = output_preview(completed.stderr or completed.stdout)
        return False, "", f"exit={completed.returncode}: {preview}"

    if not completed.stdout.strip():
        return False, "", "CLI returned empty output"

    return True, completed.stdout, ""


ok, result_text, api_error = invoke_ollama_via_api()
if not ok:
    print(f"Ollama API path failed, trying CLI fallback: {api_error}")
    ok, result_text, cli_error = invoke_ollama_via_cli()
    if not ok:
        exit_with_policy(
            f"Ollama invocation failed. API: {api_error} | CLI: {cli_error}"
        )

try:
    parsed = parse_model_json(result_text)
except ValueError:
    preview = output_preview(result_text)
    exit_with_policy(f"Failed to parse output. Preview: {preview}")

validate_model_payload(parsed, diff)
write_updates(parsed["UPDATED_FILES"], diff)

print("Docs updated via Ollama")
