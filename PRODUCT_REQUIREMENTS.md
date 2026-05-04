# Product Requirements: Vitals Publisher v2

## Problem

Medical devices need realistic test data that doesn't require real hardware or
patient data.  Testing a sepsis detection algorithm requires a continuous stream
of vitals that transitions believably through clinical deterioration stages —
healthy baseline → SIRS → sepsis onset → sepsis → septic shock — with
reproducibility for regression testing.

## Solution

A deterministic synthetic vitals generator that:
1. Publishes **v2 MQTT payloads** every 10 seconds (configurable).
2. Supports **multi-stage sepsis progression** driven by a built-in engine.
3. Optionally ingests **Synthea-generated** patient trajectories for
   industry-standard synthetic realism.
4. Embeds **SIRS and qSOFA clinical scores** in every payload so downstream
   consumers (edge-analytics) can test detection algorithms directly.

## Clinical Context

ICU clinicians require continuous vital monitoring.  The vitals publisher
simulates a patient journey through:

| Stage          | Clinical Picture                                            |
|----------------|-------------------------------------------------------------|
| `healthy`      | Normal physiology, no deterioration                         |
| `pre_sepsis`   | Subtle infection signs, SIRS < 2                            |
| `sepsis_onset` | SIRS >= 2, body temperature elevated, HR rising             |
| `sepsis`       | qSOFA >= 2, multi-organ dysfunction beginning               |
| `septic_shock` | Refractory hypotension (bp_sys < 65 mmHg) or lactate > 2 mmol/L  |

This enables realistic testing of the edge-analytics sepsis algorithm without
live patient data.

## MQTT Specifications (v2)

- **Broker:** Mosquitto (localhost:1883 by default; configurable)
- **Topic:** `medtech/vitals/latest`
- **QoS:** 1 (at-least-once delivery)
- **Frequency:** Every 10 seconds (configurable via `PUBLISH_INTERVAL_S` / `--interval`)
- **Payload:** JSON — v2 schema (see below)
- **Schema version sentinel:** `"version": "2.0"` in every message

## v2 Payload Schema

```json
{
  "version": "2.0",
  "patient_id": "P001",
  "scenario": "sepsis",
  "scenario_stage": "sepsis_onset",
  "timestamp": 1712973600000,
  "hr": 118.3,
  "bp_sys": 101.5,
  "bp_dia": 63.2,
  "o2_sat": 91.8,
  "temperature": 39.1,
  "respiratory_rate": 23.4,
  "wbc": 15.2,
  "lactate": 2.7,
  "sirs_score": 3,
  "qsofa_score": 2,
  "sepsis_stage": "septic_shock",
  "sepsis_onset_ts": 1712973480000,
  "quality": 85,
  "source": "simulator"
}
```

### Added in v2 (breaking change vs v1)

| Field             | Reason Added                                               |
|-------------------|------------------------------------------------------------|
| `version`         | Schema versioning for downstream consumers                 |
| `patient_id`      | Multi-patient support; Synthea patient UUID when applicable|
| `scenario`        | High-level scenario label for routing/filtering            |
| `scenario_stage`  | Fine-grained stage within scenario                         |
| `respiratory_rate`| Required for both SIRS criterion 3 and qSOFA criterion 1  |
| `wbc`             | Required for SIRS criterion 4                              |
| `lactate`         | Required for septic shock classification                   |
| `sirs_score`      | Pre-computed; downstream algorithms consume directly       |
| `qsofa_score`     | Pre-computed; downstream algorithms consume directly       |
| `sepsis_stage`    | Pre-classified; enables threshold-free downstream logic    |
| `sepsis_onset_ts` | First sepsis detection time; enables onset-latency metrics |

## Clinical Scoring Definitions

### SIRS (Systemic Inflammatory Response Syndrome)

Each criterion adds 1 point (max 4):
1. Temperature > 38.0 °C or < 36.0 °C
2. Heart rate > 90 bpm
3. Respiratory rate > 20 breaths/min
4. WBC > 12,000/uL or < 4,000/uL (represented as x10^3/uL: >12 or <4)

### qSOFA (quick Sequential Organ Failure Assessment)

Each criterion adds 1 point (max 3):
1. Respiratory rate >= 22 breaths/min
2. Systolic BP <= 100 mmHg
3. Altered mental status (GCS < 15) — Boolean flag

### Sepsis Classification (Sepsis-3, Singer et al. 2016)

| Stage          | Criteria                                                    |
|----------------|-------------------------------------------------------------|
| `septic_shock` | qSOFA >= 2 AND (BP_sys < 65 mmHg OR lactate > 2.0 mmol/L) |
| `sepsis`       | qSOFA >= 2                                                  |
| `sirs`         | SIRS score >= 2                                             |
| `none`         | otherwise                                                   |

## Vital Ranges Per Stage

### healthy

| Vital            | Range            |
|------------------|------------------|
| HR               | 60–95 bpm        |
| BP systolic      | 100–130 mmHg     |
| BP diastolic     | 65–85 mmHg       |
| O2 saturation    | 96–100%          |
| Temperature      | 36.5–37.3°C      |
| Respiratory rate | 12–18 breaths/min|
| WBC              | 4.5–10.0 x10^3/uL|
| Lactate          | 0.5–1.5 mmol/L   |

### pre_sepsis

| Vital            | Range            |
|------------------|------------------|
| HR               | 85–105 bpm       |
| BP systolic      | 110–135 mmHg     |
| BP diastolic     | 65–90 mmHg       |
| O2 saturation    | 93–97%           |
| Temperature      | 37.5–38.5°C      |
| Respiratory rate | 16–22 breaths/min|
| WBC              | 10–15 x10^3/uL   |
| Lactate          | 1.0–2.0 mmol/L   |

### sepsis_onset

| Vital            | Range            |
|------------------|------------------|
| HR               | 100–120 bpm      |
| BP systolic      | 95–115 mmHg      |
| BP diastolic     | 55–75 mmHg       |
| O2 saturation    | 90–94%           |
| Temperature      | 38.5–40.0°C      |
| Respiratory rate | 20–26 breaths/min|
| WBC              | 13–18 x10^3/uL   |
| Lactate          | 1.5–3.0 mmol/L   |

### sepsis

| Vital            | Range            |
|------------------|------------------|
| HR               | 115–135 bpm      |
| BP systolic      | 85–105 mmHg      |
| BP diastolic     | 45–65 mmHg       |
| O2 saturation    | 86–92%           |
| Temperature      | 39.0–40.5°C      |
| Respiratory rate | 24–30 breaths/min|
| WBC              | 15–22 x10^3/uL   |
| Lactate          | 2.5–4.5 mmol/L   |

### septic_shock

| Vital            | Range            |
|------------------|------------------|
| HR               | 130–155 bpm      |
| BP systolic      | 65–88 mmHg       |
| BP diastolic     | 30–50 mmHg       |
| O2 saturation    | 75–88%           |
| Temperature      | 39.5–41.5°C      |
| Respiratory rate | 28–38 breaths/min|
| WBC              | 18–30 x10^3/uL   |
| Lactate          | 4.0–8.0 mmol/L   |

## Synthea Integration

Synthea (https://synthetichealth.github.io/synthea/) generates realistic
synthetic patient records compliant with HL7 FHIR.  The vitals-publisher
bridge reads Synthea CSV observations and maps LOINC codes to v2 payload fields.

### Workflow

1. Generate a cohort with `--exporter.csv.export=true -m sepsis`.
2. Use `SyntheaBridge.list_sepsis_patients()` to identify candidates.
3. Pass `--synthea-path` and `--patient-id` to the publisher CLI.
4. Missing fields (e.g. lactate, not in Synthea's default sepsis module) are
   filled by the built-in progression engine.

## Configuration

All settings configurable via environment variables or CLI flags (CLI wins):

| Variable             | CLI Flag          | Default  | Description                        |
|----------------------|-------------------|----------|------------------------------------|
| `MQTT_BROKER`        | `--broker-host`   | localhost| MQTT broker hostname               |
| `MQTT_PORT`          | `--broker-port`   | 1883     | MQTT broker TCP port               |
| `SCENARIO`           | `--scenario`      | healthy  | Clinical scenario                  |
| `PATIENT_ID`         | `--patient-id`    | P001     | Patient identifier                 |
| `SYNTHEA_DATA_PATH`  | `--synthea-path`  | (empty)  | Synthea CSV output directory       |
| `SCENARIO_STAGE`     | `--stage`         | (empty)  | Starting stage                     |
| `SEED`               | `--seed`          | 42       | RNG seed for deterministic replay  |
| `PUBLISH_INTERVAL_S` | `--interval`      | 10       | Seconds between publishes          |
| `LOGLEVEL`           | —                 | INFO     | Logging verbosity                  |

## Key Features (Stage 2)

1. Generate synthetic vitals via multi-stage progression engine
2. Optional Synthea CSV ingestion for industry-standard trajectories
3. Publish v2 payloads with SIRS/qSOFA pre-computed scores
4. Deterministic replay (same `--seed` = same sequence)
5. All configuration via env vars or CLI — no hardcoded values
6. Graceful reconnect on broker loss
7. Backward-compatible: v1 `ScenarioFactory` preserved for tooling

## Module Architecture

```
src/schema.py          v2 payload dataclass + SIRS/qSOFA scoring
src/progression.py     Multi-stage time-series engine
src/synthea_bridge.py  Synthea CSV/FHIR reader
src/config.py          Env-var configuration
src/simulator.py       MQTT orchestrator + CLI
```
