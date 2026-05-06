"""v2 MQTT payload schema and clinical scoring helpers.

This module defines the canonical v2 payload structure published to
``medtech/vitals/latest`` and provides SIRS/qSOFA scoring functions
required by downstream edge-analytics consumers.

v2 Payload Fields
-----------------
version          : "2.0" – schema version sentinel
patient_id       : str   – patient identifier (e.g. "P001")
scenario         : str   – active clinical scenario label
scenario_stage   : str   – progression stage within scenario
timestamp        : int   – ms-epoch wall-clock time
hr               : float – heart rate (bpm)
bp_sys           : float – systolic blood pressure (mmHg)
bp_dia           : float – diastolic blood pressure (mmHg)
o2_sat           : float – peripheral O2 saturation (%)
temperature      : float – core body temperature (°C)
respiratory_rate : float – breaths per minute
wbc              : float – white blood cell count (×10³/µL), simulated
lactate          : float – serum lactate (mmol/L), simulated
sirs_score       : int   – SIRS criteria met (0-4)
qsofa_score      : int   – qSOFA score (0-3)
sepsis_stage     : str   – 'none' | 'sirs' | 'sepsis' | 'septic_shock'
sepsis_onset_ts  : int | None – ms-epoch when sepsis first detected (None if not yet)
quality          : str   – signal quality indicator ('good' | 'degraded' | 'poor')
source           : str   – data source label
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

SCHEMA_VERSION = "2.0"


# ---------------------------------------------------------------------------
# Clinical scoring
# ---------------------------------------------------------------------------


def calculate_sirs(
    temperature: float,
    hr: float,
    respiratory_rate: float,
    wbc: float,
) -> int:
    """Calculate the number of SIRS criteria met (0-4).

    SIRS (Systemic Inflammatory Response Syndrome) criteria:
    1. Temperature  > 38.0 °C or < 36.0 °C
    2. Heart rate   > 90 bpm
    3. Respiratory rate > 20 breaths/min
    4. WBC > 12,000/µL or < 4,000/µL  (represented here as ×10³/µL, so >12 or <4)

    Args:
        temperature: Core body temperature in °C.
        hr: Heart rate in beats per minute.
        respiratory_rate: Respiratory rate in breaths per minute.
        wbc: White blood cell count in ×10³/µL.

    Returns:
        Number of SIRS criteria met (integer 0–4).
    """
    score = 0
    if temperature > 38.0 or temperature < 36.0:
        score += 1
    if hr > 90:
        score += 1
    if respiratory_rate > 20:
        score += 1
    if wbc > 12.0 or wbc < 4.0:
        score += 1
    return score


def calculate_qsofa(
    respiratory_rate: float,
    bp_sys: float,
    altered_mentation: bool = False,
) -> int:
    """Calculate the quick SOFA (qSOFA) bedside score (0-3).

    qSOFA criteria:
    1. Respiratory rate ≥ 22 breaths/min
    2. Systolic BP ≤ 100 mmHg
    3. Altered mental status (GCS < 15) – passed in as a boolean flag

    Args:
        respiratory_rate: Respiratory rate in breaths per minute.
        bp_sys: Systolic blood pressure in mmHg.
        altered_mentation: True when GCS < 15 (altered mental status).

    Returns:
        qSOFA score (integer 0–3).
    """
    score = 0
    if respiratory_rate >= 22:
        score += 1
    if bp_sys <= 100:
        score += 1
    if altered_mentation:
        score += 1
    return score


def classify_sepsis_stage(
    sirs_score: int,
    qsofa_score: int,
    bp_sys: float,
    lactate: float,
) -> str:
    """Classify sepsis stage based on scoring and haemodynamic parameters.

    Classification follows Sepsis-3 / Singer et al. (2016) guidelines:
    - 'septic_shock': qSOFA ≥ 2 AND (bp_sys < 65 mmHg OR lactate > 2 mmol/L)
    - 'sepsis'      : qSOFA ≥ 2
    - 'sirs'        : SIRS score ≥ 2
    - 'none'        : SIRS < 2 and qSOFA < 2

    Args:
        sirs_score: SIRS criteria count (0-4).
        qsofa_score: qSOFA score (0-3).
        bp_sys: Systolic blood pressure in mmHg.
        lactate: Serum lactate in mmol/L.

    Returns:
        Stage string: 'none', 'sirs', 'sepsis', or 'septic_shock'.
    """
    if qsofa_score >= 2 and (bp_sys < 65 or lactate > 2.0):
        return "septic_shock"
    if qsofa_score >= 2:
        return "sepsis"
    if sirs_score >= 2:
        return "sirs"
    return "none"


# ---------------------------------------------------------------------------
# v2 Payload dataclass
# ---------------------------------------------------------------------------


@dataclass
class VitalsPayloadV2:
    """Canonical v2 MQTT vitals payload.

    All fields are required unless annotated ``Optional``.
    Use :meth:`to_dict` to obtain a JSON-serialisable dict for publishing.

    Args:
        patient_id: Patient identifier string.
        scenario: Active clinical scenario label.
        scenario_stage: Progression stage within the scenario.
        timestamp: Wall-clock time as ms-epoch integer.
        hr: Heart rate in bpm.
        bp_sys: Systolic BP in mmHg.
        bp_dia: Diastolic BP in mmHg.
        o2_sat: O2 saturation percent.
        temperature: Core temperature in °C.
        respiratory_rate: Respiratory rate in breaths/min.
        wbc: White blood cell count ×10³/µL (simulated).
        lactate: Serum lactate mmol/L (simulated).
        sirs_score: SIRS criteria count (0-4).
        qsofa_score: qSOFA score (0-3).
        sepsis_stage: Classified stage string.
        sepsis_onset_ts: ms-epoch of sepsis onset, or None.
        quality: Signal quality indicator (e.g. 'good', 'degraded', 'poor').
        source: Data source label.
        version: Schema version, defaults to SCHEMA_VERSION.
    """

    patient_id: str
    scenario: str
    scenario_stage: str
    timestamp: int
    hr: float
    bp_sys: float
    bp_dia: float
    o2_sat: float
    temperature: float
    respiratory_rate: float
    wbc: float
    lactate: float
    sirs_score: int
    qsofa_score: int
    sepsis_stage: str
    sepsis_onset_ts: Optional[int]
    quality: str
    source: str
    version: str = field(default=SCHEMA_VERSION)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dict representation.

        Returns:
            Dict with all payload fields ready for ``json.dumps()``.
        """
        return asdict(self)


def build_payload(
    patient_id: str,
    scenario: str,
    scenario_stage: str,
    timestamp: int,
    hr: float,
    bp_sys: float,
    bp_dia: float,
    o2_sat: float,
    temperature: float,
    respiratory_rate: float,
    wbc: float,
    lactate: float,
    quality: str,
    source: str,
    sepsis_onset_ts: Optional[int] = None,
    altered_mentation: bool = False,
) -> VitalsPayloadV2:
    """Construct a fully-scored v2 payload.

    Computes SIRS, qSOFA, and sepsis stage automatically from the supplied
    vital parameters before wrapping everything in a :class:`VitalsPayloadV2`.

    Args:
        patient_id: Patient identifier string.
        scenario: Active clinical scenario label.
        scenario_stage: Progression stage within the scenario.
        timestamp: Wall-clock time as ms-epoch integer.
        hr: Heart rate in bpm.
        bp_sys: Systolic BP in mmHg.
        bp_dia: Diastolic BP in mmHg.
        o2_sat: O2 saturation percent.
        temperature: Core temperature in °C.
        respiratory_rate: Respiratory rate in breaths/min.
        wbc: White blood cell count ×10³/µL.
        lactate: Serum lactate mmol/L.
        quality: Signal quality indicator string (e.g. 'good', 'degraded', 'poor').
        source: Data source label.
        sepsis_onset_ts: ms-epoch of first sepsis onset, or None.
        altered_mentation: True when GCS < 15.

    Returns:
        Fully populated :class:`VitalsPayloadV2` instance.
    """
    sirs = calculate_sirs(temperature, hr, respiratory_rate, wbc)
    qsofa = calculate_qsofa(respiratory_rate, bp_sys, altered_mentation)
    stage = classify_sepsis_stage(sirs, qsofa, bp_sys, lactate)

    return VitalsPayloadV2(
        version=SCHEMA_VERSION,
        patient_id=patient_id,
        scenario=scenario,
        scenario_stage=scenario_stage,
        timestamp=timestamp,
        hr=hr,
        bp_sys=bp_sys,
        bp_dia=bp_dia,
        o2_sat=o2_sat,
        temperature=temperature,
        respiratory_rate=respiratory_rate,
        wbc=wbc,
        lactate=lactate,
        sirs_score=sirs,
        qsofa_score=qsofa,
        sepsis_stage=stage,
        sepsis_onset_ts=sepsis_onset_ts,
        quality=quality,
        source=source,
    )
