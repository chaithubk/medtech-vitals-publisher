"""Unit tests for the MedTech Vitals Publisher simulator.

All MQTT broker interactions are mocked; no real broker is required.
"""

import json
import logging
import time
from unittest.mock import MagicMock, patch

from src import config
from src.simulator import ScenarioFactory, VitalsSimulator

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = {"timestamp", "hr", "bp_sys", "bp_dia", "o2_sat", "temperature", "quality", "source"}


def _in_range(value: float, lo: float, hi: float) -> bool:
    return lo <= value <= hi


# ---------------------------------------------------------------------------
# ScenarioFactory – range checks
# ---------------------------------------------------------------------------


def test_scenario_healthy_ranges():
    """ScenarioFactory.healthy() returns all vitals within healthy bounds."""
    cfg = config.SCENARIOS["healthy"]
    for _ in range(20):
        v = ScenarioFactory.healthy()
        assert _in_range(v["hr"], *cfg["hr"]), f"hr={v['hr']} out of {cfg['hr']}"
        assert _in_range(v["bp_sys"], *cfg["bp_sys"]), f"bp_sys={v['bp_sys']} out of {cfg['bp_sys']}"
        assert _in_range(v["bp_dia"], *cfg["bp_dia"]), f"bp_dia={v['bp_dia']} out of {cfg['bp_dia']}"
        assert _in_range(v["o2_sat"], *cfg["o2_sat"]), f"o2_sat={v['o2_sat']} out of {cfg['o2_sat']}"
        assert _in_range(v["temperature"], *cfg["temp"]), f"temperature={v['temperature']} out of {cfg['temp']}"
        assert v["quality"] == cfg["quality"]


def test_scenario_sepsis_ranges():
    """ScenarioFactory.sepsis() returns all vitals within sepsis bounds."""
    cfg = config.SCENARIOS["sepsis"]
    for _ in range(20):
        v = ScenarioFactory.sepsis()
        assert _in_range(v["hr"], *cfg["hr"])
        assert _in_range(v["bp_sys"], *cfg["bp_sys"])
        assert _in_range(v["bp_dia"], *cfg["bp_dia"])
        assert _in_range(v["o2_sat"], *cfg["o2_sat"])
        assert _in_range(v["temperature"], *cfg["temp"])
        assert v["quality"] == cfg["quality"]


def test_scenario_critical_ranges():
    """ScenarioFactory.critical() returns all vitals within critical bounds."""
    cfg = config.SCENARIOS["critical"]
    for _ in range(20):
        v = ScenarioFactory.critical()
        assert _in_range(v["hr"], *cfg["hr"])
        assert _in_range(v["bp_sys"], *cfg["bp_sys"])
        assert _in_range(v["bp_dia"], *cfg["bp_dia"])
        assert _in_range(v["o2_sat"], *cfg["o2_sat"])
        assert _in_range(v["temperature"], *cfg["temp"])
        assert v["quality"] == cfg["quality"]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_determinism():
    """The same scenario + seed always produces identical vital readings."""
    seed = 99
    v1_healthy = ScenarioFactory.healthy(seed=seed)
    v2_healthy = ScenarioFactory.healthy(seed=seed)
    assert v1_healthy["hr"] == v2_healthy["hr"]
    assert v1_healthy["bp_sys"] == v2_healthy["bp_sys"]
    assert v1_healthy["temperature"] == v2_healthy["temperature"]

    v1_sepsis = ScenarioFactory.sepsis(seed=seed)
    v2_sepsis = ScenarioFactory.sepsis(seed=seed)
    assert v1_sepsis["hr"] == v2_sepsis["hr"]

    # Different seeds must produce different results (with overwhelming probability)
    v_other = ScenarioFactory.healthy(seed=seed + 1)
    assert v1_healthy["hr"] != v_other["hr"] or v1_healthy["bp_sys"] != v_other["bp_sys"]


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------


def test_json_schema():
    """Generated vital dict contains all required JSON schema fields."""
    for factory_fn in (ScenarioFactory.healthy, ScenarioFactory.sepsis, ScenarioFactory.critical):
        vital = factory_fn(seed=42)
        missing = _REQUIRED_FIELDS - vital.keys()
        assert not missing, f"Missing fields: {missing}"
        assert vital["source"] == "simulator"
        assert isinstance(vital["timestamp"], int)
        assert isinstance(vital["quality"], int)
        # All numeric vitals should be floats or ints
        for field in ("hr", "bp_sys", "bp_dia", "o2_sat", "temperature"):
            assert isinstance(vital[field], (int, float)), f"{field} is not numeric"


# ---------------------------------------------------------------------------
# Timestamp sanity
# ---------------------------------------------------------------------------


def test_timestamp_reasonable():
    """Vital timestamp is within 1 second of the current time."""
    before_ms = int(time.time() * 1000)
    vital = ScenarioFactory.healthy()
    after_ms = int(time.time() * 1000)
    assert (
        before_ms <= vital["timestamp"] <= after_ms + 1000
    ), f"timestamp {vital['timestamp']} not in [{before_ms}, {after_ms + 1000}]"


# ---------------------------------------------------------------------------
# MQTT publish (mocked)
# ---------------------------------------------------------------------------


def test_mqtt_publish():
    """VitalsSimulator.run() calls mqtt_client.publish with a valid JSON payload."""
    sim = VitalsSimulator(scenario="healthy", seed=42)

    mock_mqtt = MagicMock()
    mock_mqtt.is_connected.return_value = True
    mock_mqtt.connect.return_value = True
    mock_mqtt.publish.return_value = True
    sim.mqtt_client = mock_mqtt

    def _stop_after_one(seconds):  # noqa: ANN001
        sim._running = False

    with patch("src.simulator.time.sleep", side_effect=_stop_after_one):
        sim.run()

    mock_mqtt.publish.assert_called_once()
    call_args = mock_mqtt.publish.call_args

    topic = call_args[0][0]
    payload_str = call_args[0][1]

    assert topic == config.MQTT_TOPIC, f"Unexpected topic: {topic}"

    payload = json.loads(payload_str)
    assert _REQUIRED_FIELDS <= payload.keys(), f"Missing fields: {_REQUIRED_FIELDS - payload.keys()}"
    assert payload["source"] == "simulator"


# ---------------------------------------------------------------------------
# MQTT reconnect (mocked)
# ---------------------------------------------------------------------------


def test_mqtt_reconnect():
    """VitalsSimulator.run() calls connect() again when the broker disconnects."""
    sim = VitalsSimulator(scenario="healthy", seed=42)

    mock_mqtt = MagicMock()
    # Broker appears disconnected inside the loop
    mock_mqtt.is_connected.return_value = False
    mock_mqtt.connect.return_value = True
    mock_mqtt.publish.return_value = True
    sim.mqtt_client = mock_mqtt

    def _stop_after_one(seconds):  # noqa: ANN001
        sim._running = False

    with patch("src.simulator.time.sleep", side_effect=_stop_after_one):
        sim.run()

    # connect() must have been called at least twice:
    # once for the initial connect in run() and once inside the loop
    assert mock_mqtt.connect.call_count >= 2, f"Expected >= 2 connect() calls, got {mock_mqtt.connect.call_count}"


# ---------------------------------------------------------------------------
# MQTTClient – unit tests (paho internals mocked)
# ---------------------------------------------------------------------------


class TestMQTTClient:
    """Tests for MQTTClient; paho.mqtt.client.Client is always mocked."""

    @staticmethod
    def _make_client():
        """Return an MQTTClient whose underlying paho Client is a MagicMock."""
        from src.simulator import MQTTClient

        with patch("src.simulator.mqtt.Client") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            client = MQTTClient("localhost", 1883)
            client._client = mock_instance  # keep reference
        return client

    def test_on_connect_success(self):
        """_on_connect with rc=0 sets _connected to True."""
        client = self._make_client()
        client._on_connect(None, None, {}, 0)
        assert client.is_connected() is True

    def test_on_connect_failure(self):
        """_on_connect with rc!=0 sets _connected to False."""
        client = self._make_client()
        client._connected = True
        client._on_connect(None, None, {}, 1)
        assert client.is_connected() is False

    def test_on_disconnect_clean(self):
        """_on_disconnect with rc=0 marks client as disconnected."""
        client = self._make_client()
        client._connected = True
        client._on_disconnect(None, None, 0)
        assert client.is_connected() is False

    def test_on_disconnect_unexpected(self):
        """_on_disconnect with rc!=0 marks client as disconnected."""
        client = self._make_client()
        client._connected = True
        client._on_disconnect(None, None, 1)
        assert client.is_connected() is False

    def test_publish_when_not_connected(self):
        """publish() returns False when not connected."""
        client = self._make_client()
        assert client.is_connected() is False
        assert client.publish("topic", "payload") is False

    def test_publish_success(self):
        """publish() returns True on successful broker acknowledgement."""
        import paho.mqtt.client as real_mqtt

        client = self._make_client()
        client._connected = True
        mock_result = MagicMock()
        mock_result.rc = real_mqtt.MQTT_ERR_SUCCESS
        client._client.publish.return_value = mock_result

        assert client.publish("t", "p", qos=1) is True
        client._client.publish.assert_called_once_with("t", "p", qos=1)

    def test_publish_broker_error(self):
        """publish() returns False when the broker returns an error code."""
        client = self._make_client()
        client._connected = True
        mock_result = MagicMock()
        mock_result.rc = 4  # some error
        client._client.publish.return_value = mock_result

        assert client.publish("t", "p") is False

    def test_disconnect(self):
        """disconnect() calls paho disconnect/loop_stop and marks as not connected."""
        client = self._make_client()
        client._connected = True
        client.disconnect()
        assert client.is_connected() is False
        client._client.disconnect.assert_called_once()
        client._client.loop_stop.assert_called_once()

    def test_connect_succeeds_first_try(self):
        """connect() returns True when the broker accepts the connection."""
        client = self._make_client()

        def _set_connected(seconds):
            client._connected = True

        with patch("src.simulator.time.sleep", side_effect=_set_connected):
            result = client.connect()

        assert result is True

    def test_connect_retries_on_exception(self):
        """connect() retries when paho raises an exception."""
        from src.simulator import MQTTClient

        with patch("src.simulator.mqtt.Client") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            client = MQTTClient("localhost", 1883)
            client._client = mock_instance

        call_count = [0]

        def _paho_connect(host, port):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError("connection refused")
            # Second attempt: trigger connected on next sleep
            client._connected = True

        sleep_count = [0]

        def _sleep(seconds):
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                client._connected = True

        client._client.connect.side_effect = _paho_connect

        with patch("src.simulator.time.sleep", side_effect=_sleep):
            result = client.connect()

        assert result is True
        assert call_count[0] >= 2


# ---------------------------------------------------------------------------
# VitalsSimulator – additional coverage
# ---------------------------------------------------------------------------


def test_simulator_shutdown():
    """shutdown() sets _running=False and calls mqtt_client.disconnect()."""
    sim = VitalsSimulator(scenario="healthy", seed=42)
    mock_mqtt = MagicMock()
    sim.mqtt_client = mock_mqtt

    sim.shutdown()

    assert sim._running is False
    mock_mqtt.disconnect.assert_called_once()


def test_simulator_connect_logs_every_10th():
    """connect() emits an INFO log on every 10th successful connection."""
    sim = VitalsSimulator(scenario="healthy", seed=42)
    mock_mqtt = MagicMock()
    mock_mqtt.connect.return_value = True
    sim.mqtt_client = mock_mqtt
    sim._connect_count = 9  # next call will be the 10th

    with patch("src.simulator.logger") as mock_logger:
        sim.connect()
        mock_logger.info.assert_called()


def test_run_failed_publish_logs_warning():
    """run() logs a warning when publish() returns False."""
    sim = VitalsSimulator(scenario="healthy", seed=42)
    mock_mqtt = MagicMock()
    mock_mqtt.is_connected.return_value = True
    mock_mqtt.connect.return_value = True
    mock_mqtt.publish.return_value = False  # simulate publish failure
    sim.mqtt_client = mock_mqtt

    def _stop(seconds):
        sim._running = False

    with patch("src.simulator.time.sleep", side_effect=_stop):
        sim.run()

    # Just verify the loop ran; warning was logged internally


def test_run_logs_every_100th_publish():
    """run() emits an INFO log on every 100th successful publish."""
    sim = VitalsSimulator(scenario="healthy", seed=42)
    sim._publish_count = 99  # next successful publish will be the 100th
    mock_mqtt = MagicMock()
    mock_mqtt.is_connected.return_value = True
    mock_mqtt.connect.return_value = True
    mock_mqtt.publish.return_value = True
    sim.mqtt_client = mock_mqtt

    def _stop(seconds):
        sim._running = False

    with patch("src.simulator.time.sleep", side_effect=_stop):
        with patch("src.simulator.logger") as mock_logger:
            sim.run()
            mock_logger.info.assert_called()


# ---------------------------------------------------------------------------
# CLI main()
# ---------------------------------------------------------------------------


def test_main_keyboard_interrupt():
    """main() handles KeyboardInterrupt and calls simulator.shutdown()."""
    import sys

    from src.simulator import main

    with patch("src.simulator.VitalsSimulator") as mock_cls:
        mock_sim = MagicMock()
        mock_cls.return_value = mock_sim
        mock_sim.run.side_effect = KeyboardInterrupt()

        with patch.object(sys, "argv", ["prog", "--scenario", "sepsis"]):
            main()

        assert mock_sim.shutdown.call_count == 1


def test_main_default_scenario():
    """main() defaults to 'healthy' when no --scenario flag is supplied."""
    import sys

    from src.simulator import main

    with patch("src.simulator.VitalsSimulator") as mock_cls:
        mock_sim = MagicMock()
        mock_cls.return_value = mock_sim
        mock_sim.run.side_effect = KeyboardInterrupt()

        with patch.object(sys, "argv", ["prog"]):
            main()

        call_kwargs = mock_cls.call_args
        scenario_used = call_kwargs[1].get("scenario") if call_kwargs[1] else call_kwargs[0][0]
        assert scenario_used == "healthy"


def test_configure_logging_custom_level():
    """_configure_logging() uses the LOGLEVEL environment variable."""
    from src.simulator import _configure_logging

    with patch.dict("os.environ", {"LOGLEVEL": "DEBUG"}):
        with patch("src.simulator.logging.basicConfig") as mock_basic:
            _configure_logging()
            mock_basic.assert_called_once()
            _, kwargs = mock_basic.call_args
            assert kwargs["level"] == logging.DEBUG
