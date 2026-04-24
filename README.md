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
mosquitto_sub -h localhost -p 1883 -t medtech/vitals/latest -v
```

**From a browser on Windows/macOS** — use the HiveMQ WebSocket client:
1. Open **http://www.hivemq.com/demos/websocket-client/** (use HTTP, not HTTPS —
   browsers block unencrypted WebSocket connections from HTTPS pages).
2. Set Host: `localhost`, Port: `9001`, Path: `/`.
3. Click **Connect** — status turns green.
4. Subscribe to topic `medtech/vitals/latest`.
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
