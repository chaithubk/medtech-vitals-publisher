from __future__ import annotations

import json
import os
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

try:
    request = urllib.request.Request(
        url=f"{ollama_host.rstrip('/')}/api/generate",
        data=json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        body = response.read().decode("utf-8")
except (OSError, urllib.error.URLError, TimeoutError):
    exit_with_policy("Ollama invocation failed.")

try:
    envelope = json.loads(body)
except json.JSONDecodeError:
    preview = output_preview(body)
    exit_with_policy(f"Ollama invocation failed. Preview: {preview}")

result_text = envelope.get("response", "")
if not isinstance(result_text, str) or not result_text.strip():
    preview = output_preview(json.dumps(envelope))
    exit_with_policy(f"Ollama invocation failed. Preview: {preview}")

try:
    parsed = parse_model_json(result_text)
except ValueError:
    preview = output_preview(result_text)
    exit_with_policy(f"Failed to parse output. Preview: {preview}")

validate_model_payload(parsed, diff)
write_updates(parsed["UPDATED_FILES"], diff)

print("Docs updated via Ollama")
