# Vitals Publisher

Synthetic patient vitals generator for MedTech edge device platform.

## Overview

Generates realistic physiological vital signs (HR, BP, O2, Temp) and publishes
them to an MQTT broker every 10 seconds. Three clinical scenarios are supported:
`healthy`, `sepsis`, and `critical`.

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

If you use multiple GitHub accounts on the host, that is fine. Keep your alias
setup in WSL if you need it there, but the dev container should not depend on a
host-specific SSH alias.

### 2. Use a canonical Git remote in the container

Inside the container, prefer the standard GitHub remote format:

```bash
git remote set-url origin <git-ssh-url>
```

Do not rely on WSL-only SSH host aliases such as `github-accountA` unless you
also copy the matching SSH config into the container. In this repo that extra
alias is unnecessary.

### 3. Reopen or rebuild the dev container

After your WSL agent is ready, reopen the folder in the container or rebuild the
container so VS Code can forward the SSH agent into the container session.

### 4. Verify Git auth inside the container

Run these checks in the container:

```bash
echo "$SSH_AUTH_SOCK"
ssh-add -l
ssh -T git@github.com
```

Expected result:
- `SSH_AUTH_SOCK` is set.
- `ssh-add -l` lists your key.
- `ssh -T git@github.com` confirms GitHub authentication.

### 5. Verify the local broker and app

Once the container is up, confirm the development services and tests:

```bash
python -m pytest -q
python -m src.simulator --scenario healthy
```

### Notes for multiple GitHub accounts

- Multiple keys on the host are fine as long as the correct key is loaded in the
  WSL agent before the container starts.
- If you need tighter control, load only the account-specific key you want to
  use before opening the container.
- If your host uses SSH aliases to choose between accounts, that host-side alias
  does not automatically exist inside the container.
- The simplest container setup is to use `git@github.com:owner/repo.git` and let
  the forwarded agent provide the right key.

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
  --scenario synthea \
  --synthea-path synthea_data/csv
```

The `manifest.json` file records the exact inputs, git SHA, generation
timestamp, file count, and total data-row count so the dataset is fully
traceable and reproducible.

### Keeping artifacts small

The default population of **10** keeps artifact size under ~1 MB and the job
runtime under 2 minutes. Increase `population` for more representative datasets
when needed (e.g. 1000 for load testing).

## Running the Simulator

```bash
# Default scenario (healthy)
python -m src.simulator --scenario healthy

# Other scenarios
python -m src.simulator --scenario sepsis
python -m src.simulator --scenario critical
```

Environment variables override defaults without changing code:

| Variable   | Default     | Description              |
|------------|-------------|--------------------------|
| MQTT_BROKER | localhost  | Broker hostname or IP    |
| MQTT_PORT  | 1883        | Broker TCP port          |
| SCENARIO   | healthy     | Clinical scenario        |
| LOGLEVEL   | INFO        | Logging verbosity        |

## Verifying Messages

**From inside the container** — subscribe with `mosquitto_sub`:
```bash
# Vitals payload
mosquitto_sub -h localhost -p 1883 -t medtech/vitals/latest -v

# Publisher online/offline status
mosquitto_sub -h localhost -p 1883 -t medtech/vitals/status -v
```

**From a browser on Windows/macOS** — use the HiveMQ WebSocket client:
1. Open **http://www.hivemq.com/demos/websocket-client/** (use HTTP, not HTTPS —
   browsers block unencrypted WebSocket connections from HTTPS pages).
2. Set Host: `localhost`, Port: `9001`, Path: `/`.
3. Click **Connect** — status turns green.
3. Subscribe to topic `medtech/vitals/latest` for vitals or `medtech/vitals/status` for online/offline state.
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
