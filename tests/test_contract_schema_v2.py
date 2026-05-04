"""Contract compliance test.

Validates that the v2 payload produced by the current production code path
conforms to the vendored telemetry contract schema in
``contracts/vitals/v2.0.json``.

Any drift between the publisher output and the pinned contract will cause
this test to fail immediately, making schema mismatch visible in CI before
it can affect downstream consumers.
"""

import json
from pathlib import Path

import jsonschema
import pytest

from src.progression import ProgressionEngine
from src.schema import build_payload

# ---------------------------------------------------------------------------
# Schema fixture
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_SCHEMA_PATH = _REPO_ROOT / "contracts" / "vitals" / "v2.0.json"


@pytest.fixture(scope="module")
def vitals_schema() -> dict:
    """Load the vendored v2.0 JSON Schema once per test module."""
    assert _SCHEMA_PATH.exists(), (
        f"Vendored schema not found at {_SCHEMA_PATH}. "
        "Run `python scripts/vendor_telemetry_contract.py` to populate it."
    )
    return json.loads(_SCHEMA_PATH.read_text())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_payload(scenario: str, stage: str | None = None) -> dict:
    """Generate a single v2 payload dict using the real production code path."""
    engine = ProgressionEngine(scenario=scenario, stage=stage, seed=42)
    raw = engine.next_reading(ts=1_700_000_000_000)
    payload = build_payload(
        patient_id="P001",
        scenario=scenario,
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
        sepsis_onset_ts=raw.get("sepsis_onset_ts"),
    )
    return payload.to_dict()


# ---------------------------------------------------------------------------
# Contract compliance tests
# ---------------------------------------------------------------------------


class TestContractCompliance:
    """Validates that generated payloads conform to contracts/vitals/v2.0.json."""

    @pytest.mark.parametrize(
        "scenario,stage",
        [
            ("healthy", None),
            ("sepsis", "pre_sepsis"),
            ("sepsis", "sepsis_onset"),
            ("sepsis", "sepsis"),
            ("sepsis", "septic_shock"),
            ("critical", None),
        ],
    )
    def test_payload_validates_against_contract(self, vitals_schema, scenario, stage):
        """Generated payload for every scenario/stage must pass JSON Schema validation."""
        payload = _generate_payload(scenario, stage)
        try:
            jsonschema.validate(instance=payload, schema=vitals_schema)
        except jsonschema.ValidationError as exc:
            pytest.fail(
                f"Payload for scenario={scenario!r}, stage={stage!r} failed "
                f"contract validation:\n{exc.message}\n\n"
                f"Payload was:\n{json.dumps(payload, indent=2)}"
            )

    def test_all_required_fields_present(self, vitals_schema):
        """Every required field in the schema is present in the generated payload."""
        payload = _generate_payload("sepsis", "sepsis_onset")
        required = set(vitals_schema.get("required", []))
        missing = required - payload.keys()
        assert not missing, f"Missing required fields: {missing}"

    def test_no_extra_fields(self, vitals_schema):
        """No extra fields are emitted beyond those defined in the schema."""
        payload = _generate_payload("healthy")
        defined = set(vitals_schema["properties"].keys())
        extra = payload.keys() - defined
        assert not extra, f"Payload contains fields not defined in contract schema: {extra}"

    def test_version_field_is_2_0(self, vitals_schema):
        """version field must be exactly '2.0' as required by the contract."""
        payload = _generate_payload("healthy")
        jsonschema.validate(instance=payload, schema=vitals_schema)
        assert payload["version"] == "2.0"

    def test_quality_is_string(self, vitals_schema):
        """quality field must be a string (not integer) per contract."""
        payload = _generate_payload("sepsis", "sepsis_onset")
        jsonschema.validate(instance=payload, schema=vitals_schema)
        assert isinstance(payload["quality"], str)

    def test_timestamp_is_integer(self, vitals_schema):
        """timestamp must be an integer (ms-epoch) per contract."""
        payload = _generate_payload("healthy")
        jsonschema.validate(instance=payload, schema=vitals_schema)
        assert isinstance(payload["timestamp"], int)

    def test_sirs_score_is_integer_in_range(self, vitals_schema):
        """sirs_score must be an integer 0-4."""
        payload = _generate_payload("sepsis", "sepsis_onset")
        jsonschema.validate(instance=payload, schema=vitals_schema)
        assert isinstance(payload["sirs_score"], int)
        assert 0 <= payload["sirs_score"] <= 4

    def test_qsofa_score_is_integer_in_range(self, vitals_schema):
        """qsofa_score must be an integer 0-3."""
        payload = _generate_payload("sepsis", "sepsis_onset")
        jsonschema.validate(instance=payload, schema=vitals_schema)
        assert isinstance(payload["qsofa_score"], int)
        assert 0 <= payload["qsofa_score"] <= 3

    def test_sepsis_onset_ts_none_when_not_septic(self, vitals_schema):
        """sepsis_onset_ts is null for healthy scenario (not yet in sepsis)."""
        payload = _generate_payload("healthy")
        jsonschema.validate(instance=payload, schema=vitals_schema)
        assert payload["sepsis_onset_ts"] is None

    def test_sepsis_stage_enum(self, vitals_schema):
        """sepsis_stage value must be one of the enum values in the contract."""
        valid_stages = {"none", "sirs", "sepsis", "septic_shock"}
        for scenario, stage in [
            ("healthy", None),
            ("sepsis", "sepsis"),
            ("critical", None),
        ]:
            payload = _generate_payload(scenario, stage)
            jsonschema.validate(instance=payload, schema=vitals_schema)
            assert payload["sepsis_stage"] in valid_stages
