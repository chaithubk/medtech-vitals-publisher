#!/usr/bin/env python3
"""Vitals Simulator - Synthetic vital signs generator.

This module implements:
- VitalsSimulator: Main orchestrator
- ScenarioFactory: Vital signs generation per clinical scenario
- MQTTClient: paho-mqtt wrapper with exponential-backoff reconnect
"""

import argparse
import json
import logging
import os
import random
import time
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt

from src import config

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

    def __init__(
        self, broker_host: str, broker_port: int, client_id: Optional[str] = None
    ) -> None:
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
        self._client = mqtt.Client(client_id=self._client_id)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        # Last Will: broker publishes "offline" if the client disconnects unexpectedly
        self._client.will_set(
            config.MQTT_STATUS_TOPIC, payload="offline", qos=1, retain=True
        )
        self._client.loop_start()

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
            logger.info(
                "Connected to MQTT broker %s:%d", self._broker_host, self._broker_port
            )
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
    # Public API
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to the MQTT broker with exponential-backoff retries.

        Returns:
            True once the connection is established.
        """
        backoff = 1
        max_backoff = 60
        while True:
            try:
                self._client.connect(self._broker_host, self._broker_port)
                time.sleep(0.5)  # give on_connect callback time to fire
                if self._connected:
                    self._client.publish(
                        config.MQTT_STATUS_TOPIC, payload="online", qos=1, retain=True
                    )
                    return True
                logger.warning("Connection not confirmed yet, retrying in %ds", backoff)
            except (OSError, ConnectionError, TimeoutError) as exc:
                logger.warning("MQTT connect error: %s – retrying in %ds", exc, backoff)
                if (
                    self._broker_host in {"localhost", "127.0.0.1"}
                    and _running_in_container()
                ):
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
            self._client.publish(
                config.MQTT_STATUS_TOPIC, payload=status, qos=1, retain=True
            )
            logger.info("Published status: %s", status)

    def disconnect(self) -> None:
        """Disconnect from the MQTT broker and stop the network loop."""
        self._client.disconnect()
        self._client.loop_stop()
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

        assert cfg["hr"][0] <= hr <= cfg["hr"][1], f"hr {hr} out of range {cfg['hr']}"
        assert (
            cfg["bp_sys"][0] <= bp_sys <= cfg["bp_sys"][1]
        ), f"bp_sys {bp_sys} out of range {cfg['bp_sys']}"
        assert (
            cfg["bp_dia"][0] <= bp_dia <= cfg["bp_dia"][1]
        ), f"bp_dia {bp_dia} out of range {cfg['bp_dia']}"
        assert (
            cfg["o2_sat"][0] <= o2_sat <= cfg["o2_sat"][1]
        ), f"o2_sat {o2_sat} out of range {cfg['o2_sat']}"
        assert (
            cfg["temp"][0] <= temperature <= cfg["temp"][1]
        ), f"temperature {temperature} out of range {cfg['temp']}"

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
    """Main orchestrator: generates synthetic vitals and publishes them via MQTT.

    Args:
        scenario: Clinical scenario ('healthy', 'sepsis', or 'critical').
        broker_host: MQTT broker hostname or IP.
        broker_port: MQTT broker TCP port.
        seed: Random seed for deterministic vital generation.
    """

    def __init__(
        self,
        scenario: str = "healthy",
        broker_host: str = config.MQTT_BROKER,
        broker_port: int = config.MQTT_PORT,
        seed: int = 42,
    ) -> None:
        """Initialise the simulator.

        Args:
            scenario: Clinical scenario ('healthy', 'sepsis', or 'critical').
            broker_host: MQTT broker hostname or IP.
            broker_port: MQTT broker TCP port.
            seed: Random seed for deterministic vital generation.
        """
        self.scenario = scenario
        self.seed = seed
        self.config = config
        self._running = False
        self._publish_count = 0
        self._connect_count = 0
        self._rng = random.Random(seed)
        self.mqtt_client: MQTTClient = MQTTClient(
            broker_host=broker_host, broker_port=broker_port
        )
        logger.info(
            "VitalsSimulator initialised: scenario=%s, broker=%s:%d, seed=%d",
            scenario,
            broker_host,
            broker_port,
            seed,
        )

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
        """Generate one vital-signs reading for the current scenario.

        Returns:
            Dict with vital sign fields.
        """
        return ScenarioFactory._generate(self.scenario, self._rng)

    def run(self) -> None:
        """Main event loop: publishes vitals every PUBLISH_INTERVAL_S seconds.

        Runs until shutdown() is called or a KeyboardInterrupt is raised.
        Reconnects automatically if the broker drops the connection.
        """
        logger.info("VitalsSimulator starting – scenario=%s", self.scenario)
        self._running = True
        self.connect()
        while self._running:
            if not self.mqtt_client.is_connected():
                logger.warning("MQTT broker disconnected – reconnecting")
                self.connect()

            vital = self._generate_vital()
            payload = json.dumps(vital)
            success = self.mqtt_client.publish(
                config.MQTT_TOPIC, payload, qos=config.MQTT_QOS
            )
            if success:
                self._publish_count += 1
                if self._publish_count % 100 == 0:
                    logger.info("Published %d vitals so far", self._publish_count)
            else:
                logger.warning(
                    "Failed to publish vital reading #%d", self._publish_count + 1
                )

            time.sleep(config.PUBLISH_INTERVAL_S)

    def shutdown(self) -> None:
        """Gracefully stop the event loop and close the MQTT connection."""
        logger.info(
            "VitalsSimulator shutting down after %d publishes", self._publish_count
        )
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

    parser = argparse.ArgumentParser(description="MedTech Vitals Publisher")
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
    args = parser.parse_args()

    simulator = VitalsSimulator(
        scenario=args.scenario,
        broker_host=args.broker_host,
        broker_port=args.broker_port,
    )
    try:
        simulator.run()
    except KeyboardInterrupt:
        logger.info("Received shutdown signal (Ctrl+C)")
    finally:
        simulator.shutdown()


if __name__ == "__main__":
    main()
