"""Tests for strict contract validation (MEDTECH_STRICT_CONTRACT=1).

Covers:
- Default schema path resolution.
- MEDTECH_VITALS_SCHEMA env-var override.
- Hard-fail (sys.exit 1) when strict mode is on and schema file is missing.
- Hard-fail when strict mode is on and payload is invalid JSON Schema.
- No exit when strict mode is on and payload is valid.
- No-op when strict mode is off (default).
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import src.config as cfg_module
from src.contract_validator import (
    load_runtime_schema,
    strict_validate_before_publish,
    validate_payload,
)
from src.progression import ProgressionEngine
from src.schema import build_payload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VENDORED_SCHEMA = Path(__file__).parent.parent / "contracts" / "vitals" / "v2.0.json"


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
        quality=raw["quality"],
        source="simulator",
        sepsis_onset_ts=None,
    ).to_dict()


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


class TestSchemaPathResolution:
    """Verify config resolves the correct schema path from env vars."""

    def test_default_path_is_rootfs_location(self):
        """Without MEDTECH_VITALS_SCHEMA, default is /usr/share/... (rootfs)."""
        env = {k: v for k, v in os.environ.items() if k != "MEDTECH_VITALS_SCHEMA"}
        with patch.dict(os.environ, env, clear=True):
            import importlib

            importlib.reload(cfg_module)
            assert cfg_module.VITALS_SCHEMA_PATH == (
                "/usr/share/medtech/contracts/vitals/current.json"
            )

    def test_env_override_changes_path(self, tmp_path):
        """MEDTECH_VITALS_SCHEMA overrides the default schema path."""
        custom = str(tmp_path / "custom.json")
        with patch.dict(os.environ, {"MEDTECH_VITALS_SCHEMA": custom}):
            import importlib

            importlib.reload(cfg_module)
            assert cfg_module.VITALS_SCHEMA_PATH == custom

    def test_strict_contract_default_off(self):
        """MEDTECH_STRICT_CONTRACT defaults to False (off)."""
        env = {k: v for k, v in os.environ.items() if k != "MEDTECH_STRICT_CONTRACT"}
        with patch.dict(os.environ, env, clear=True):
            import importlib

            importlib.reload(cfg_module)
            assert cfg_module.STRICT_CONTRACT is False

    def test_strict_contract_enabled_by_env(self):
        """MEDTECH_STRICT_CONTRACT=1 enables strict mode."""
        with patch.dict(os.environ, {"MEDTECH_STRICT_CONTRACT": "1"}):
            import importlib

            importlib.reload(cfg_module)
            assert cfg_module.STRICT_CONTRACT is True

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
# strict_validate_before_publish
# ---------------------------------------------------------------------------


class TestStrictValidateBeforePublish:
    """Tests for the strict_validate_before_publish integration function."""

    def test_noop_when_strict_mode_off(self):
        """strict_validate_before_publish is a no-op when STRICT_CONTRACT is False."""
        with patch.object(cfg_module, "STRICT_CONTRACT", False):
            # Even an empty dict passes — no validation happens
            strict_validate_before_publish({})

    def test_valid_payload_does_not_exit(self, tmp_path):
        """No sys.exit when strict mode is on and payload is valid."""
        schema = json.loads(_VENDORED_SCHEMA.read_text())
        schema_file = tmp_path / "schema.json"
        schema_file.write_text(json.dumps(schema), encoding="utf-8")

        payload = _make_valid_payload()
        with (
            patch.object(cfg_module, "STRICT_CONTRACT", True),
            patch.object(cfg_module, "VITALS_SCHEMA_PATH", str(schema_file)),
        ):
            strict_validate_before_publish(payload)  # must not raise or exit

    def test_missing_schema_exits_nonzero(self, tmp_path):
        """sys.exit(1) is called when schema file is missing in strict mode."""
        missing_path = str(tmp_path / "no_such_file.json")
        with (
            patch.object(cfg_module, "STRICT_CONTRACT", True),
            patch.object(cfg_module, "VITALS_SCHEMA_PATH", missing_path),
            pytest.raises(SystemExit) as exc_info,
        ):
            strict_validate_before_publish(_make_valid_payload())
        assert exc_info.value.code == 1

    def test_invalid_payload_exits_nonzero(self, tmp_path):
        """sys.exit(1) is called when payload fails schema validation in strict mode."""
        schema = json.loads(_VENDORED_SCHEMA.read_text())
        schema_file = tmp_path / "schema.json"
        schema_file.write_text(json.dumps(schema), encoding="utf-8")

        bad_payload = {"version": "1.0"}  # wrong version + missing required fields
        with (
            patch.object(cfg_module, "STRICT_CONTRACT", True),
            patch.object(cfg_module, "VITALS_SCHEMA_PATH", str(schema_file)),
            pytest.raises(SystemExit) as exc_info,
        ):
            strict_validate_before_publish(bad_payload)
        assert exc_info.value.code == 1

    def test_unreadable_schema_exits_nonzero(self, tmp_path):
        """sys.exit(1) is called when schema file cannot be read (OSError) in strict mode."""
        if os.getuid() == 0:
            pytest.skip("chmod 0o000 has no effect for root; skip this test when running as root")
        schema_file = tmp_path / "schema.json"
        schema_file.write_text("{}", encoding="utf-8")
        schema_file.chmod(0o000)  # make unreadable

        try:
            with (
                patch.object(cfg_module, "STRICT_CONTRACT", True),
                patch.object(cfg_module, "VITALS_SCHEMA_PATH", str(schema_file)),
                pytest.raises(SystemExit) as exc_info,
            ):
                strict_validate_before_publish(_make_valid_payload())
            assert exc_info.value.code == 1
        finally:
            schema_file.chmod(0o644)  # restore for cleanup

    def test_invalid_json_in_schema_file_exits_nonzero(self, tmp_path):
        """sys.exit(1) is called when schema file contains invalid JSON in strict mode."""
        schema_file = tmp_path / "schema.json"
        schema_file.write_text("not json at all", encoding="utf-8")

        with (
            patch.object(cfg_module, "STRICT_CONTRACT", True),
            patch.object(cfg_module, "VITALS_SCHEMA_PATH", str(schema_file)),
            pytest.raises(SystemExit) as exc_info,
        ):
            strict_validate_before_publish(_make_valid_payload())
        assert exc_info.value.code == 1
