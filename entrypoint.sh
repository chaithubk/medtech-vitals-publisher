#!/bin/sh
set -e

# Start the mosquitto MQTT broker in the background
mosquitto -c /etc/mosquitto/mosquitto.conf -d

# Wait until the broker is accepting connections
echo "Waiting for mosquitto to start..."
until mosquitto_pub -h localhost -p "${MQTT_PORT:-1883}" -t "medtech/health" -m "ready" -q 0 2>/dev/null; do
    sleep 1
done
echo "Mosquitto broker is ready"

# Launch the vitals simulator
exec python -m src
