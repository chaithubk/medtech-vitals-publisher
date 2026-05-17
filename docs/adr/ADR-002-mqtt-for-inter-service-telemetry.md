# ADR-002 — MQTT for Inter-Service Telemetry Transport

| Field | Value |
|---|---|
| **ADR ID** | ADR-002 |
| **Title** | MQTT for Inter-Service Telemetry Transport |
| **Status** | Accepted |
| **Date** | 2026-05-13 |
| **Deciders** | MedTech R&D, Systems Architect |
| **Affected Repos** | `medtech-vitals-publisher`, `medtech-edge-analytics`, `medtech-clinician-ui`, `medtech-telemetry-cloud` |

---

## Context

The MedTech platform requires a reliable mechanism for transporting vital-signs payloads from the **vitals publisher** to the **edge analytics engine**, the **clinician UI**, and to the **cloud backend**. 

**MQTT (Message Queuing Telemetry Transport)** is a publish-subscribe messaging protocol designed for constrained and distributed network environments. It provides:
- Topic-based routing (`medtech/vitals/latest`, `medtech/predictions/sepsis`)
- QoS levels (at-most-once, at-least-once, exactly-once)
- A broker (Mosquitto) that decouples publishers and subscribers
- A standard wire protocol (TCP/IP port 1883, TLS port 8883) enabling cross-host communication
- Open-source, free implementations (Mosquitto)

---

## Decision

**MQTT is selected as the standard inter-service telemetry transport.**

Mosquitto (open-source MQTT broker) is used as the embedded broker. The vitals publisher bundles the broker and publishes on `medtech/vitals/latest`. All consumers subscribe independently. This architecture is used for both the Docker Compose simulation environment and the on-device Yocto deployment, and scales uniformly to cloud integrations.

---

## Rationale

### 1. Free and Open-Source

Mosquitto is a lightweight, well-maintained, free and open-source MQTT broker. This eliminates vendor lock-in and licensing costs, critical for a medical device platform that must support long-term deployments and regulatory compliance without external dependencies.

### 2. Cloud Interoperability

The `medtech-telemetry-cloud` backend aggregates telemetry from a fleet of devices across multiple hospitals. MQTT's standard protocol enables seamless integration with cloud platforms; the device uses the same publish-subscribe pattern whether writing to an on-device broker or a cloud-hosted broker. The MQTT bridge pattern (device MQTT → cloud MQTT broker → backend ingestion) enables horizontal scaling across deployments with no changes to device firmware.

### 3. Hospital-Wide Network Deployment

MQTT's standard wire protocol (TCP/IP over port 1883 or TLS over 8883) works identically whether all services are in the same container, the same device, or distributed across a hospital network. This unifies the on-device deployment model with future hospital-wide integration scenarios.

### 4. Consumer Independence and Decoupling

MQTT's publish-subscribe model means that adding a new consumer (e.g., a hospital audit logger, a research data capture service) requires zero changes to the publisher. Each consumer subscribes independently to relevant topics without tight coupling to the publisher or other subscribers.

### 5. Alignment with IEC 60601-1-8 Alarm Architecture

IEC 60601-1-8 requires that the alarm system remain operational even when ancillary subsystems fail. MQTT's `depends_on: service_healthy` pattern, QoS retry semantics, and optional broker persistence provide a decoupled alarm transport that continues to deliver vitals even if the clinician UI restarts or the cloud bridge is offline.

### 6. HL7 FHIR / Hospital Integration Standards

HL7 FHIR R4 and IHE profiles for medical device integration use MQTT over TLS as a recognized standard transport for device observation data and subscription notifications. This decision preserves optionality for future FHIR-based hospital integrations.

### 7. Testability in CI

MQTT's network-based transport enables container-isolated integration testing in Docker Compose. Any CI runner can spin up the full MQTT broker + publisher + subscriber stack without special OS-level configuration, ensuring reproducible and reliable testing across environments.

---

## Consequences

### Positive

- All consumers (edge analytics, clinician UI, cloud bridge, integration tests) can subscribe independently with no coupling to each other
- Adding new consumers requires zero changes to the publisher
- Hospital-wide deployment requires no architectural change to the protocol
- Broker restart is transparent to consumers within QoS retry window
- Standard protocol enables off-the-shelf tooling (MQTT Explorer, mosquitto_pub, FHIR MQTT bridges)

### Negative

- Broker availability is a critical dependency; broker crash silences the entire vitals pipeline until restart—mitigated by `restart: unless-stopped` policy and health checks
- QoS 0 (at-most-once) used for low latency means occasional message drops under network stress—mitigated by `service_healthy` dependency chain and broker restart automation

### Neutral

- The Mosquitto broker is bundled in the `medtech-vitals-publisher` container; future deployments may externalize the broker if needed for multi-device scenarios

---

## Alternatives Considered

| Alternative | Assessment |
|---|---|
| gRPC streaming | Higher implementation complexity; no native pub-sub fan-out; not standard in IHE/HL7 device profiles |
| REST polling (HTTP) | Polling latency incompatible with < 500 ms end-to-end target; requires all consumers to implement polling clients |
| Apache Kafka | Operational complexity inappropriate for embedded edge; Kafka requires JVM and significant memory overhead on a constrained device |
| DDS (Data Distribution Service) | Standard in aerospace/defense real-time systems; limited hospital ecosystem tooling; steep learning curve without commensurate clinical interoperability benefit |

---

## Standards References

| Standard | Relationship to This Decision |
|---|---|
| **IEC 60601-1-8:2006+AMD1:2012** | MQTT broker decoupling supports alarm system availability requirement (§5.2). QoS semantics support alarm delivery reliability. |
| **HL7 FHIR R4 Subscriptions** | FHIR subscription backplane supports MQTT as a notification channel; this decision preserves FHIR integration optionality. |
| **IHE DEV — Patient Care Device (PCD)** | IHE PCD profiles specify network transport for device observation data; MQTT over TLS is aligned with the IHE MQTT PoC profile. |
| **ISO 14971:2019** | Broker restart risk is documented; `restart: unless-stopped` and `service_healthy` dependency chain are the risk controls. |

---

## Review Date

This decision should be revisited if:
- The platform adopts a requirement for exactly-once delivery semantics across reboots (consider MQTT QoS 2 with persistent storage or alternative broker)
- Hospital deployment requires multi-broker federation across geographically distributed sites (consider MQTT bridge or HiveMQ cluster)
- Latency requirements tighten below acceptable MQTT over TCP thresholds (quantify and reassess network topology)
