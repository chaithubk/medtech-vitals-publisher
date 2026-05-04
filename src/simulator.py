#!/usr/bin/env python3
"""Vitals Simulator - Synthetic vital signs generator (v2).

This module implements:
- VitalsSimulator: Main orchestrator (publishes v2 payloads)
- ScenarioFactory: Legacy v1 vital-sign generation (kept for compatibility)
- MQTTClient: paho-mqtt wrapper with exponential-backoff reconnect

v2 Changes
----------
- Publishes :class:`~src.schema.VitalsPayloadV2` to ``medtech/vitals/latest``.
- Integrates :class:`~src.progression.ProgressionEngine` for multi-stage
  time-series generation.
- Optionally reads from Synthea CSV output via
  :class:`~src.synthea_bridge.SyntheaBridge`.
- New CLI flags: ``--patient-id``, ``--synthea-path``, ``--stage``,
  ``--seed``, ``--interval``.
"""

import argparse
import json
import logging
import os
import random
import time
from typing import Any, Dict, Iterator, Optional

import paho.mqtt.client as mqtt

from src import config
from src.progression import ProgressionEngine
from src.schema import build_payload
from src.synthea_bridge import SyntheaBridge

logger = logging.getLogger(__name__)

_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"


def _running_in_container() -> bool:
    """Best-effort check for containerized runtime."""
    return os.path.exists("/.dockerenv")


# ---------------------------------------------------------------------------
# MQTTClient
# ---------------------------------------------------------------------------


class MQTTClient:
    """paho-mqtt wrapper with exponential-backoff reconnect logic.

    Args:
        broker_host: Hostname or IP of the MQTT broker.
        broker_port: TCP port of the MQTT broker.
        client_id: Optional client identifier; auto-generated when omitted.
    """

    def __init__(self, broker_host: str, broker_port: int, client_id: Optional[str] = None) -> None:
        """Initialise the MQTT client.

        Args:
            broker_host: Hostname or IP of the MQTT broker.
            broker_port: TCP port of the MQTT broker.
            client_id: Optional client identifier; auto-generated when omitted.
        """
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._client_id = client_id or f"vitals-publisher-{int(time.time())}"
        self._connected = False
        self._loop_started = False
        self._client = mqtt.Client(client_id=self._client_id)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        # Last Will: broker publishes "offline" if the client disconnects unexpectedly
        self._client.will_set(config.MQTT_STATUS_TOPIC, payload="offline", qos=1, retain=True)
        # loop_start() is intentionally deferred to connect() to avoid spawning
        # background threads for objects that may never actually connect.

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: int) -> None:
        """paho on_connect callback.

        Args:
            client: paho Client instance.
            userdata: User-defined data (unused).
            flags: Connection flags dict.
            rc: Return code; 0 means success.
        """
        if rc == 0:
            self._connected = True
            logger.info("Connected to MQTT broker %s:%d", self._broker_host, self._broker_port)
        else:
            self._connected = False
            logger.warning("MQTT connection refused, rc=%d", rc)

    def _on_disconnect(self, client: Any, userdata: Any, rc: int) -> None:
        """paho on_disconnect callback.

        Args:
            client: paho Client instance.
            userdata: User-defined data (unused).
            rc: Return code; non-zero means unexpected disconnect.
        """
        self._connected = False
        if rc != 0:
            logger.warning("Unexpected MQTT disconnect, rc=%d", rc)
        else:
            logger.info("MQTT broker disconnected cleanly")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _stop_loop(self) -> None:
        """Stop the paho network loop if it has been started."""
        if self._loop_started:
            self._client.loop_stop()
            self._loop_started = False

    def __del__(self) -> None:
        """Best-effort cleanup – stop the background network loop on GC."""
        try:
            self._stop_loop()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to the MQTT broker with exponential-backoff retries.

        The paho network loop is started on the first call so that the
        on_connect callback can fire.  Subsequent calls reuse the running loop.

        Returns:
            True once the connection is established.
        """
        backoff = 1
        max_backoff = 60
        connect_wait_timeout = 5.0
        connect_wait_interval = 0.1
        if not self._loop_started:
            self._client.loop_start()
            self._loop_started = True
        while True:
            try:
                self._client.connect(self._broker_host, self._broker_port)
                deadline = time.monotonic() + connect_wait_timeout
                while time.monotonic() < deadline:
                    if self._connected:
                        self._client.publish(
                            config.MQTT_STATUS_TOPIC,
                            payload="online",
                            qos=1,
                            retain=True,
                        )
                        return True
                    time.sleep(connect_wait_interval)
                logger.warning(
                    "Connection not confirmed within %.1fs, retrying in %ds",
                    connect_wait_timeout,
                    backoff,
                )
            except (OSError, ConnectionError, TimeoutError) as exc:
                logger.warning("MQTT connect error: %s – retrying in %ds", exc, backoff)
                if self._broker_host in {"localhost", "127.0.0.1"} and _running_in_container():
                    logger.warning(
                        "Container detected and broker is %s. If your broker runs on the host machine, "
                        "set MQTT_BROKER=host.docker.internal (or pass --broker-host).",
                        self._broker_host,
                    )
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    def publish(self, topic: str, payload: str, qos: int = 1) -> bool:
        """Publish a message to the MQTT broker.

        Args:
            topic: MQTT topic string.
            payload: Message payload (UTF-8 string).
            qos: Quality-of-service level (0, 1, or 2).

        Returns:
            True if the message was queued successfully, False otherwise.
        """
        if not self._connected:
            logger.warning("Cannot publish: not connected to broker")
            return False
        result = self._client.publish(topic, payload, qos=qos)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.warning("Publish failed, rc=%d", result.rc)
            return False
        return True

    def publish_status(self, status: str) -> None:
        """Publish a retained status message to the status topic.

        Args:
            status: Status string, typically 'online' or 'offline'.
        """
        if self._connected:
            self._client.publish(config.MQTT_STATUS_TOPIC, payload=status, qos=1, retain=True)
            logger.info("Published status: %s", status)

    def disconnect(self) -> None:
        """Disconnect from the MQTT broker and stop the network loop."""
        try:
            self._client.disconnect()
        finally:
            self._stop_loop()
            self._connected = False
        logger.info("MQTT client disconnected")

    def is_connected(self) -> bool:
        """Return the current connection status.

        Returns:
            True if connected to the broker, False otherwise.
        """
        return self._connected


# ---------------------------------------------------------------------------
# ScenarioFactory
# ---------------------------------------------------------------------------


class ScenarioFactory:
    """Factory for generating deterministic synthetic vital signs by scenario."""

    @staticmethod
    def _generate(scenario: str, rng: random.Random) -> Dict[str, Any]:
        """Generate one vital-signs reading for the given scenario.

        Args:
            scenario: One of 'healthy', 'sepsis', or 'critical'.
            rng: Pre-seeded Random instance for determinism.

        Returns:
            Dict with vital sign fields plus metadata.
        """
        cfg = config.SCENARIOS[scenario]

        hr = round(rng.uniform(*cfg["hr"]), 1)
        bp_sys = round(rng.uniform(*cfg["bp_sys"]), 1)
        bp_dia = round(rng.uniform(*cfg["bp_dia"]), 1)
        o2_sat = round(rng.uniform(*cfg["o2_sat"]), 1)
        temperature = round(rng.uniform(*cfg["temp"]), 1)
        quality: int = cfg["quality"]

        if not cfg["hr"][0] <= hr <= cfg["hr"][1]:
            raise ValueError(f"hr {hr} out of range {cfg['hr']}")
        if not cfg["bp_sys"][0] <= bp_sys <= cfg["bp_sys"][1]:
            raise ValueError(f"bp_sys {bp_sys} out of range {cfg['bp_sys']}")
        if not cfg["bp_dia"][0] <= bp_dia <= cfg["bp_dia"][1]:
            raise ValueError(f"bp_dia {bp_dia} out of range {cfg['bp_dia']}")
        if not cfg["o2_sat"][0] <= o2_sat <= cfg["o2_sat"][1]:
            raise ValueError(f"o2_sat {o2_sat} out of range {cfg['o2_sat']}")
        if not cfg["temp"][0] <= temperature <= cfg["temp"][1]:
            raise ValueError(f"temperature {temperature} out of range {cfg['temp']}")

        return {
            "timestamp": int(time.time() * 1000),
            "hr": hr,
            "bp_sys": bp_sys,
            "bp_dia": bp_dia,
            "o2_sat": o2_sat,
            "temperature": temperature,
            "quality": quality,
            "source": "simulator",
        }

    @staticmethod
    def healthy(seed: Optional[int] = None) -> Dict[str, Any]:
        """Generate healthy-scenario vital signs.

        Args:
            seed: Optional random seed for determinism.

        Returns:
            Dict with healthy vital sign readings.
        """
        return ScenarioFactory._generate("healthy", random.Random(seed))

    @staticmethod
    def sepsis(seed: Optional[int] = None) -> Dict[str, Any]:
        """Generate sepsis-scenario vital signs.

        Args:
            seed: Optional random seed for determinism.

        Returns:
            Dict with sepsis vital sign readings.
        """
        return ScenarioFactory._generate("sepsis", random.Random(seed))

    @staticmethod
    def critical(seed: Optional[int] = None) -> Dict[str, Any]:
        """Generate critical-scenario vital signs.

        Args:
            seed: Optional random seed for determinism.

        Returns:
            Dict with critical vital sign readings.
        """
        return ScenarioFactory._generate("critical", random.Random(seed))


# ---------------------------------------------------------------------------
# VitalsSimulator
# ---------------------------------------------------------------------------


class VitalsSimulator:
    """Main orchestrator: generates v2 synthetic vitals and publishes them via MQTT.

    Data source priority (highest to lowest):
    1. Synthea bridge (if ``synthea_path`` is provided and patient found).
    2. :class:`~src.progression.ProgressionEngine` (default).

    Args:
        scenario: Clinical scenario ('healthy', 'sepsis', or 'critical').
        broker_host: MQTT broker hostname or IP.
        broker_port: MQTT broker TCP port.
        seed: Random seed for deterministic vital generation.
        patient_id: Patient identifier embedded in every payload.
        synthea_path: Optional path to Synthea ``output/csv`` directory.
        stage: Optional explicit starting stage within the scenario.
        publish_interval_s: Seconds between published readings.
    """

    def __init__(
        self,
        scenario: str = "healthy",
        broker_host: str = config.MQTT_BROKER,
        broker_port: int = config.MQTT_PORT,
        seed: int = config.SEED,
        patient_id: str = config.PATIENT_ID,
        synthea_path: str = "",
        stage: str = "",
        publish_interval_s: int = config.PUBLISH_INTERVAL_S,
    ) -> None:
        """Initialise the simulator.

        Args:
            scenario: Clinical scenario ('healthy', 'sepsis', or 'critical').
            broker_host: MQTT broker hostname or IP.
            broker_port: MQTT broker TCP port.
            seed: Random seed for deterministic vital generation.
            patient_id: Patient identifier embedded in every payload.
            synthea_path: Optional path to Synthea ``output/csv`` directory.
            stage: Optional explicit starting stage within the scenario.
            publish_interval_s: Seconds between published readings.
        """
        if scenario not in config.SCENARIOS:
            valid = ", ".join(config.SCENARIOS.keys())
            raise ValueError(f"Invalid scenario '{scenario}'. Expected one of: {valid}")
        self.scenario = scenario
        self.seed = seed
        self.patient_id = patient_id
        self.publish_interval_s = publish_interval_s
        self.config = config
        self._running = False
        self._publish_count = 0
        self._connect_count = 0
        # Legacy RNG kept so ScenarioFactory static methods still work in tests
        self._rng = random.Random(seed)
        self.mqtt_client: MQTTClient = MQTTClient(broker_host=broker_host, broker_port=broker_port)

        # Source label embedded in every published payload: "simulator" by default,
        # overridden to "synthea" when the Synthea bridge is active.
        self._source: str = "simulator"

        # Build the v2 reading source iterator
        self._reading_iter: Iterator[Dict[str, Any]] = self._build_source(
            scenario=scenario,
            stage=stage,
            seed=seed,
            patient_id=patient_id,
            synthea_path=synthea_path,
        )

        logger.info(
            "VitalsSimulator v2 initialised: scenario=%s, broker=%s:%d, seed=%d",
            scenario,
            broker_host,
            broker_port,
            seed,
        )

    # ------------------------------------------------------------------
    # Internal: source construction
    # ------------------------------------------------------------------

    def _build_source(
        self,
        scenario: str,
        stage: str,
        seed: int,
        patient_id: str,
        synthea_path: str,
    ) -> Iterator[Dict[str, Any]]:
        """Return an iterator that yields v2-compatible raw reading dicts.

        Args:
            scenario: Clinical scenario name.
            stage: Optional starting stage.
            seed: RNG seed.
            patient_id: Patient identifier.
            synthea_path: Path to Synthea CSV dir (empty string = disabled).

        Returns:
            An iterator yielding raw reading dicts.
        """
        engine = ProgressionEngine(
            scenario=scenario,
            stage=stage or None,
            patient_id=patient_id,
            seed=seed,
        )

        if synthea_path:
            try:
                bridge = SyntheaBridge(synthea_path)
                available = bridge.list_patients()
                pid: Optional[str] = None
                if patient_id in available:
                    # Caller supplied an explicit, valid Synthea patient UUID
                    pid = patient_id
                elif scenario == "sepsis":
                    # Auto-select the best candidate: prefer patients with a confirmed
                    # Sepsis condition record, then fall back to vital-sign heuristics.
                    sepsis_candidates = bridge.list_sepsis_patients()
                    if sepsis_candidates:
                        pid = sepsis_candidates[0]
                        self.patient_id = pid
                        logger.info("Auto-selected sepsis patient from Synthea dataset: %s", pid)
                if pid is None:
                    pid = available[0] if available else None
                if pid:
                    logger.info("Using Synthea data source: path=%s, patient=%s", synthea_path, pid)
                    self._source = "synthea"
                    return bridge.iter_patient(pid, fallback_engine=engine, loop=True)
                logger.warning("No patients found in Synthea path '%s'; using progression engine", synthea_path)
            except (FileNotFoundError, OSError) as exc:
                logger.warning("Synthea bridge unavailable (%s); using progression engine", exc)

        # Default: progression engine as infinite generator
        return self._engine_iter(engine)

    @staticmethod
    def _engine_iter(engine: ProgressionEngine) -> Iterator[Dict[str, Any]]:
        """Wrap a ProgressionEngine as an infinite iterator.

        Args:
            engine: Configured ProgressionEngine instance.

        Yields:
            Reading dicts from the engine.
        """
        while True:
            yield engine.next_reading()

    def connect(self) -> bool:
        """Connect to the MQTT broker.

        Returns:
            True if the connection was established successfully.
        """
        result = self.mqtt_client.connect()
        if result:
            self._connect_count += 1
            if self._connect_count % 10 == 0:
                logger.info("MQTT connection count: %d", self._connect_count)
        return result

    def _generate_vital(self) -> Dict[str, Any]:
        """Generate the next v2 vital-signs payload dict.

        Consumes one reading from the internal source iterator, computes
        SIRS/qSOFA scores via :func:`~src.schema.build_payload`, and
        returns the JSON-serialisable dict.

        Returns:
            Dict conforming to the v2 MQTT payload schema.
        """
        raw = next(self._reading_iter)
        payload = build_payload(
            patient_id=self.patient_id,
            scenario=self.scenario,
            scenario_stage=raw["scenario_stage"],
            timestamp=raw["timestamp"],
            hr=raw["hr"],
            bp_sys=raw["bp_sys"],
            bp_dia=raw["bp_dia"],
            o2_sat=raw["o2_sat"],
            temperature=raw["temperature"],
            respiratory_rate=raw["respiratory_rate"],
            wbc=raw["wbc"],
            lactate=raw["lactate"],
            quality=raw["quality"],
            source=self._source,
            sepsis_onset_ts=raw.get("sepsis_onset_ts"),
        )
        return payload.to_dict()

    def run(self) -> None:
        """Main event loop: publishes v2 vitals every publish_interval_s seconds.

        Runs until shutdown() is called or a KeyboardInterrupt is raised.
        Reconnects automatically if the broker drops the connection.
        """
        logger.info("VitalsSimulator v2 starting – scenario=%s", self.scenario)
        self._running = True
        self.connect()
        while self._running:
            if not self.mqtt_client.is_connected():
                logger.warning("MQTT broker disconnected – reconnecting")
                self.connect()

            vital = self._generate_vital()
            payload = json.dumps(vital)
            success = self.mqtt_client.publish(config.MQTT_TOPIC, payload, qos=config.MQTT_QOS)
            if success:
                self._publish_count += 1
                if self._publish_count % 100 == 0:
                    logger.info("Published %d vitals so far", self._publish_count)
            else:
                logger.warning("Failed to publish vital reading #%d", self._publish_count + 1)

            time.sleep(self.publish_interval_s)

    def shutdown(self) -> None:
        """Gracefully stop the event loop and close the MQTT connection."""
        logger.info("VitalsSimulator shutting down after %d publishes", self._publish_count)
        self._running = False
        self.mqtt_client.publish_status("offline")
        self.mqtt_client.disconnect()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_VALID_SCENARIOS = list(config.SCENARIOS.keys())


def _configure_logging() -> None:
    """Configure root logger from the LOGLEVEL environment variable."""
    log_level = os.environ.get("LOGLEVEL", "INFO").upper()
    numeric = getattr(logging, log_level, logging.INFO)
    logging.basicConfig(format=_LOG_FORMAT, level=numeric)


def main() -> None:
    """Parse CLI arguments, create a VitalsSimulator, and run it."""
    _configure_logging()

    parser = argparse.ArgumentParser(description="MedTech Vitals Publisher v2")
    parser.add_argument(
        "--scenario",
        choices=_VALID_SCENARIOS,
        default=os.environ.get("SCENARIO", "healthy"),
        help="Vital-signs scenario to simulate (default: healthy)",
    )
    parser.add_argument(
        "--broker-host",
        default=config.MQTT_BROKER,
        help=f"MQTT broker host (default: {config.MQTT_BROKER})",
    )
    parser.add_argument(
        "--broker-port",
        type=int,
        default=config.MQTT_PORT,
        help=f"MQTT broker port (default: {config.MQTT_PORT})",
    )
    parser.add_argument(
        "--patient-id",
        default=config.PATIENT_ID,
        help="Patient identifier embedded in every v2 payload (default: P001)",
    )
    parser.add_argument(
        "--synthea-path",
        default=config.SYNTHEA_DATA_PATH,
        help="Path to Synthea output/csv directory (optional; falls back to built-in engine)",
    )
    parser.add_argument(
        "--stage",
        default=config.SCENARIO_STAGE,
        help="Starting progression stage within the scenario (optional)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=config.SEED,
        help=f"Random seed for deterministic replay (default: {config.SEED})",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=config.PUBLISH_INTERVAL_S,
        help=f"Publish interval in seconds (default: {config.PUBLISH_INTERVAL_S})",
    )
    args = parser.parse_args()

    simulator = VitalsSimulator(
        scenario=args.scenario,
        broker_host=args.broker_host,
        broker_port=args.broker_port,
        patient_id=args.patient_id,
        synthea_path=args.synthea_path,
        stage=args.stage,
        seed=args.seed,
        publish_interval_s=args.interval,
    )
    try:
        simulator.run()
    except KeyboardInterrupt:
        logger.info("Received shutdown signal (Ctrl+C)")
    finally:
        simulator.shutdown()


if __name__ == "__main__":
    main()
