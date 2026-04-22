FROM python:3.11-bookworm

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY tests/ ./tests/

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV MQTT_BROKER=localhost
ENV MQTT_PORT=1883
ENV SCENARIO=healthy
ENV LOGLEVEL=INFO

# Run simulator
CMD ["python", "-m", "src.simulator", "--scenario", "healthy"]