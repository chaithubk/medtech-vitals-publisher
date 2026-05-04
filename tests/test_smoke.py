"""Smoke test to ensure CI passes with stub code."""


def test_import():
    """Test that we can import the simulator module."""
    import src.simulator

    assert src.simulator is not None


def test_main_callable():
    """Test that main function exists."""
    from src.simulator import main

    assert callable(main)


def test_v2_modules_importable():
    """Test that v2 schema, progression, and synthea_bridge modules import cleanly."""
    from src.schema import SCHEMA_VERSION, VitalsPayloadV2, build_payload
    from src.progression import ProgressionEngine
    from src.synthea_bridge import SyntheaBridge

    assert SCHEMA_VERSION == "2.0"
    assert callable(build_payload)
    assert callable(ProgressionEngine)
    assert callable(SyntheaBridge)


def test_v2_payload_smoke():
    """Smoke test: build a v2 payload end-to-end."""
    from src.schema import build_payload

    p = build_payload(
        patient_id="P001",
        scenario="sepsis",
        scenario_stage="sepsis_onset",
        timestamp=1_700_000_000_000,
        hr=115.0,
        bp_sys=100.0,
        bp_dia=65.0,
        o2_sat=92.0,
        temperature=38.8,
        respiratory_rate=22.0,
        wbc=14.0,
        lactate=2.2,
        quality="degraded",
        source="simulator",
    )
    d = p.to_dict()
    assert d["version"] == "2.0"
    assert d["patient_id"] == "P001"
    assert isinstance(d["sirs_score"], int)
    assert isinstance(d["qsofa_score"], int)
    assert d["sepsis_stage"] in {"none", "sirs", "sepsis", "septic_shock"}

