# Product Requirements: Vitals Publisher

## Problem
Medical devices need realistic test data that doesn't require real hardware or patient data.

## Solution
A deterministic synthetic vitals generator that publishes to MQTT every 10 seconds.

## Clinical Context
ICU clinicians require continuous vital monitoring. Our vitals publisher simulates:
- Healthy patient: normal vital ranges
- Sepsis patient: progressive vital degradation
- Critical patient: life-threatening values

This enables realistic testing without live patient data.

## Key Features (Stage 1)
1. Generate synthetic vitals (HR, BP, O2, Temp, quality)
2. Connect to Mosquitto MQTT broker
3. Support multiple scenarios (healthy, sepsis, critical)
4. Publish every 10 seconds to `medtech/vitals/latest`
5. Graceful error handling and reconnection
6. Deterministic output (same scenario = same vitals)

## MQTT Specifications
- **Broker:** Mosquitto (localhost:1883 by default)
- **Topic:** medtech/vitals/latest
- **QoS:** 1 (at-least-once delivery)
- **Frequency:** Every 10 seconds
- **Payload:** JSON

## Vital Ranges (Per Scenario)

### Healthy
- HR: 60-100 bpm
- BP: 90-130 / 60-85 mmHg
- O2 Sat: 95-100%
- Temp: 36.5-37.5°C
- Quality: 95

### Sepsis
- HR: 110-140 bpm (elevated)
- BP: 100-160 / 65-100 mmHg (variable)
- O2 Sat: 90-95% (lower)
- Temp: 38.5-40°C (fever)
- Quality: 85

### Critical
- HR: 140+ bpm (tachycardia)
- BP: unstable, wide swings
- O2 Sat: <90% (hypoxia)
- Temp: >40°C (high fever)
- Quality: 75

## MQTT Payload Schema
```json
{
  "timestamp": 1712973600000,
  "hr": 92.0,
  "bp_sys": 135.0,
  "bp_dia": 85.0,
  "o2_sat": 98.0,
  "temperature": 37.2,
  "quality": 95,
  "source": "simulator"
}