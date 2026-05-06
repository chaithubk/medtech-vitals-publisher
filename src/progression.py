"""Deterministic multi-stage sepsis progression engine.

The engine models a patient trajectory through clinical stages and produces
time-series vital readings that evolve realistically.  The same ``seed``
always yields the same sequence, enabling reproducible regression tests.

Stages
------
pre_sepsis    : Baseline / normal values with mild drift
sepsis_onset  : First measurable deterioration  (SIRS ≥ 2)
sepsis        : Progressive deterioration (qSOFA ≥ 2)
septic_shock  : Life-threatening haemodynamic failure

Healthy Stage (non-sepsis)
--------------------------
healthy       : Stable normal physiology

Each stage applies a delta per tick to the previous reading, bounded by
physiological limits.
"""

from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

# Each entry: (hr_range, bp_sys_range, bp_dia_range, o2_sat_range,
#              temp_range, rr_range, wbc_range, lactate_range, quality)
_STAGE_PARAMS: Dict[str, Dict[str, Any]] = {
    "healthy": {
        "hr": (60.0, 95.0),
        "bp_sys": (100.0, 130.0),
        "bp_dia": (65.0, 85.0),
        "o2_sat": (96.0, 100.0),
        "temp": (36.5, 37.3),
        "rr": (12.0, 18.0),
        "wbc": (4.5, 10.0),
        "lactate": (0.5, 1.5),
        "quality": "good",
    },
    "pre_sepsis": {
        "hr": (85.0, 105.0),
        "bp_sys": (110.0, 135.0),
        "bp_dia": (65.0, 90.0),
        "o2_sat": (93.0, 97.0),
        "temp": (37.5, 38.5),
        "rr": (16.0, 22.0),
        "wbc": (10.0, 15.0),
        "lactate": (1.0, 2.0),
        "quality": "good",
    },
    "sepsis_onset": {
        "hr": (100.0, 120.0),
        "bp_sys": (95.0, 115.0),
        "bp_dia": (55.0, 75.0),
        "o2_sat": (90.0, 94.0),
        "temp": (38.5, 40.0),
        "rr": (20.0, 26.0),
        "wbc": (13.0, 18.0),
        "lactate": (1.5, 3.0),
        "quality": "degraded",
    },
    "sepsis": {
        "hr": (115.0, 135.0),
        "bp_sys": (85.0, 105.0),
        "bp_dia": (45.0, 65.0),
        "o2_sat": (86.0, 92.0),
        "temp": (39.0, 40.5),
        "rr": (24.0, 30.0),
        "wbc": (15.0, 22.0),
        "lactate": (2.5, 4.5),
        "quality": "degraded",
    },
    "septic_shock": {
        "hr": (130.0, 155.0),
        "bp_sys": (65.0, 88.0),
        "bp_dia": (30.0, 50.0),
        "o2_sat": (75.0, 88.0),
        "temp": (39.5, 41.5),
        "rr": (28.0, 38.0),
        "wbc": (18.0, 30.0),
        "lactate": (4.0, 8.0),
        "quality": "poor",
    },
}

_VALID_STAGES = list(_STAGE_PARAMS.keys())

# Default progression order for sepsis scenario
_SEPSIS_PROGRESSION: List[Tuple[str, int]] = [
    ("pre_sepsis", 6),  # 6 ticks at pre-sepsis
    ("sepsis_onset", 8),  # 8 ticks at onset
    ("sepsis", 10),  # 10 ticks at sepsis
    ("septic_shock", -1),  # -1 = repeat indefinitely
]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _sample_in_range(rng: random.Random, lo: float, hi: float) -> float:
    return round(rng.uniform(lo, hi), 1)


def _midpoint(lo: float, hi: float) -> float:
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# ProgressionEngine
# ---------------------------------------------------------------------------


class ProgressionEngine:
    """Generates a deterministic sequence of v2-compatible vital readings.

    The engine maintains internal state (current vitals + stage) and
    advances them on every call to :meth:`next_reading`.

    Args:
        scenario: High-level scenario: 'healthy', 'sepsis', or 'critical'.
        stage: Starting stage; inferred from *scenario* if omitted.
        patient_id: Patient identifier string embedded in each reading.
        seed: Integer seed for fully deterministic replay.
        ticks_per_stage: Optional dict overriding the number of ticks
            spent in each stage before advancing.  ``-1`` means
            "stay in this stage indefinitely".
    """

    def __init__(
        self,
        scenario: str = "healthy",
        stage: Optional[str] = None,
        patient_id: str = "P001",
        seed: int = 42,
        ticks_per_stage: Optional[Dict[str, int]] = None,
    ) -> None:
        """Initialise the progression engine.

        Args:
            scenario: High-level scenario name.
            stage: Explicit starting stage (optional).
            patient_id: Patient identifier.
            seed: RNG seed for deterministic replay.
            ticks_per_stage: Stage-duration overrides.
        """
        valid_scenarios = {"healthy", "sepsis", "critical"}
        if scenario not in valid_scenarios:
            raise ValueError(f"Invalid scenario '{scenario}'. Expected one of: {sorted(valid_scenarios)}")

        self.scenario = scenario
        self.patient_id = patient_id
        # Deterministic RNG is required for reproducible simulation/test replay.
        self._rng = random.Random(seed)  # nosec B311
        self._tick = 0
        self._sepsis_onset_ts: Optional[int] = None

        # Build the stage progression list
        if scenario == "healthy":
            self._progression: List[Tuple[str, int]] = [("healthy", -1)]
        elif scenario == "sepsis":
            if ticks_per_stage:
                self._progression = [(s, ticks_per_stage.get(s, t)) for s, t in _SEPSIS_PROGRESSION]
            else:
                self._progression = list(_SEPSIS_PROGRESSION)
        else:  # critical – jump straight to septic_shock
            self._progression = [("septic_shock", -1)]

        self._prog_idx = 0
        self._stage_tick = 0

        # If an explicit starting stage was requested, seek to it
        if stage is not None:
            if stage not in _VALID_STAGES:
                raise ValueError(f"Invalid stage '{stage}'. Expected one of: {_VALID_STAGES}")
            while self._prog_idx < len(self._progression):
                if self._progression[self._prog_idx][0] == stage:
                    break
                self._prog_idx += 1
            if self._prog_idx >= len(self._progression):
                # Stage not in progression list; append it as indefinite
                self._progression.append((stage, -1))
                self._prog_idx = len(self._progression) - 1

        # Seed the initial vital values to the midpoint of the starting stage
        current_stage = self._current_stage()
        params = _STAGE_PARAMS[current_stage]
        self._hr = _midpoint(*params["hr"])
        self._bp_sys = _midpoint(*params["bp_sys"])
        self._bp_dia = _midpoint(*params["bp_dia"])
        self._o2_sat = _midpoint(*params["o2_sat"])
        self._temp = _midpoint(*params["temp"])
        self._rr = _midpoint(*params["rr"])
        self._wbc = _midpoint(*params["wbc"])
        self._lactate = _midpoint(*params["lactate"])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _current_stage(self) -> str:
        if self._prog_idx >= len(self._progression):
            return self._progression[-1][0]
        return self._progression[self._prog_idx][0]

    def _advance_stage(self) -> None:
        """Advance to the next stage if the current stage's tick budget is spent."""
        if self._prog_idx >= len(self._progression) - 1:
            return  # already on last stage
        _, budget = self._progression[self._prog_idx]
        if budget == -1:
            return  # indefinite stage
        if self._stage_tick >= budget:
            self._prog_idx += 1
            self._stage_tick = 0

    def _drift_toward(
        self,
        current: float,
        lo: float,
        hi: float,
        noise_scale: float = 0.3,
    ) -> float:
        """Apply gentle drift toward target range midpoint + random noise.

        Args:
            current: Current value.
            lo: Target range lower bound.
            hi: Target range upper bound.
            noise_scale: Amplitude of random noise relative to range width.

        Returns:
            New value clamped within [lo - margin, hi + margin].
        """
        target = _midpoint(lo, hi)
        half_width = (hi - lo) / 2.0
        # Move 15 % toward target each tick
        drift = (target - current) * 0.15
        noise = self._rng.gauss(0.0, half_width * noise_scale)
        new_val = current + drift + noise
        # Clamp with a 20 % margin outside the target range
        margin = (hi - lo) * 0.2
        return round(_clamp(new_val, lo - margin, hi + margin), 1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_stage(self) -> str:
        """The current stage name."""
        return self._current_stage()

    def next_reading(self, ts: Optional[int] = None) -> Dict[str, Any]:
        """Produce the next vital-signs reading and advance internal state.

        Args:
            ts: Optional explicit ms-epoch timestamp.  Uses wall-clock when None.

        Returns:
            Dict compatible with :func:`src.schema.build_payload` kwargs (minus
            patient_id, scenario, source — those are added by the caller).
        """
        self._advance_stage()
        stage = self._current_stage()
        params = _STAGE_PARAMS[stage]

        # Drift each vital toward the target range for this stage
        self._hr = self._drift_toward(self._hr, *params["hr"])
        self._bp_sys = self._drift_toward(self._bp_sys, *params["bp_sys"])
        self._bp_dia = self._drift_toward(self._bp_dia, *params["bp_dia"])
        self._o2_sat = self._drift_toward(self._o2_sat, *params["o2_sat"])
        self._temp = self._drift_toward(self._temp, *params["temp"])
        self._rr = self._drift_toward(self._rr, *params["rr"])
        self._wbc = self._drift_toward(self._wbc, *params["wbc"])
        self._lactate = self._drift_toward(self._lactate, *params["lactate"])

        timestamp = ts if ts is not None else int(time.time() * 1000)

        # Track when sepsis first manifests (qSOFA ≥ 2 proxy: stage ∈ {sepsis, septic_shock})
        if stage in {"sepsis", "septic_shock"} and self._sepsis_onset_ts is None:
            self._sepsis_onset_ts = timestamp

        self._tick += 1
        self._stage_tick += 1

        return {
            "scenario_stage": stage,
            "timestamp": timestamp,
            "hr": self._hr,
            "bp_sys": self._bp_sys,
            "bp_dia": self._bp_dia,
            "o2_sat": self._o2_sat,
            "temperature": self._temp,
            "respiratory_rate": self._rr,
            "wbc": self._wbc,
            "lactate": self._lactate,
            "quality": params["quality"],
            "sepsis_onset_ts": self._sepsis_onset_ts,
        }

    def generate_sequence(
        self, n: int, start_ts: Optional[int] = None, interval_ms: int = 10_000
    ) -> List[Dict[str, Any]]:
        """Generate *n* consecutive readings spaced *interval_ms* apart.

        Useful for offline simulation (e.g. Synthea bridge validation).

        Args:
            n: Number of readings to generate.
            start_ts: Optional starting ms-epoch timestamp.  Uses wall-clock when None.
            interval_ms: Interval between readings in milliseconds (default 10 000).

        Returns:
            List of reading dicts as returned by :meth:`next_reading`.
        """
        ts = start_ts if start_ts is not None else int(time.time() * 1000)
        results = []
        for _ in range(n):
            results.append(self.next_reading(ts=ts))
            ts += interval_ms
        return results
