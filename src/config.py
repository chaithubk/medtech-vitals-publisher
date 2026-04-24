"""Configuration constants for the MedTech Vitals Publisher.

All runtime-tunable values are read from environment variables with sensible defaults.
"""

import os


def _get_int_env(name: str, default: int) -> int:
    """Parse an integer environment variable with a safe fallback.

    Args:
        name: Environment variable name.
        default: Value to use when the variable is absent or non-numeric.

    Returns:
        Parsed integer value, or *default* when the variable is unset or invalid.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


# MQTT connection settings
MQTT_BROKER: str = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT: int = _get_int_env("MQTT_PORT", 1883)
MQTT_TOPIC: str = "medtech/vitals/latest"
MQTT_STATUS_TOPIC: str = "medtech/vitals/status"
MQTT_QOS: int = 1

# Publishing cadence
PUBLISH_INTERVAL_S: int = 10

# Vital-sign ranges and quality scores per scenario
SCENARIOS: dict = {
    "healthy": {
        "hr": (60, 100),
        "bp_sys": (90, 130),
        "bp_dia": (60, 85),
        "o2_sat": (95, 100),
        "temp": (36.5, 37.5),
        "quality": 95,
    },
    "sepsis": {
        "hr": (110, 140),
        "bp_sys": (100, 160),
        "bp_dia": (65, 100),
        "o2_sat": (90, 95),
        "temp": (38.5, 40),
        "quality": 85,
    },
    "critical": {
        "hr": (140, 180),
        "bp_sys": (80, 200),
        "bp_dia": (40, 120),
        "o2_sat": (60, 90),
        "temp": (40, 42),
        "quality": 75,
    },
}
