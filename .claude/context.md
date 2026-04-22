# Vitals Publisher - Claude AI Context

## Repository Purpose
Single-threaded Python service generating synthetic vital readings.
Publishes to MQTT broker every 10 seconds.
Data source for entire platform (replaces real hardware).

## Tech Stack
- Python 3.11+
- paho-mqtt 1.6.1
- pytest (testing)
- Docker (containerization)

## Key Responsibilities
1. Generate synthetic vitals (HR, BP, O2, Temp, quality)
2. Connect to Mosquitto MQTT broker (localhost:1883, configurable)
3. Support scenarios: healthy, sepsis-onset, critical
4. Publish to medtech/vitals/latest every 10s (JSON)
5. Handle disconnect gracefully (auto-reconnect)
6. CLI: --scenario healthy|sepsis|critical
7. Graceful shutdown on SIGTERM

## MQTT Integration
- Broker: localhost:1883 (configurable via env var MQTT_BROKER)
- Topic: medtech/vitals/latest
- QoS: 1 (at-least-once)
- Frequency: Every 10 seconds
- Payload: JSON (vital readings with timestamp)

## Architecture Constraints
- Single-threaded (no background threads)
- Deterministic (same scenario + seed = same vitals, seed-based RNG)
- Self-contained (no external APIs/databases)
- Graceful error handling (reconnect on broker loss)
- No hardcoded values (all configurable via env vars or config.py)

## Success Criteria (Stage 1)
- [ ] Compiles without errors
- [ ] Connects to MQTT broker
- [ ] Publishes valid vitals every 10s
- [ ] Supports 3+ scenarios (healthy, sepsis, critical)
- [ ] Unit tests >80% coverage
- [ ] Docker image builds and runs
- [ ] CLI: python -m src.simulator --scenario healthy works
- [ ] Reconnects on broker disconnect
- [ ] Graceful shutdown on Ctrl+C

## Do NOT
- Add cloud integrations
- Use external databases
- Implement authentication (save for Stage 3)
- Over-optimize code (keep it simple, readable)
- Use threads (single-threaded only)
- Hardcode any values (use env vars or config)
