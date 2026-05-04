"""Integration tests for the real Synthea demo dataset.

These tests exercise the actual CSV files under ``data/synthea/demo/csv/``
(synthea-sepsis-p10-s42-v3.3.0).  They are skipped automatically when the
demo data directory is absent so they do not break CI in environments that
have not downloaded the dataset.

Dataset facts verified here:
- Population: 10 patients
- Sepsis patient: 79590754-4679-dafd-8aab-103706580fff
  (Dennise990 Schuster709, F, b.1958, sepsis onset 2012-04-18)
- observations.csv: seeded LOINC vital-sign trajectory for the sepsis patient
- conditions.csv: one SNOMED-CT 91302008 row for the sepsis patient
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from src.progression import ProgressionEngine
from src.synthea_bridge import SyntheaBridge

DEMO_CSV = Path("data/synthea/demo/csv")
DEMO_SEPSIS_PATIENT = "79590754-4679-dafd-8aab-103706580fff"

pytestmark = pytest.mark.skipif(
    not DEMO_CSV.is_dir(),
    reason="Demo dataset not present (data/synthea/demo/csv)",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bridge() -> SyntheaBridge:
    return SyntheaBridge(str(DEMO_CSV))


@pytest.fixture(scope="module")
def engine() -> ProgressionEngine:
    return ProgressionEngine(scenario="sepsis", seed=42)


# ---------------------------------------------------------------------------
# Dataset structure
# ---------------------------------------------------------------------------


class TestDemoCsvStructure:
    """Verify the expected files and row counts in the demo dataset."""

    def test_observations_csv_exists(self):
        assert (DEMO_CSV / "observations.csv").exists()

    def test_patients_csv_exists(self):
        assert (DEMO_CSV / "patients.csv").exists()

    def test_conditions_csv_exists(self):
        assert (DEMO_CSV / "conditions.csv").exists()

    def test_manifest_json_exists(self):
        assert (DEMO_CSV / "manifest.json").exists()

    def test_population_is_ten(self, bridge):
        patients = bridge.list_patients()
        assert len(patients) == 10, f"Expected 10 patients, got {len(patients)}"


# ---------------------------------------------------------------------------
# Sepsis patient detection
# ---------------------------------------------------------------------------


class TestSepsisPatientDetection:
    """The demo dataset has exactly one sepsis patient identifiable via conditions.csv."""

    def test_conditions_csv_identifies_sepsis_patient(self, bridge):
        from_conditions = bridge.list_sepsis_patients_from_conditions()
        assert (
            DEMO_SEPSIS_PATIENT in from_conditions
        ), f"Expected {DEMO_SEPSIS_PATIENT} in conditions-based list, got {from_conditions}"

    def test_list_sepsis_patients_returns_sepsis_patient(self, bridge):
        """list_sepsis_patients() includes the conditions.csv patient even without LOINC vitals."""
        sepsis_list = bridge.list_sepsis_patients()
        assert DEMO_SEPSIS_PATIENT in sepsis_list

    def test_loinc_vitals_exist_in_observations(self, bridge):
        """load_patient() returns the seeded LOINC trajectory for the sepsis patient."""
        readings = bridge.load_patient(DEMO_SEPSIS_PATIENT)
        assert len(readings) == 3, f"Expected 3 grouped readings, got {len(readings)}"
        assert [r["hr"] for r in readings] == [102.0, 118.0, 136.0]

    def test_sepsis_onset_ts_from_conditions(self, bridge):
        """get_sepsis_onset_ts() returns 2012-04-18 UTC for the demo sepsis patient."""
        ts = bridge.get_sepsis_onset_ts(DEMO_SEPSIS_PATIENT)
        assert ts is not None
        # 2012-04-18T00:00:00Z = 1334707200000 ms
        assert ts == 1334707200000, f"Expected 1334707200000 ms, got {ts}"


# ---------------------------------------------------------------------------
# Engine fallback via iter_patient
# ---------------------------------------------------------------------------


class TestIterPatientWithDemoData:
    """iter_patient() streams the seeded demo trajectory and preserves monotonic time."""

    def test_iter_patient_yields_with_engine(self, bridge, engine):
        gen = bridge.iter_patient(DEMO_SEPSIS_PATIENT, fallback_engine=engine, loop=True)
        readings = list(itertools.islice(gen, 10))
        assert len(readings) == 10

    def test_readings_have_correct_fields(self, bridge, engine):
        gen = bridge.iter_patient(DEMO_SEPSIS_PATIENT, fallback_engine=engine, loop=True)
        r = next(gen)
        required = {
            "scenario_stage",
            "timestamp",
            "hr",
            "bp_sys",
            "bp_dia",
            "o2_sat",
            "temperature",
            "respiratory_rate",
            "wbc",
            "lactate",
            "quality",
        }
        missing = required - r.keys()
        assert not missing, f"Missing keys in reading: {missing}"

    def test_readings_have_sepsis_vitals(self, bridge, engine):
        """Readings from the sepsis engine should show sepsis-range values within 24 ticks."""
        gen = bridge.iter_patient(DEMO_SEPSIS_PATIENT, fallback_engine=engine, loop=True)
        readings = list(itertools.islice(gen, 24))
        # At least one reading should be in a sepsis or later stage
        stages = {r["scenario_stage"] for r in readings}
        sepsis_stages = {"pre_sepsis", "sepsis_onset", "sepsis", "septic_shock"}
        assert stages & sepsis_stages, f"No sepsis stages seen, got: {stages}"

    def test_timestamps_are_monotonically_increasing(self, bridge, engine):
        gen = bridge.iter_patient(DEMO_SEPSIS_PATIENT, fallback_engine=engine, loop=True)
        readings = list(itertools.islice(gen, 12))
        timestamps = [r["timestamp"] for r in readings]
        for i in range(1, len(timestamps)):
            assert timestamps[i] > timestamps[i - 1], f"timestamp[{i}]={timestamps[i]} not > [{i-1}]={timestamps[i-1]}"


# ---------------------------------------------------------------------------
# Simulator integration — VitalsSimulator with demo path
# ---------------------------------------------------------------------------


class TestVitalsSimulatorWithDemoData:
    """VitalsSimulator._build_source() should auto-select the sepsis patient."""

    def test_auto_selects_sepsis_patient(self):
        """When patient_id defaults to 'P001', simulator auto-selects from conditions.csv."""
        from src.simulator import VitalsSimulator

        # Construct without MQTT — we only want to verify _build_source logic.
        # Override broker to a dummy host so connect() is never called.
        sim = VitalsSimulator(
            scenario="sepsis",
            broker_host="127.0.0.1",
            broker_port=11883,
            patient_id="P001",  # default; not a valid Synthea UUID
            synthea_path=str(DEMO_CSV),
            seed=42,
        )
        # The simulator should have overridden patient_id to the demo sepsis patient
        assert sim.patient_id == DEMO_SEPSIS_PATIENT
        assert sim._source == "synthea"

    def test_generate_vital_returns_valid_payload(self):
        """_generate_vital() returns a dict with all expected v2 payload keys."""
        from src.simulator import VitalsSimulator

        sim = VitalsSimulator(
            scenario="sepsis",
            broker_host="127.0.0.1",
            broker_port=11883,
            patient_id="P001",
            synthea_path=str(DEMO_CSV),
            seed=42,
        )
        vital = sim._generate_vital()
        for key in (
            "patient_id",
            "scenario",
            "hr",
            "bp_sys",
            "bp_dia",
            "o2_sat",
            "temperature",
            "respiratory_rate",
            "wbc",
            "lactate",
        ):
            assert key in vital, f"Key '{key}' missing from payload"
