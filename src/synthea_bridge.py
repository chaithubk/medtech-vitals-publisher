"""Synthea bridge: reads Synthea CSV/FHIR output and converts it to v2 vitals.

Synthea (https://synthetichealth.github.io/synthea/) generates realistic
synthetic patient data in FHIR and CSV formats.  This bridge allows the
vitals-publisher to:

1. Read Synthea-generated observation CSV files (``observations.csv``).
2. Extract vital-sign rows for a specific patient / encounter.
3. Normalise the values into the v2 payload field names.
4. Fall back to the :class:`~src.progression.ProgressionEngine` for any
   fields not present in the Synthea export.

Typical Synthea CSV column layout (``observations.csv``)
---------------------------------------------------------
DATE, PATIENT, ENCOUNTER, CODE, DESCRIPTION, VALUE, UNITS, TYPE

Supported LOINC codes mapped to v2 fields
------------------------------------------
8867-4   Heart rate               → hr
8480-6   Systolic BP              → bp_sys
8462-4   Diastolic BP             → bp_dia
59408-5  O2 saturation (pulse ox) → o2_sat
8310-5   Body temperature         → temperature
9279-1   Respiratory rate         → respiratory_rate
6690-2   WBC count                → wbc
2524-7   Lactate                  → lactate

Usage example
-------------
>>> bridge = SyntheaBridge("/path/to/synthea/output/csv")
>>> patient_ids = bridge.list_patients()
>>> frames = bridge.load_patient("some-uuid", scenario="sepsis")
>>> for reading in frames:
...     print(reading)

Generating Synthea output (documented workflow)
-----------------------------------------------
1. Download the Synthea jar from https://github.com/synthetichealth/synthea/releases
2. Run with the sepsis module enabled::

       java -jar synthea-with-dependencies.jar \\
           -s 42 \\
           --exporter.csv.export=true \\
           --exporter.fhir.export=false \\
           -m sepsis \\
           -p 10 \\
           Massachusetts

3. Copy the ``output/csv`` directory to your project (e.g., ``data/synthea/csv``).
4. Set the env var ``SYNTHEA_DATA_PATH=data/synthea/csv`` (or pass ``--synthea-path``).
5. Run the publisher::

       python -m src --scenario sepsis --synthea-path data/synthea/csv

Notes
-----
- If ``SYNTHEA_DATA_PATH`` is not set or no matching patient is found, the
  publisher falls back to the built-in :class:`~src.progression.ProgressionEngine`.
- WBC and lactate are rarely exported by Synthea's default modules; the bridge
  uses the progression engine as a fallback for those fields.
"""

from __future__ import annotations

import csv
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LOINC → v2 field mapping
# ---------------------------------------------------------------------------

_LOINC_MAP: Dict[str, Tuple[str, float, float]] = {
    # code: (field_name, unit_scale_to_standard, fallback_value)
    "8867-4": ("hr", 1.0, 75.0),
    "8480-6": ("bp_sys", 1.0, 120.0),
    "8462-4": ("bp_dia", 1.0, 80.0),
    "59408-5": ("o2_sat", 1.0, 98.0),
    "8310-5": ("temperature", 1.0, 37.0),
    "9279-1": ("respiratory_rate", 1.0, 16.0),
    "6690-2": ("wbc", 1.0, 7.5),       # ×10³/µL
    "2524-7": ("lactate", 1.0, 1.0),   # mmol/L
}

_ALL_V2_VITALS = {"hr", "bp_sys", "bp_dia", "o2_sat", "temperature", "respiratory_rate", "wbc", "lactate"}

# ---------------------------------------------------------------------------
# SyntheaBridge
# ---------------------------------------------------------------------------


class SyntheaBridge:
    """Reads Synthea CSV output and produces v2-compatible vital dicts.

    Args:
        csv_dir: Path to the Synthea ``output/csv`` directory containing at
                 least ``observations.csv`` and ``patients.csv``.
    """

    def __init__(self, csv_dir: str) -> None:
        """Initialise the bridge.

        Args:
            csv_dir: Directory path containing Synthea CSV files.
        """
        self._dir = Path(csv_dir)
        if not self._dir.is_dir():
            raise FileNotFoundError(f"Synthea CSV directory not found: {csv_dir}")

        self._obs_path = self._dir / "observations.csv"
        self._patients_path = self._dir / "patients.csv"

        if not self._obs_path.exists():
            raise FileNotFoundError(f"observations.csv not found in {csv_dir}")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def list_patients(self) -> List[str]:
        """Return all unique patient IDs present in ``observations.csv``.

        Returns:
            Sorted list of patient UUID strings.
        """
        patients: set = set()
        with open(self._obs_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                pid = row.get("PATIENT", "").strip()
                if pid:
                    patients.add(pid)
        return sorted(patients)

    def list_sepsis_patients(self) -> List[str]:
        """Return patient IDs that have at least one sepsis-related LOINC observation.

        Heuristic: a patient with HR > 100 or Temp > 38.5 is flagged as a
        potential sepsis patient.

        Returns:
            Sorted list of patient UUID strings.
        """
        candidates: set = set()
        with open(self._obs_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                code = row.get("CODE", "").strip()
                pid = row.get("PATIENT", "").strip()
                raw_val = row.get("VALUE", "").strip()
                if not pid or not raw_val:
                    continue
                try:
                    value = float(raw_val)
                except ValueError:
                    continue
                if code == "8867-4" and value > 100:
                    candidates.add(pid)
                elif code == "8310-5" and value > 38.5:
                    candidates.add(pid)
        return sorted(candidates)

    def load_patient(
        self,
        patient_id: str,
        scenario: str = "sepsis",
        fallback_engine: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Load all vital observations for a patient and return as v2-compatible dicts.

        Rows are grouped by encounter then by timestamp.  Missing v2 fields are
        filled in from *fallback_engine* (a
        :class:`~src.progression.ProgressionEngine`) if provided, or set to
        sensible defaults.

        Args:
            patient_id: Synthea patient UUID or readable ID.
            scenario: Scenario label embedded in the output dicts.
            fallback_engine: Optional ProgressionEngine used to fill missing fields.

        Returns:
            List of vital dicts sorted by timestamp ascending.
        """
        raw_rows = self._read_patient_observations(patient_id)
        if not raw_rows:
            logger.warning(
                "No observations found for requested patient in %s", self._obs_path
            )
            return []

        # Group by timestamp
        grouped: Dict[int, Dict[str, float]] = {}
        for ts_ms, field, value in raw_rows:
            bucket = grouped.setdefault(ts_ms, {})
            bucket[field] = value

        results: List[Dict[str, Any]] = []
        for ts_ms in sorted(grouped.keys()):
            obs = grouped[ts_ms]

            # Fill missing vitals from fallback engine or defaults
            if fallback_engine is not None:
                fb = fallback_engine.next_reading(ts=ts_ms)
            else:
                fb = _default_vitals(ts_ms)

            reading: Dict[str, Any] = {
                "scenario_stage": fb.get("scenario_stage", "pre_sepsis"),
                "timestamp": ts_ms,
                "hr": obs.get("hr", fb["hr"]),
                "bp_sys": obs.get("bp_sys", fb["bp_sys"]),
                "bp_dia": obs.get("bp_dia", fb["bp_dia"]),
                "o2_sat": obs.get("o2_sat", fb["o2_sat"]),
                "temperature": obs.get("temperature", fb["temperature"]),
                "respiratory_rate": obs.get("respiratory_rate", fb["respiratory_rate"]),
                "wbc": obs.get("wbc", fb["wbc"]),
                "lactate": obs.get("lactate", fb["lactate"]),
                "quality": 88,
                "sepsis_onset_ts": fb.get("sepsis_onset_ts"),
            }
            results.append(reading)

        return results

    def iter_patient(
        self,
        patient_id: str,
        scenario: str = "sepsis",
        fallback_engine: Optional[Any] = None,
        loop: bool = True,
    ) -> Iterator[Dict[str, Any]]:
        """Yield vital readings for a patient, optionally looping indefinitely.

        Args:
            patient_id: Synthea patient UUID.
            scenario: Scenario label for the readings.
            fallback_engine: Optional ProgressionEngine for missing fields.
            loop: When True, repeat the sequence indefinitely (for live publishing).

        Yields:
            Vital reading dicts (same structure as :meth:`load_patient` output).
        """
        readings = self.load_patient(patient_id, scenario=scenario, fallback_engine=fallback_engine)
        if not readings:
            logger.warning("No readings for requested patient; yielding nothing.")
            return

        now_ms = int(time.time() * 1000)
        # Re-anchor timestamps to current wall-clock, preserving intervals
        if readings:
            base = readings[0]["timestamp"]
            readings = [
                {**r, "timestamp": now_ms + (r["timestamp"] - base)}
                for r in readings
            ]

        while True:
            yield from readings
            if not loop:
                break

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_patient_observations(
        self, patient_id: str
    ) -> List[Tuple[int, str, float]]:
        """Parse observations.csv and return (timestamp_ms, field, value) tuples.

        Args:
            patient_id: Synthea patient UUID.

        Returns:
            List of (timestamp_ms, field_name, value) tuples.
        """
        rows: List[Tuple[int, str, float]] = []

        with open(self._obs_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if row.get("PATIENT", "").strip() != patient_id:
                    continue
                code = row.get("CODE", "").strip()
                if code not in _LOINC_MAP:
                    continue
                raw_val = row.get("VALUE", "").strip()
                if not raw_val:
                    continue
                try:
                    value = float(raw_val)
                except ValueError:
                    continue

                date_str = row.get("DATE", "").strip()
                ts_ms = _parse_date_to_ms(date_str)

                field_name, scale, _ = _LOINC_MAP[code]
                rows.append((ts_ms, field_name, round(value * scale, 1)))

        return rows


# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------


def _parse_date_to_ms(date_str: str) -> int:
    """Convert a Synthea DATE string to ms-epoch.

    Synthea uses ISO 8601 format: ``YYYY-MM-DDTHH:MM:SS`` or ``YYYY-MM-DD``.

    Args:
        date_str: ISO 8601 date/datetime string.

    Returns:
        Integer milliseconds since epoch.
    """
    import datetime

    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(date_str, fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    # Fallback: use current time
    logger.warning("Cannot parse date '%s'; using current time", date_str)
    return int(time.time() * 1000)


def _default_vitals(ts_ms: int) -> Dict[str, Any]:
    """Return a minimal set of default vital values for missing observations.

    Args:
        ts_ms: Timestamp for the reading.

    Returns:
        Dict with all v2 vital fields set to healthy defaults.
    """
    return {
        "scenario_stage": "pre_sepsis",
        "timestamp": ts_ms,
        "hr": 80.0,
        "bp_sys": 120.0,
        "bp_dia": 75.0,
        "o2_sat": 97.0,
        "temperature": 37.0,
        "respiratory_rate": 16.0,
        "wbc": 7.5,
        "lactate": 1.0,
        "quality": 88,
        "sepsis_onset_ts": None,
    }
