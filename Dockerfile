FROM python:3.11-bookworm

WORKDIR /app

# Install Mosquitto MQTT broker for local development and integration testing
RUN apt-get update && \
    apt-get install -y --no-install-recommends mosquitto mosquitto-clients && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY tests/ ./tests/
COPY mosquitto.conf /etc/mosquitto/mosquitto.conf

# Startup script: launch mosquitto broker then the Python simulator
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV MQTT_BROKER=localhost
ENV MQTT_PORT=1883
ENV SCENARIO=healthy
ENV LOGLEVEL=INFO

CMD ["/entrypoint.sh"]