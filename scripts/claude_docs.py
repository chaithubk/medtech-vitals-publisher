from __future__ import annotations

import os

from anthropic import Anthropic

from docs_updater_common import exit_with_policy
from docs_updater_common import get_diff
from docs_updater_common import output_preview
from docs_updater_common import parse_model_json
from docs_updater_common import read_docs
from docs_updater_common import validate_model_payload
from docs_updater_common import write_updates

api_key = os.getenv("ANTHROPIC_API_KEY")
if not api_key:
    exit_with_policy("ANTHROPIC_API_KEY is not set.")

client = Anthropic(api_key=api_key)
diff = get_diff("CLAUDE_DIFF_COMMITS", default_window=5, max_window=30)
docs_content = read_docs()

prompt = f"""
Act as a senior architect and technical writer.

Perform deep analysis of repository changes.

TASKS:
- Understand architecture impact
- Identify outdated or missing docs
- Improve ALL documentation

RULES:
- Be accurate and structured
- Do not hallucinate
- Update only relevant files
- Respond with JSON only (no markdown code fences, no commentary)

OUTPUT JSON:
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


def main() -> None:
    try:
        response = client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-3-haiku-20240307"),
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        exit_with_policy("Claude invocation failed.")

    text_blocks = []
    for block in response.content or []:
        if getattr(block, "type", None) == "text" and isinstance(
            getattr(block, "text", None), str
        ):
            text_blocks.append(block.text)

    response_text = "\n".join(text_blocks)

    try:
        parsed = parse_model_json(response_text)
    except ValueError:
        preview = output_preview(response_text)
        exit_with_policy(f"Failed to parse output. Preview: {preview}")

    validate_model_payload(parsed, diff)
    write_updates(parsed["UPDATED_FILES"], diff)

    print("Docs updated via Claude")


if __name__ == "__main__":
    main()
