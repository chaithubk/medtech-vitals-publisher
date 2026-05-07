"""Runtime contract validation for outbound v2 telemetry payloads.

When strict mode is enabled (``MEDTECH_STRICT_CONTRACT=1``), this module loads
the JSON Schema from the device rootfs (``MEDTECH_VITALS_SCHEMA`` or the default
path ``/usr/share/medtech/contracts/vitals/current.json``) and validates every
outbound payload before it is published.

If strict mode is enabled and:
- the schema file is missing or unreadable, the process exits with code 1.
- the payload does not conform to the schema, the process exits with code 1.

When strict mode is disabled (the default), this module is a no-op.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

import jsonschema

from src import config

logger = logging.getLogger(__name__)


def load_runtime_schema(path: str) -> Dict[str, Any]:
    """Load and parse the JSON Schema from *path*.

    Args:
        path: Filesystem path to the JSON Schema file.

    Returns:
        Parsed schema dict.

    Raises:
        FileNotFoundError: When the file does not exist.
        OSError: When the file cannot be read (permission error, etc.).
        json.JSONDecodeError: When the file content is not valid JSON.
    """
    schema_path = Path(path)
    if not schema_path.exists():
        raise FileNotFoundError(f"Runtime schema not found: {path}")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_payload(payload: Dict[str, Any], schema: Dict[str, Any]) -> None:
    """Validate *payload* against *schema*.

    Args:
        payload: JSON-serialisable dict to validate.
        schema: JSON Schema dict.

    Raises:
        jsonschema.ValidationError: When validation fails.
    """
    jsonschema.validate(instance=payload, schema=schema)


def strict_validate_before_publish(payload: Dict[str, Any]) -> None:
    """Validate *payload* when strict mode is active; hard-fail on any error.

    This is a no-op when ``MEDTECH_STRICT_CONTRACT`` is not ``"1"``.

    When strict mode is enabled:
    - Loads the runtime schema from ``config.VITALS_SCHEMA_PATH``.
    - Validates *payload* against the loaded schema.
    - Calls ``sys.exit(1)`` on any load or validation failure (hard-fail).

    Args:
        payload: Outbound v2 telemetry payload dict (not yet JSON-serialised).
    """
    if not config.STRICT_CONTRACT:
        return

    schema_path = config.VITALS_SCHEMA_PATH
    try:
        schema = load_runtime_schema(schema_path)
    except (FileNotFoundError, OSError) as exc:
        logger.critical(
            "STRICT CONTRACT: schema file missing or unreadable at '%s': %s – aborting",
            schema_path,
            exc,
        )
        sys.exit(1)
    except json.JSONDecodeError as exc:
        logger.critical(
            "STRICT CONTRACT: schema file at '%s' is not valid JSON: %s – aborting",
            schema_path,
            exc,
        )
        sys.exit(1)

    try:
        validate_payload(payload, schema)
    except jsonschema.ValidationError as exc:
        logger.critical(
            "STRICT CONTRACT: outbound payload failed validation: %s – aborting",
            exc.message,
        )
        sys.exit(1)
