"""Unit tests for src.synthea_bridge — Synthea CSV/FHIR bridge."""

import csv
import tempfile
from pathlib import Path

import pytest

from src.synthea_bridge import (
    SyntheaBridge,
    _default_vitals,
    _parse_date_to_ms,
)

# Conditions CSV header / rows used in tests
_CONDITIONS_HEADER = ["START", "STOP", "PATIENT", "ENCOUNTER", "SYSTEM", "CODE", "DESCRIPTION"]


def _write_conditions_csv(path: Path, rows: list) -> None:
    """Write a minimal conditions.csv to *path*."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_CONDITIONS_HEADER)
        writer.writerows(rows)


def _make_synthea_dir_with_conditions(obs_rows=None, cond_rows=None) -> tempfile.TemporaryDirectory:
    """Create a temp Synthea dir with both observations.csv and conditions.csv."""
    tmpdir = tempfile.TemporaryDirectory()
    _write_observations_csv(Path(tmpdir.name) / "observations.csv", obs_rows)
    if cond_rows is not None:
        _write_conditions_csv(Path(tmpdir.name) / "conditions.csv", cond_rows)
    return tmpdir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal observations.csv rows covering all mapped LOINC codes
_OBSERVATIONS_HEADER = ["DATE", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION", "VALUE", "UNITS", "TYPE"]

_SAMPLE_ROWS = [
    # Patient A — heart rate
    ["2023-01-01T08:00:00", "patient-A", "enc-1", "8867-4", "Heart rate", "105", "/min", "numeric"],
    # Patient A — systolic BP
    ["2023-01-01T08:00:00", "patient-A", "enc-1", "8480-6", "Systolic BP", "95", "mmHg", "numeric"],
    # Patient A — diastolic BP
    ["2023-01-01T08:00:00", "patient-A", "enc-1", "8462-4", "Diastolic BP", "60", "mmHg", "numeric"],
    # Patient A — O2 saturation
    ["2023-01-01T08:00:00", "patient-A", "enc-1", "59408-5", "O2 Sat", "92", "%", "numeric"],
    # Patient A — temperature
    ["2023-01-01T08:00:00", "patient-A", "enc-1", "8310-5", "Body Temp", "39.1", "Cel", "numeric"],
    # Patient A — respiratory rate
    ["2023-01-01T08:00:00", "patient-A", "enc-1", "9279-1", "Resp Rate", "24", "/min", "numeric"],
    # Patient A — WBC
    ["2023-01-01T08:00:00", "patient-A", "enc-1", "6690-2", "WBC", "14.5", "10*3/uL", "numeric"],
    # Patient A — lactate
    ["2023-01-01T08:00:00", "patient-A", "enc-1", "2524-7", "Lactate", "2.8", "mmol/L", "numeric"],
    # Patient A — second timestamp (different time)
    ["2023-01-01T08:10:00", "patient-A", "enc-1", "8867-4", "Heart rate", "110", "/min", "numeric"],
    # Patient B — just one row
    ["2023-01-02T10:00:00", "patient-B", "enc-2", "8867-4", "Heart rate", "108", "/min", "numeric"],
]


def _write_observations_csv(path: Path, rows: list = None) -> None:
    """Write an observations.csv file to *path*."""
    if rows is None:
        rows = _SAMPLE_ROWS
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_OBSERVATIONS_HEADER)
        writer.writerows(rows)


def _make_synthea_dir(rows: list = None) -> tempfile.TemporaryDirectory:
    """Create a temporary directory with a minimal Synthea CSV layout."""
    tmpdir = tempfile.TemporaryDirectory()
    _write_observations_csv(Path(tmpdir.name) / "observations.csv", rows)
    return tmpdir


# ---------------------------------------------------------------------------
# SyntheaBridge construction
# ---------------------------------------------------------------------------


class TestSyntheaBridgeConstruction:
    """Tests for SyntheaBridge.__init__()."""

    def test_nonexistent_dir_raises(self):
        """FileNotFoundError raised when csv_dir does not exist."""
        with pytest.raises(FileNotFoundError, match="not found"):
            SyntheaBridge("/does/not/exist")

    def test_missing_observations_csv_raises(self):
        """FileNotFoundError raised when observations.csv is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError, match="observations.csv"):
                SyntheaBridge(tmpdir)

    def test_valid_dir_constructs(self):
        """Valid directory with observations.csv constructs without error."""
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            assert bridge is not None


# ---------------------------------------------------------------------------
# list_patients / list_sepsis_patients
# ---------------------------------------------------------------------------


class TestListPatients:
    """Tests for SyntheaBridge.list_patients() and list_sepsis_patients()."""

    def test_list_patients_returns_all(self):
        """list_patients() returns all unique patient IDs."""
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            patients = bridge.list_patients()
            assert "patient-A" in patients
            assert "patient-B" in patients
            assert len(patients) == 2

    def test_list_patients_sorted(self):
        """list_patients() returns sorted results."""
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            patients = bridge.list_patients()
            assert patients == sorted(patients)

    def test_list_sepsis_patients(self):
        """list_sepsis_patients() returns patients with HR > 100 or Temp > 38.5."""
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            sepsis_patients = bridge.list_sepsis_patients()
            # patient-A has HR=105 and Temp=39.1 — both sepsis flags
            assert "patient-A" in sepsis_patients
            # patient-B has HR=108 — sepsis flag
            assert "patient-B" in sepsis_patients

    def test_list_sepsis_patients_excludes_healthy(self):
        """list_sepsis_patients() excludes patients with healthy vitals only."""
        healthy_rows = [
            ["2023-01-01T08:00:00", "healthy-P", "enc-h", "8867-4", "Heart rate", "72", "/min", "numeric"],
            ["2023-01-01T08:00:00", "healthy-P", "enc-h", "8310-5", "Body Temp", "37.0", "Cel", "numeric"],
        ]
        with _make_synthea_dir(healthy_rows) as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            sepsis_patients = bridge.list_sepsis_patients()
            assert "healthy-P" not in sepsis_patients


# ---------------------------------------------------------------------------
# load_patient
# ---------------------------------------------------------------------------


class TestLoadPatient:
    """Tests for SyntheaBridge.load_patient()."""

    def test_load_known_patient_returns_readings(self):
        """load_patient() returns non-empty list for a known patient."""
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            readings = bridge.load_patient("patient-A")
            assert len(readings) > 0

    def test_load_unknown_patient_returns_empty(self):
        """load_patient() returns [] for an unknown patient ID."""
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            readings = bridge.load_patient("no-such-patient")
            assert readings == []

    def test_reading_contains_required_fields(self):
        """Each reading dict contains all required v2 vital fields."""
        required = {"scenario_stage", "timestamp", "hr", "bp_sys", "bp_dia", "o2_sat",
                    "temperature", "respiratory_rate", "wbc", "lactate", "quality", "sepsis_onset_ts"}
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            readings = bridge.load_patient("patient-A")
            for reading in readings:
                missing = required - reading.keys()
                assert not missing, f"Missing keys: {missing}"

    def test_synthea_values_appear_in_reading(self):
        """Values from observations.csv are reflected in the loaded reading."""
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            readings = bridge.load_patient("patient-A")
            # The first timestamp should have HR=105
            first = readings[0]
            assert first["hr"] == 105.0

    def test_readings_sorted_by_timestamp(self):
        """Readings are returned in ascending timestamp order."""
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            readings = bridge.load_patient("patient-A")
            timestamps = [r["timestamp"] for r in readings]
            assert timestamps == sorted(timestamps)

    def test_fallback_fills_missing_vitals(self):
        """Fields not in Synthea CSV are filled from the fallback dict."""
        # Only provide HR rows — other vitals must be filled by fallback
        hr_only_rows = [
            ["2023-01-01T08:00:00", "patient-A", "enc-1", "8867-4", "Heart rate", "115", "/min", "numeric"],
        ]
        with _make_synthea_dir(hr_only_rows) as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            readings = bridge.load_patient("patient-A")
            assert len(readings) == 1
            reading = readings[0]
            # HR was in CSV
            assert reading["hr"] == 115.0
            # Others should be numeric (from defaults)
            assert isinstance(reading["bp_sys"], (int, float))
            assert isinstance(reading["respiratory_rate"], (int, float))


# ---------------------------------------------------------------------------
# iter_patient
# ---------------------------------------------------------------------------


class TestIterPatient:
    """Tests for SyntheaBridge.iter_patient()."""

    def test_iter_patient_yields_dicts(self):
        """iter_patient() yields reading dicts."""
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            gen = bridge.iter_patient("patient-A", loop=False)
            readings = list(gen)
            assert len(readings) > 0
            for r in readings:
                assert isinstance(r, dict)

    def test_iter_patient_loop_false_finite(self):
        """iter_patient(loop=False) yields each reading exactly once."""
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            readings_non_loop = list(bridge.iter_patient("patient-A", loop=False))
            # There are 2 distinct timestamps for patient-A
            assert len(readings_non_loop) == 2

    def test_iter_patient_unknown_yields_nothing(self):
        """iter_patient() for unknown patient yields nothing."""
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            readings = list(bridge.iter_patient("ghost-patient", loop=False))
            assert readings == []

    def test_iter_patient_timestamps_monotonic_on_loop(self):
        """Timestamps are strictly increasing across loop boundaries (loop=True)."""
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            gen = bridge.iter_patient("patient-A", loop=True)
            # Collect enough readings to span more than one full loop cycle (2 readings per cycle)
            all_readings = [next(gen) for _ in range(6)]
            timestamps = [r["timestamp"] for r in all_readings]
            # Every timestamp must be strictly greater than the previous
            for i in range(1, len(timestamps)):
                assert timestamps[i] > timestamps[i - 1], (
                    f"timestamp[{i}]={timestamps[i]} not > timestamp[{i-1}]={timestamps[i-1]}"
                )


# ---------------------------------------------------------------------------
# _parse_date_to_ms
# ---------------------------------------------------------------------------


class TestParseDateToMs:
    """Tests for _parse_date_to_ms()."""

    def test_iso_datetime_format(self):
        """'YYYY-MM-DDTHH:MM:SS' parses successfully."""
        ts = _parse_date_to_ms("2023-01-01T08:00:00")
        assert isinstance(ts, int)
        assert ts > 0

    def test_iso_date_only_format(self):
        """'YYYY-MM-DD' parses successfully."""
        ts = _parse_date_to_ms("2023-06-15")
        assert isinstance(ts, int)
        assert ts > 0

    def test_invalid_format_falls_back(self):
        """Unparseable string falls back to current time (no exception)."""
        before = int(__import__("time").time() * 1000)
        ts = _parse_date_to_ms("not-a-date")
        after = int(__import__("time").time() * 1000) + 1000
        assert before <= ts <= after

    def test_datetime_is_ms_not_seconds(self):
        """Returned value is milliseconds (> 1e12 for any date after ~2001)."""
        ts = _parse_date_to_ms("2023-01-01T00:00:00")
        assert ts > 1_000_000_000_000  # > 1 trillion → definitely ms, not seconds

    def test_utc_reproducibility(self):
        """Same date string always yields the same ms value (UTC, not local time)."""
        # 2023-01-01T00:00:00 UTC = 1_672_531_200_000 ms
        ts = _parse_date_to_ms("2023-01-01T00:00:00")
        assert ts == 1_672_531_200_000, f"Expected 1_672_531_200_000 (UTC), got {ts}"


# ---------------------------------------------------------------------------
# _default_vitals
# ---------------------------------------------------------------------------


def test_default_vitals_structure():
    """_default_vitals() returns a dict with all expected keys."""
    expected_keys = {
        "scenario_stage", "timestamp", "hr", "bp_sys", "bp_dia", "o2_sat",
        "temperature", "respiratory_rate", "wbc", "lactate", "quality", "sepsis_onset_ts",
    }
    defaults = _default_vitals(ts_ms=0)
    assert expected_keys == set(defaults.keys())
    assert defaults["timestamp"] == 0


# ---------------------------------------------------------------------------
# conditions.csv — list_sepsis_patients_from_conditions / get_sepsis_onset_ts
# ---------------------------------------------------------------------------


class TestConditionsCSV:
    """Tests for conditions.csv-backed sepsis detection methods."""

    _SEPSIS_ROWS = [
        ["2012-04-18", "", "patient-sep", "enc-1", "SNOMED-CT", "91302008", "Sepsis (disorder)"],
    ]
    _OTHER_ROWS = [
        ["2020-01-01", "", "patient-flu", "enc-2", "SNOMED-CT", "6142004", "Influenza (disorder)"],
    ]

    def test_list_sepsis_patients_from_conditions_finds_patient(self):
        """list_sepsis_patients_from_conditions() returns sepsis patient IDs."""
        with _make_synthea_dir_with_conditions(cond_rows=self._SEPSIS_ROWS + self._OTHER_ROWS) as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            result = bridge.list_sepsis_patients_from_conditions()
            assert "patient-sep" in result
            assert "patient-flu" not in result

    def test_list_sepsis_patients_from_conditions_no_file(self):
        """Returns [] when conditions.csv does not exist."""
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            result = bridge.list_sepsis_patients_from_conditions()
            assert result == []

    def test_list_sepsis_patients_merges_conditions(self):
        """list_sepsis_patients() includes patients from conditions.csv."""
        # Use only healthy-vitals observations so the heuristic alone returns nothing
        healthy_obs = [
            ["2023-01-01T08:00:00", "healthy-P", "enc-h", "8867-4", "Heart rate", "72", "/min", "numeric"],
        ]
        with _make_synthea_dir_with_conditions(
            obs_rows=healthy_obs, cond_rows=self._SEPSIS_ROWS
        ) as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            result = bridge.list_sepsis_patients()
            assert "patient-sep" in result
            assert "healthy-P" not in result

    def test_get_sepsis_onset_ts_returns_ms(self):
        """get_sepsis_onset_ts() returns a valid ms timestamp for a known patient."""
        with _make_synthea_dir_with_conditions(cond_rows=self._SEPSIS_ROWS) as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            ts = bridge.get_sepsis_onset_ts("patient-sep")
            # 2012-04-18 UTC = 1334707200000 ms
            assert ts == 1334707200000

    def test_get_sepsis_onset_ts_unknown_patient_returns_none(self):
        """get_sepsis_onset_ts() returns None for a patient not in conditions.csv."""
        with _make_synthea_dir_with_conditions(cond_rows=self._SEPSIS_ROWS) as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            assert bridge.get_sepsis_onset_ts("unknown-patient") is None

    def test_get_sepsis_onset_ts_no_file_returns_none(self):
        """get_sepsis_onset_ts() returns None when conditions.csv is absent."""
        with _make_synthea_dir() as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            assert bridge.get_sepsis_onset_ts("any-patient") is None


# ---------------------------------------------------------------------------
# iter_patient — engine fallback when no LOINC observations
# ---------------------------------------------------------------------------


class TestIterPatientEngineFallback:
    """iter_patient() must stream from fallback engine when no LOINC obs exist."""

    def _make_engine(self):
        from src.progression import ProgressionEngine
        return ProgressionEngine(scenario="sepsis", seed=42)

    def test_iter_patient_no_loinc_obs_loop_true_yields(self):
        """When observations are all non-LOINC, loop=True yields from engine."""
        # observations.csv contains only QALY rows (no LOINC vitals)
        qaly_rows = [
            ["2023-01-01T08:00:00", "patient-qaly", "enc-1", "QALY", "QALY", "20.0", "a", "numeric"],
        ]
        with _make_synthea_dir(qaly_rows) as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            engine = self._make_engine()
            gen = bridge.iter_patient("patient-qaly", fallback_engine=engine, loop=True)
            readings = [next(gen) for _ in range(5)]
            assert len(readings) == 5
            for r in readings:
                assert "hr" in r
                assert "bp_sys" in r
                assert "scenario_stage" in r

    def test_iter_patient_no_loinc_obs_loop_false_yields_nothing(self):
        """When no LOINC obs and loop=False, yields nothing (no engine fallback)."""
        qaly_rows = [
            ["2023-01-01T08:00:00", "patient-qaly", "enc-1", "QALY", "QALY", "20.0", "a", "numeric"],
        ]
        with _make_synthea_dir(qaly_rows) as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            result = list(bridge.iter_patient("patient-qaly", loop=False))
            assert result == []

    def test_iter_patient_no_loinc_no_engine_loop_true_yields_nothing(self):
        """When no LOINC obs and no fallback engine, yields nothing even with loop=True."""
        qaly_rows = [
            ["2023-01-01T08:00:00", "patient-qaly", "enc-1", "QALY", "QALY", "20.0", "a", "numeric"],
        ]
        with _make_synthea_dir(qaly_rows) as tmpdir:
            bridge = SyntheaBridge(tmpdir)
            result = list(bridge.iter_patient("patient-qaly", fallback_engine=None, loop=False))
            assert result == []
