FROM python:3.11-bookworm

# Set non-interactive mode
ENV DEBIAN_FRONTEND=noninteractive

# Update and install only real packages
RUN apt-get update && apt-get install -y \
    vim \
    nano \
    git \
    curl \
    wget \
    tar \
    gzip \
    zip \
    unzip \
    tree \
    htop \
    build-essential \
    make \
    gcc \
    g++ \
    mosquitto \
    mosquitto-clients \
    iputils-ping \
    net-tools \
    tmux \
    jq \
    procps \
    gawk \
    locate \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Ensure pip is up to date
RUN pip install --upgrade pip setuptools wheel

# Preinstall runtime + CI parity tooling in the image so the container is ready
# without extra manual setup.
COPY requirements.txt /tmp/requirements.txt
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install -r /tmp/requirements.txt -r /tmp/requirements-dev.txt

# Set working directory
WORKDIR /workspace

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV MQTT_BROKER=localhost
ENV MQTT_PORT=1883
ENV SCENARIO=healthy
ENV LOGLEVEL=INFO
ENV TERM=xterm-256color

# Create useful directories
RUN mkdir -p /workspace/logs \
    && mkdir -p /workspace/data \
    && chmod 777 /workspace/logs \
    && chmod 777 /workspace/data

CMD ["/bin/bash"]