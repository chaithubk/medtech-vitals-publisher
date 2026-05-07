"""Runtime contract validation for outbound v2 telemetry payloads.

This module loads the JSON Schema from the device rootfs
(``MEDTECH_VITALS_SCHEMA`` or the default path
``/usr/share/medtech/contracts/vitals/current.json``) and validates every
outbound payload before it is published.

The schema is loaded **once** at startup via :func:`initialize_runtime_schema`
and the resulting dict is stored on the simulator instance.  Every outbound
payload is then validated by :func:`validate_before_publish` before the MQTT
publish call.

Hard-fail behaviour (``sys.exit(1)``):

- Schema file missing, unreadable, or not valid JSON at startup.
- Outbound payload does not conform to the schema.
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


def initialize_runtime_schema() -> Dict[str, Any]:
    """Load the runtime schema at startup; hard-fail if unavailable.

    Reads the schema path from :attr:`src.config.VITALS_SCHEMA_PATH`.
    If the file is missing, unreadable, or malformed, the process exits
    with code 1 (hard-fail).

    Returns:
        Parsed schema dict to be stored and reused for per-publish validation.
    """
    schema_path = config.VITALS_SCHEMA_PATH
    try:
        schema = load_runtime_schema(schema_path)
        logger.info("Runtime schema loaded from '%s'", schema_path)
        return schema
    except FileNotFoundError as exc:
        logger.critical(
            "Runtime schema missing at '%s': %s – aborting",
            schema_path,
            exc,
        )
        sys.exit(1)
    except OSError as exc:
        logger.critical(
            "Runtime schema unreadable at '%s': %s – aborting",
            schema_path,
            exc,
        )
        sys.exit(1)
    except json.JSONDecodeError as exc:
        logger.critical(
            "Runtime schema at '%s' is not valid JSON: %s – aborting",
            schema_path,
            exc,
        )
        sys.exit(1)


def validate_before_publish(payload: Dict[str, Any], schema: Dict[str, Any]) -> None:
    """Validate *payload* against *schema* before publishing; hard-fail on violation.

    Args:
        payload: Outbound v2 telemetry payload dict (not yet JSON-serialised).
        schema: The runtime schema loaded by :func:`initialize_runtime_schema`.
    """
    try:
        validate_payload(payload, schema)
    except jsonschema.ValidationError as exc:
        logger.critical(
            "Outbound payload failed contract validation: %s – aborting",
            exc.message,
        )
        sys.exit(1)
