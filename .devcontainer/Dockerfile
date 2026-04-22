FROM python:3.11-bookworm

# Install MQTT broker + tools
RUN apt-get update && apt-get install -y \
    mosquitto mosquitto-clients \
    git curl vim nano tmux \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

ENV PYTHONUNBUFFERED=1
ENV MQTT_BROKER=localhost
ENV SCENARIO=healthy
ENV LOGLEVEL=INFO

CMD ["/bin/bash"]