# PRD-002 — MedTech Vitals Publisher

| Field | Value |
|---|---|
| **Document ID** | PRD-002 |
| **Product** | MedTech Vitals Publisher |
| **Repo** | `chaithubk/medtech-vitals-publisher` |
| **Author** | MedTech R&D |
| **Status** | Active |
| **Document Class** | Aspirational (product intent and roadmap) |
| **Service Version** | 2.1.1 |
| **Last Updated** | 2026-05-29 |

> **Synthetic Data and Privacy Declaration:** No real patient PHI/PII is used,
> transmitted, or stored. Synthetic records may contain person-like demographic
> fields generated for realism. This service is an educational R&D prototype only.

> **Normative Source Note:** This PRD is aspirational. Runtime behavior is
> normatively defined by implementation and contract artifacts in `README.md`,
> `src/`, `tests/`, and `contracts/vitals/`.

> **Versioning Policy:** Service versioning is independent from telemetry
> contract schema versioning. Service releases track application changes; schema
> versions track payload compatibility.

---

## 1. Opportunity

Reliable automated early warning systems (EWS) require a continuous, high-fidelity stream of physiological data. In production, this stream comes from bedside monitors, pulse oximeters, and vital-sign management systems (VSMS). During the **pre-hardware phase** of a MedTech program — before a certified device and its IEC 60601 drivers are available — there is a critical gap: **no vitals stream exists to exercise the inference engine, the clinician dashboard, or the integration tests**.

Without a robust vitals simulator, the entire downstream pipeline — edge analytics, UI, cloud ingestion, CI — is blocked. More critically, without a simulator that can reproduce clinically realistic scenarios (steady healthy baseline, early sepsis-onset trajectory, and critical deterioration), the sepsis detection model cannot be regression-tested and the alarm system cannot be validated.

The **MedTech Vitals Publisher** fills this gap. It is a deterministic, scenario-driven Python MQTT publisher that generates Synthea-modeled vital signs at a configurable publish interval. It also bundles a Mosquitto broker, making it the single point of MQTT network entry for the entire containerized stack.

### Clinical Scenarios Supported

| Scenario | Description | Clinical Purpose |
|---|---|---|
| `healthy` | Normal adult vitals within reference ranges | Establishes baseline; verifies no false-positive alarms |
| `sepsis` | Multi-stage deterioration (`pre_sepsis` → `sepsis_onset` → `sepsis` → `septic_shock`) | Tests sensitivity and progression handling across sepsis stages |
| `critical` | Severely abnormal vitals triggering SOFA-equivalent threshold crossing | Tests model specificity at high clinical acuity |

---

## 2. Target Audience

### Primary Users

| Persona | Need |
|---|---|
| **Edge Analytics Engineer** | A reliable, schema-compliant vitals feed to test inference at all three acuity levels without needing physical hardware |
| **Clinician UI Engineer** | A predictable data stream to validate alarm rendering, chart updates, and risk badge display |
| **Platform Integration Engineer** | A MQTT broker + publisher as a single Docker service dependency for `docker compose up` |
| **QA / Validation Engineer** | Deterministic scenario replay to confirm identical vitals produce identical risk predictions across builds |

### Secondary Users

| Persona | Need |
|---|---|
| **Clinical Educator** | Controllable scenario injection to demonstrate sepsis escalation in training simulations |
| **Regulatory Affairs** | Evidence that test data is entirely synthetic, documented, and traceable |

---

## 3. Product Vision

> Provide a deterministic, clinically realistic, scenario-driven vitals stream over MQTT so that every downstream consumer — inference engine, dashboard, integration test suite, cloud backend — can be continuously exercised, validated, and released without waiting for physical hardware.

---

## 4. Success Metrics

| Metric | Target | Measurement Method |
|---|---|---|
| Telemetry reliability | **≥ 99.9%** (< 1 drop per 1,000 messages) | Integration test message-count assertions |
| Publish latency (p99) | **< 50 ms** per publish cycle | Timed integration test |
| Schema compliance rate | **100%** of published payloads valid against contract | Runtime schema validator + CI |
| Scenario determinism | Identical seed → identical sequence | CI regression fixture comparison |
| Broker availability (MQTT health check) | **< 15 s** to ready after container start | Docker healthcheck in compose |

---

## 5. Scope

### In Scope (v2.x)

- Deterministic vitals generation for `healthy`, `sepsis`, `critical` scenarios
- MQTT publish on topic `medtech/vitals/latest`
- Integrated Mosquitto broker (port 1883)
- Configurable publish interval via `PUBLISH_INTERVAL_S` environment variable
- Scenario selection via `SCENARIO` environment variable
- Telemetry contract vendoring (`contracts/vitals/`) and drift detection CI workflow
- GHCR image publishing with pinned release tags
- Docker healthcheck via `mosquitto_pub` probe

### Out of Scope

- Real device driver integration
- HL7 v2.x or FHIR resource emission (handled by cloud backend)
- Multi-patient simultaneous simulation (single-patient stream per container)
- Waveform (continuous) data (spot-check vitals only)

---

## 6. Functional Requirements

### FR-001: Deterministic Scenario Generation

The publisher MUST produce clinically plausible vital-sign values for each scenario using fixed seed or deterministic progression:
- `healthy`: HR 60–100, SpO₂ 96–100%, Temp 36.0–37.5°C, BP 110–130/70–85
- `sepsis`: stage-driven deterioration including `sepsis_onset` as an internal progression stage
- `critical`: HR > 130 or < 40, SpO₂ < 88%, Temp > 39.5°C or < 35°C

### FR-002: Schema-Compliant Payloads

Every published payload MUST conform to the telemetry contract (`vitals.schema.json` v2.x):
```json
{
  "version": "2.1.1",
  "patient_id": "P001",
  "scenario": "sepsis",
  "scenario_stage": "sepsis_onset",
  "timestamp": 1712973600000,
  "hr": <number>,
  "bp_sys": <number>,
  "bp_dia": <number>,
  "o2_sat": <number>,
  "temperature": <number>,
  "respiratory_rate": <number>,
  "wbc": <number>,
  "lactate": <number>,
  "creatinine": <number>,
  "altered_mentation": <boolean>,
  "sirs_score": <integer>,
  "qsofa_score": <integer>,
  "sepsis_stage": "none|sirs|sepsis|septic_shock",
  "sepsis_onset_ts": <integer|null>,
  "quality": "good|degraded|poor",
  "source": "simulator"
}
```

### FR-003: MQTT Broker Integration

The service MUST start a Mosquitto broker on port 1883 and publish on `medtech/vitals/latest` using QoS 1 (at-least-once delivery).

### FR-004: Contract Vendoring

The service MUST vendor the telemetry contract schema into `contracts/vitals/` at build time, and CI MUST detect drift if the upstream contract releases a new version.

---

## 7. Non-Functional Requirements

| ID | Requirement | Standard Reference |
|---|---|---|
| NFR-001 | Publish interval MUST be configurable without code change | 12-factor app / IEC 62304 §5.5 |
| NFR-002 | Container MUST pass a `HEALTHCHECK` within 15 seconds of start | Docker best practice; CI gate |
| NFR-003 | Payload `quality` field MUST reflect signal fidelity using categorical values (`good`, `degraded`, `poor`) | IEC 60601-1-8 alarm reliability |
| NFR-004 | All scenario transitions MUST be reproducible from the same environment | ISO 14971 §10 (test traceability) |
| NFR-005 | Image MUST be < 500 MB to support edge-constrained deployment testing | Resource constraint |

---

## 8. Regulatory & Standards Alignment

| Standard | Relevance to This Product |
|---|---|
| **IEC 60601-1-8:2006+AMD1:2012** | Vitals publisher provides the physiological inputs that drive alarm conditions. Scenario fidelity directly determines the validity of alarm system testing. The `quality` field is used by consumers to assess alarm source reliability. |
| **HL7 v2.x ORU^R01** | `hr`, `o2_sat`, `bp_sys`, `bp_dia`, `temperature` field naming and units are aligned with HL7 OBX segment conventions to enable downstream interoperability without transformation. |
| **ISO 14971:2019 §7** | The three scenarios (healthy, sepsis, critical) constitute hazard-based test inputs. Each scenario maps to a risk item in the program hazard analysis: false negative (missed sepsis), false positive (nuisance alarm), and catastrophic presentation. |
| **IEC 62304:2015 §5.5** | Configurable scenario selection via environment variable constitutes a testable and traceable software unit requirement. |

---

## 9. Risks & Mitigations (ISO 14971 Format)

| Risk | Likelihood | Severity | Risk Control |
|---|---|---|---|
| Scenario drift from real clinical vitals profiles | Medium | High (invalid inference testing) | Clinically reviewed Synthea reference profiles document acceptable ranges |
| MQTT broker crash silently starves downstream services | Low | High (no inference, no alarm) | Docker healthcheck + `depends_on: service_healthy` in compose |
| Schema drift between publisher and contract | Medium | High (downstream parse failures) | Contract vendoring + CI drift-check workflow |
| Publish interval misconfigured to 0 | Low | Medium (CPU saturation) | Input validation guard at startup |

---

## 10. Dependencies

| Dependency | Repo | Note |
|---|---|---|
| Telemetry Contract | `medtech-telemetry-contract` | Schema vendored at build time; drift-check on release |
| Edge Analytics | `medtech-edge-analytics` | Subscribes to `medtech/vitals/latest` |
| Clinician UI | `medtech-clinician-ui` | Subscribes to `medtech/vitals/latest` |
| Platform | `medtech-platform` | Pinned image tag in `docker-compose.yml` |
| Telemetry Cloud | `medtech-telemetry-cloud` | Bridges MQTT → FastAPI ingestion endpoint |

---

## 11. Open Questions

1. Should multi-patient simulation (multiple concurrent patient streams) be supported in v3.x?
2. Should the sepsis progression path support configurable onset-stage duration (e.g., 30 min vs 2 hr trajectories)?
3. Should the publisher emit to a secondary `medtech/vitals/history` topic for time-series consumers?

## Evidence of Learning

To ensure clinical realism and outcome reliability, the following evidence packages have been added:
- [Clinical Realism Validation Protocol](../validation/CLINICAL_REALISM_PROTOCOL.md)
- [Validation Results Template](../validation/REALISM_RESULTS_TEMPLATE.md)
- [Outcome Metrics](../product-evidence/OUTCOME-METRICS.md)
- [Discovery Log](../product-evidence/DISCOVERY-LOG.md)
