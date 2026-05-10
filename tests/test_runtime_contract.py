"""Tests for always-on runtime contract validation.

Covers:
- Default schema path resolution from config.
- MEDTECH_VITALS_SCHEMA env-var override.
- load_runtime_schema: valid file, missing file, invalid JSON.
- validate_payload: valid payload, invalid payload.
- initialize_runtime_schema: success path, hard-fail on missing/unreadable/malformed schema.
- validate_before_publish: success path, hard-fail on invalid payload.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import src.config as cfg_module
from src.contract_validator import (
    initialize_runtime_schema,
    load_runtime_schema,
    validate_before_publish,
    validate_payload,
)
from src.progression import ProgressionEngine
from src.schema import build_payload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_VENDORED_SCHEMA = Path(__file__).parent.parent / "contracts" / "vitals" / "vitals.schema.json"


def _make_valid_payload() -> dict:
    """Return a payload dict that conforms to the v2.0 schema."""
    engine = ProgressionEngine(scenario="healthy", seed=42)
    raw = engine.next_reading(ts=1_700_000_000_000)
    return build_payload(
        patient_id="P001",
        scenario="healthy",
        scenario_stage=raw["scenario_stage"],
        timestamp=raw["timestamp"],
        hr=raw["hr"],
        bp_sys=raw["bp_sys"],
        bp_dia=raw["bp_dia"],
        o2_sat=raw["o2_sat"],
        temperature=raw["temperature"],
        respiratory_rate=raw["respiratory_rate"],
        wbc=raw["wbc"],
        lactate=raw["lactate"],
        creatinine=raw["creatinine"],
        quality=raw["quality"],
        source="simulator",
        sepsis_onset_ts=None,
        altered_mentation=raw.get("altered_mentation", False),
    ).to_dict()


# ---------------------------------------------------------------------------
# Schema path resolution
# ---------------------------------------------------------------------------


class TestSchemaPathResolution:
    """Verify config resolves the correct schema path from env vars."""

    def test_default_path_is_rootfs_location(self):
        """Without MEDTECH_VITALS_SCHEMA, default is /usr/share/... (rootfs)."""
        env = {k: v for k, v in os.environ.items() if k != "MEDTECH_VITALS_SCHEMA"}
        with patch.dict(os.environ, env, clear=True):
            import importlib

            importlib.reload(cfg_module)
            assert cfg_module.VITALS_SCHEMA_PATH == ("/usr/share/medtech/contracts/vitals/vitals.schema.json")

    def test_env_override_changes_path(self, tmp_path):
        """MEDTECH_VITALS_SCHEMA overrides the default schema path."""
        custom = str(tmp_path / "custom.json")
        with patch.dict(os.environ, {"MEDTECH_VITALS_SCHEMA": custom}):
            import importlib

            importlib.reload(cfg_module)
            assert cfg_module.VITALS_SCHEMA_PATH == custom

    def teardown_method(self):
        """Reload config to restore defaults after each test."""
        import importlib

        importlib.reload(cfg_module)


# ---------------------------------------------------------------------------
# load_runtime_schema
# ---------------------------------------------------------------------------


class TestLoadRuntimeSchema:
    """Tests for the load_runtime_schema helper."""

    def test_loads_valid_schema_file(self, tmp_path):
        """A valid JSON schema file is loaded and returned as a dict."""
        schema = {"type": "object"}
        f = tmp_path / "schema.json"
        f.write_text(json.dumps(schema), encoding="utf-8")
        result = load_runtime_schema(str(f))
        assert result == schema

    def test_missing_file_raises_file_not_found(self, tmp_path):
        """FileNotFoundError is raised when the file does not exist."""
        with pytest.raises(FileNotFoundError, match="Runtime schema not found"):
            load_runtime_schema(str(tmp_path / "nonexistent.json"))

    def test_invalid_json_raises_decode_error(self, tmp_path):
        """json.JSONDecodeError is raised for malformed JSON content."""
        f = tmp_path / "bad.json"
        f.write_text("not valid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_runtime_schema(str(f))


# ---------------------------------------------------------------------------
# validate_payload
# ---------------------------------------------------------------------------


class TestValidatePayload:
    """Tests for the validate_payload helper."""

    def test_valid_payload_passes(self):
        """A payload conforming to the schema raises no error."""
        schema = json.loads(_VENDORED_SCHEMA.read_text())
        payload = _make_valid_payload()
        validate_payload(payload, schema)  # must not raise

    def test_invalid_payload_raises_validation_error(self):
        """A payload missing required fields raises jsonschema.ValidationError."""
        import jsonschema

        schema = json.loads(_VENDORED_SCHEMA.read_text())
        with pytest.raises(jsonschema.ValidationError):
            validate_payload({"version": "2.0"}, schema)


# ---------------------------------------------------------------------------
# initialize_runtime_schema
# ---------------------------------------------------------------------------


class TestInitializeRuntimeSchema:
    """Tests for the initialize_runtime_schema startup function."""

    def test_returns_schema_dict_on_success(self, tmp_path):
        """A valid schema file returns the parsed dict without calling sys.exit."""
        schema = json.loads(_VENDORED_SCHEMA.read_text())
        schema_file = tmp_path / "schema.json"
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        with patch.object(cfg_module, "VITALS_SCHEMA_PATH", str(schema_file)):
            result = initialize_runtime_schema()
        assert result == schema

    def test_missing_schema_exits_nonzero(self, tmp_path):
        """sys.exit(1) is called when the schema file does not exist."""
        missing = str(tmp_path / "no_such_file.json")
        with (
            patch.object(cfg_module, "VITALS_SCHEMA_PATH", missing),
            pytest.raises(SystemExit) as exc_info,
        ):
            initialize_runtime_schema()
        assert exc_info.value.code == 1

    def test_invalid_json_exits_nonzero(self, tmp_path):
        """sys.exit(1) is called when schema file contains invalid JSON."""
        schema_file = tmp_path / "bad.json"
        schema_file.write_text("not json at all", encoding="utf-8")
        with (
            patch.object(cfg_module, "VITALS_SCHEMA_PATH", str(schema_file)),
            pytest.raises(SystemExit) as exc_info,
        ):
            initialize_runtime_schema()
        assert exc_info.value.code == 1

    def test_unreadable_schema_exits_nonzero(self, tmp_path):
        """sys.exit(1) is called when schema file cannot be read (OSError)."""
        schema_file = tmp_path / "schema.json"
        schema_file.write_text("{}", encoding="utf-8")
        # Patch load_runtime_schema to raise OSError: chmod 0o000 is not
        # reliable when the test process runs as root (e.g. in a dev container).
        with (
            patch.object(cfg_module, "VITALS_SCHEMA_PATH", str(schema_file)),
            patch(
                "src.contract_validator.load_runtime_schema",
                side_effect=OSError("Permission denied"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            initialize_runtime_schema()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# validate_before_publish
# ---------------------------------------------------------------------------


class TestValidateBeforePublish:
    """Tests for the validate_before_publish per-publish function."""

    def test_valid_payload_does_not_exit(self):
        """No sys.exit when payload conforms to the vendored schema."""
        schema = json.loads(_VENDORED_SCHEMA.read_text())
        payload = _make_valid_payload()
        validate_before_publish(payload, schema)  # must not raise or exit

    def test_invalid_payload_exits_nonzero(self):
        """sys.exit(1) is called when payload fails schema validation."""
        schema = json.loads(_VENDORED_SCHEMA.read_text())
        bad_payload = {"version": "1.0"}  # wrong version + missing required fields
        with pytest.raises(SystemExit) as exc_info:
            validate_before_publish(bad_payload, schema)
        assert exc_info.value.code == 1

    def test_always_validates_regardless_of_env(self, tmp_path):
        """Validation is always enforced — no opt-in flag required."""
        schema = json.loads(_VENDORED_SCHEMA.read_text())
        bad_payload = {"version": "1.0"}
        # Even with no MEDTECH_STRICT_CONTRACT env var set, validation runs.
        env = {k: v for k, v in os.environ.items() if "STRICT" not in k}
        with (
            patch.dict(os.environ, env, clear=True),
            pytest.raises(SystemExit) as exc_info,
        ):
            validate_before_publish(bad_payload, schema)
        assert exc_info.value.code == 1
