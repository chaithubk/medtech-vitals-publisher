"""Contract pin metadata consistency tests.

Ensures the vendored schema and the structured pin metadata stay aligned.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"
VITALS_DIR = CONTRACTS_DIR / "vitals"
PIN_FILE = VITALS_DIR / "contract-pin.json"
SCHEMA_FILE = VITALS_DIR / "vitals.schema.json"


def _load_json(
    path: Path,
) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_pin_file_exists_and_has_required_fields():
    assert PIN_FILE.exists(), f"Missing contract pin metadata: {PIN_FILE}"
    pin = _load_json(PIN_FILE)

    required = {
        "contract_repo",
        "tag",
        "commit_sha",
        "schema_path",
        "local_schema",
        "synced_at_utc",
        "compatibility",
        "schema_diff",
        "application_code_changes_required",
    }
    missing = required - set(pin.keys())
    assert not missing, f"Missing required metadata fields: {sorted(missing)}"


def test_pin_schema_path_and_local_schema_are_expected():
    pin = _load_json(PIN_FILE)
    assert pin["schema_path"] == "schemas/vitals/vitals.schema.json"
    assert pin["local_schema"] == "contracts/vitals/vitals.schema.json"


def test_schema_file_exists_and_is_valid_json():
    assert SCHEMA_FILE.exists(), f"Missing vendored schema: {SCHEMA_FILE}"
    schema = _load_json(SCHEMA_FILE)
    assert isinstance(
        schema,
        dict,
    )
    assert schema.get("type") == "object"


def test_compatibility_metadata_shape():
    pin = _load_json(PIN_FILE)
    compatibility = pin["compatibility"]
    assert isinstance(
        compatibility,
        dict,
    )
    assert "classification" in compatibility
    assert "breaking" in compatibility
    assert isinstance(
        compatibility["breaking"],
        bool,
    )


def test_schema_diff_metadata_shape():
    pin = _load_json(PIN_FILE)
    schema_diff = pin["schema_diff"]
    assert isinstance(
        schema_diff,
        dict,
    )
    expected = {
        "required_added",
        "required_removed",
        "properties_added",
        "properties_removed",
        "type_changed",
    }
    assert expected.issubset(schema_diff.keys())
    for key in expected:
        assert isinstance(
            schema_diff[key],
            list,
        ), f"Expected list at schema_diff.{key}"
