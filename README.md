# Vitals Publisher

Synthetic patient vitals generator for MedTech edge device platform.

## Overview

Generates realistic physiological vital signs and publishes them to an MQTT
broker every 10 seconds (configurable).  Three high-level clinical scenarios
are supported: `healthy`, `sepsis`, and `critical`.

**v2 highlights:**
- Multi-stage sepsis progression engine (`pre_sepsis → sepsis_onset → sepsis → septic_shock`).
- Synthea-based synthetic patient data bridge (optional).
- v2 MQTT payload with `respiratory_rate`, SIRS/qSOFA scores, sepsis stage
  metadata, and `version: "2.0"`.
- Deterministic replay — same `--seed` always produces the same sequence.

## Tech Stack

- Python 3.11+
- paho-mqtt (MQTT client)
- Mosquitto 2.x (MQTT broker, bundled in dev container)
- pytest (testing)
- Docker / VS Code Dev Containers

## Dev Container Setup

The dev container (`Dockerfile` + `.devcontainer/devcontainer.json`) provides a
fully self-contained environment — no external broker or manual install needed.

What happens automatically on container start:
- Mosquitto is installed in the image during build.
- On every container start, `postStartCommand` writes a Mosquitto config and
  launches the broker:
  - Port **1883** — standard MQTT (TCP), used by the simulator.
  - Port **9001** — MQTT over WebSocket, used for browser-based inspection.
- Both ports are forwarded to your host by VS Code so you can reach them on
  `localhost` from Windows/macOS.

## Clean Setup Checklist

Use this flow when opening the repo in a fresh dev container.

### 1. Prepare Git authentication in WSL

The container should use your WSL `ssh-agent`. Your private keys stay in WSL;
the container only uses the forwarded agent for signing SSH requests.

In WSL, make sure an agent is running and your key is loaded:

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519_xyz
ssh-add -l
```

### 2. Use a canonical Git remote in the container

Inside the container, prefer the standard GitHub remote format:

```bash
git remote set-url origin <git-ssh-url>
```

### 3. Reopen or rebuild the dev container

After your WSL agent is ready, reopen the folder in the container or rebuild the
container so VS Code can forward the SSH agent into the container session.

### 4. Verify Git auth inside the container

```bash
echo "$SSH_AUTH_SOCK"
ssh-add -l
ssh -T git@github.com
```

### 5. Verify the local broker and app

```bash
python -m pytest -q
python -m src --scenario healthy
```

## Generating Synthea Datasets (CI Artifact)

The workflow `.github/workflows/generate-synthea-dataset.yml` downloads a
pinned [Synthea](https://github.com/synthetichealth/synthea) release, generates
a synthetic patient population exported as CSV files, and uploads the result as
a downloadable GitHub Actions artifact — no local Java or Synthea install
required.

### Triggering the workflow

1. Go to **Actions → Generate Synthea Dataset** in the GitHub UI.
2. Click **Run workflow** and fill in the inputs:

| Input | Default | Description |
|---|---|---|
| `synthea_version` | `3.3.0` | Synthea release tag (pinned for reproducibility) |
| `module` | `sepsis` | Clinical module to simulate |
| `population` | `10` | Number of synthetic patients |
| `seed` | `42` | Random seed (same seed → same patients) |
| `state` | `Massachusetts` | US state for demographic data |
| `artifact_name` | _(auto)_ | Override artifact name; auto-computed as `synthea-<module>-p<pop>-s<seed>-v<version>` |

3. When the run completes, download the **zip artifact** (CSV tables) and the
   **manifest artifact** (`manifest.json`) from the run's **Artifacts** section.

### Using the downloaded dataset

Unzip the artifact, then point the vitals publisher at the CSV directory with
`--synthea-path`:

```bash
unzip synthea-sepsis-p10-s42-v3.3.0.zip -d synthea_data/

python -m src.simulator \
  --scenario sepsis \
  --synthea-path synthea_data/csv
```

The `manifest.json` file records the exact inputs, git SHA, generation
timestamp, file count, and total data-row count so the dataset is fully
traceable and reproducible.

### Demo Dataset In This Repo

The checked-in demo dataset under `data/synthea/demo/csv` is intentionally
trimmed for fast local runs and tests. It currently keeps only:

- `observations.csv`
- `conditions.csv`
- `patients.csv`

Current improvements in the demo data:

- Seeded LOINC vital-sign rows for the sepsis patient
  (`79590754-4679-dafd-8aab-103706580fff`).
- `conditions.csv` still contains SNOMED-CT `91302008` for sepsis onset.
- `manifest.json` reflects the trimmed file count and row totals.

### `.env.demo` (What It Is For)

`.env.demo` is a convenience env preset for running the repo demo quickly with
the bundled dataset and known sepsis patient. It is optional and not required
for runtime.

Load it and run:

```bash
set -a; . ./.env.demo; set +a
python -m src --scenario sepsis
```

Equivalent explicit CLI command (without `.env.demo`):

```bash
python -m src \
  --scenario sepsis \
  --synthea-path data/synthea/demo/csv \
  --patient-id 79590754-4679-dafd-8aab-103706580fff \
  --seed 42 \
  --interval 5
```

### `.env.demo` In CI/CD

Current GitHub Actions workflows do **not** load or source `.env.demo`.

- CI tests/lint/build: `.github/workflows/ci.yml`
- Docker PR build: `.github/workflows/docker-build.yml`
- Dataset generator: `.github/workflows/generate-synthea-dataset.yml`
- Release pipeline: `.github/workflows/release.yml`

If you want CI to run with this preset, add an explicit step to source
`.env.demo` in the target workflow job.

### Keeping artifacts small

The default population of **10** keeps artifact size under ~1 MB and the job
runtime under 2 minutes. Increase `population` for more representative datasets
when needed (e.g. 1000 for load testing).

## Running the Simulator

```bash
# Default scenario (healthy)
python -m src --scenario healthy

# Sepsis progression scenario
python -m src --scenario sepsis

# Sepsis starting at a specific stage
python -m src --scenario sepsis --stage sepsis_onset

# Set a specific patient ID and seed for reproducibility
python -m src --scenario sepsis --patient-id PAT-007 --seed 42

# Override publish interval (e.g. 5 seconds for faster testing)
python -m src --scenario sepsis --interval 5

# Use Synthea output as data source
python -m src --scenario sepsis --synthea-path data/synthea/csv --patient-id <uuid>
```

### Environment Variables

| Variable             | Default     | Description                                        |
|----------------------|-------------|----------------------------------------------------|
| `MQTT_BROKER`        | `localhost` | Broker hostname or IP                              |
| `MQTT_PORT`          | `1883`      | Broker TCP port                                    |
| `SCENARIO`           | `healthy`   | Clinical scenario (`healthy`, `sepsis`, `critical`)|
| `PATIENT_ID`         | `P001`      | Patient identifier in every v2 payload             |
| `SYNTHEA_DATA_PATH`  | *(empty)*   | Path to Synthea `output/csv` directory             |
| `SCENARIO_STAGE`     | *(empty)*   | Starting stage within scenario                     |
| `SEED`               | `42`        | RNG seed for deterministic replay                  |
| `PUBLISH_INTERVAL_S` | `10`        | Seconds between published readings                 |
| `LOGLEVEL`           | `INFO`      | Logging verbosity                                  |

## v2 MQTT Payload Schema

All messages are published to `medtech/vitals/latest` as JSON.

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

### Field Reference

| Field             | Type        | Description                                                     |
|-------------------|-------------|-----------------------------------------------------------------|
| `version`         | `string`    | Schema version — always `"2.0"` for this release               |
| `patient_id`      | `string`    | Patient identifier                                              |
| `scenario`        | `string`    | High-level scenario: `healthy`, `sepsis`, `critical`            |
| `scenario_stage`  | `string`    | Active progression stage (see stages below)                     |
| `timestamp`       | `integer`   | Wall-clock time in **milliseconds** since Unix epoch            |
| `hr`              | `float`     | Heart rate (bpm)                                                |
| `bp_sys`          | `float`     | Systolic blood pressure (mmHg)                                  |
| `bp_dia`          | `float`     | Diastolic blood pressure (mmHg)                                 |
| `o2_sat`          | `float`     | Peripheral O2 saturation (%)                                    |
| `temperature`     | `float`     | Core body temperature (°C)                                      |
| `respiratory_rate`| `float`     | Respiratory rate (breaths/min)                                  |
| `wbc`             | `float`     | White blood cell count (x10^3/uL) — simulated/estimated         |
| `lactate`         | `float`     | Serum lactate (mmol/L) — simulated/estimated                    |
| `sirs_score`      | `integer`   | SIRS criteria met (0-4)                                         |
| `qsofa_score`     | `integer`   | qSOFA score (0-3)                                               |
| `sepsis_stage`    | `string`    | Classified stage: `none`, `sirs`, `sepsis`, `septic_shock`      |
| `sepsis_onset_ts` | `int/null`  | ms-epoch when sepsis first detected; `null` if not yet          |
| `quality`         | `integer`   | Sensor quality estimate (0-100)                                 |
| `source`          | `string`    | Data source: `"simulator"` (built-in engine) or `"synthea"` (Synthea bridge) |

### Progression Stages

| Stage          | Description                                         |
|----------------|-----------------------------------------------------|
| `healthy`      | Stable normal physiology                            |
| `pre_sepsis`   | Mild drift — SIRS may be < 2                        |
| `sepsis_onset` | Early deterioration — SIRS >= 2 starts here         |
| `sepsis`       | Progressive decline — qSOFA >= 2                    |
| `septic_shock` | Haemodynamic failure — bp_sys < 65 mmHg or lactate > 2 mmol/L |

### Scoring Definitions

**SIRS** (Systemic Inflammatory Response Syndrome) — each criterion adds 1 point:
1. Temperature > 38 °C or < 36 °C
2. Heart rate > 90 bpm
3. Respiratory rate > 20 breaths/min
4. WBC > 12,000/uL or < 4,000/uL

**qSOFA** (quick Sequential Organ Failure Assessment) — each criterion adds 1 point:
1. Respiratory rate >= 22 breaths/min
2. Systolic BP <= 100 mmHg
3. Altered mental status (GCS < 15)

**Sepsis classification** (Sepsis-3, Singer et al. 2016):
- `septic_shock`: qSOFA >= 2 AND (BP_sys < 65 OR lactate > 2.0)
- `sepsis`: qSOFA >= 2
- `sirs`: SIRS score >= 2
- `none`: otherwise

## Synthea Integration

[Synthea](https://synthetichealth.github.io/synthea/) is an open-source
synthetic patient generator maintained by MITRE Corporation.

### Generating a Cohort

1. **Download** the Synthea jar (requires Java 11+):

   ```bash
   wget https://github.com/synthetichealth/synthea/releases/latest/download/synthea-with-dependencies.jar
   ```

2. **Run** with the sepsis module and CSV export enabled:

   ```bash
   java -jar synthea-with-dependencies.jar \
       -s 42 \
       --exporter.csv.export=true \
       --exporter.fhir.export=false \
       -m sepsis \
       -p 50 \
       Massachusetts
   ```

3. **Identify** sepsis patients:

   ```python
   from src.synthea_bridge import SyntheaBridge
   b = SyntheaBridge("output/csv")
   print("Sepsis candidates:", b.list_sepsis_patients())
   ```

4. **Run** the publisher with a selected patient:

   ```bash
   python -m src \
       --scenario sepsis \
       --synthea-path output/csv \
       --patient-id <uuid-from-step-3> \
       --seed 42
   ```

### How the Bridge Works

The `SyntheaBridge` reads `observations.csv` and maps LOINC codes to v2 fields:

| LOINC   | Description             | v2 Field            |
|---------|-------------------------|---------------------|
| 8867-4  | Heart rate              | `hr`                |
| 8480-6  | Systolic BP             | `bp_sys`            |
| 8462-4  | Diastolic BP            | `bp_dia`            |
| 59408-5 | O2 saturation (pulse)   | `o2_sat`            |
| 8310-5  | Body temperature        | `temperature`       |
| 9279-1  | Respiratory rate        | `respiratory_rate`  |
| 6690-2  | WBC count               | `wbc`               |
| 2524-7  | Lactate                 | `lactate`           |

Fields absent from Synthea output (e.g. lactate in default modules) are filled
by the built-in progression engine as a fallback.

## Module Structure

```
src/
├── __init__.py          # Package init, version (2.0.0)
├── __main__.py          # python -m src entry point
├── config.py            # All env-var-driven configuration
├── schema.py            # v2 payload dataclass + SIRS/qSOFA helpers
├── progression.py       # Multi-stage sepsis progression engine
├── synthea_bridge.py    # Synthea CSV reader / FHIR bridge
└── simulator.py         # VitalsSimulator orchestrator + MQTTClient
```

## Verifying Messages

**From inside the container** — subscribe with `mosquitto_sub`:
```bash
# Vitals payload
mosquitto_sub -h localhost -p 1883 -t medtech/vitals/latest -v

# Publisher online/offline status
mosquitto_sub -h localhost -p 1883 -t medtech/vitals/status -v
```

**From a browser on Windows/macOS** — use the HiveMQ WebSocket client:
1. Open **http://www.hivemq.com/demos/websocket-client/**
2. Set Host: `localhost`, Port: `9001`, Path: `/`.
3. Click **Connect** — status turns green.
4. Subscribe to `medtech/vitals/latest`.
5. Run the simulator; messages appear in real time.

**Desktop GUI** — [MQTT Explorer](https://mqtt-explorer.com/) connects directly
to `localhost:1883` (TCP) and shows all topics with live message history.

## Running Tests

```bash
# All tests (no broker required — MQTT is fully mocked)
python -m pytest -q

# With coverage
python -m pytest --cov=src --cov-report=term-missing
```

## Checking the Broker

```bash
# Is Mosquitto running?
ps aux | grep mosquitto | grep -v grep

# What ports is it listening on?
netstat -lntp | grep -E '1883|9001'
```
