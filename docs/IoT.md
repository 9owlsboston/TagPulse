# IoT Application Architecture & Design

End-to-end reference for IoT infrastructure, from devices to business applications.

---

## 1. Layered Architecture (Big Picture)

IoT solutions follow a **layered architecture**:

```
Devices/Edge → Connectivity → Ingestion → Processing → Storage → Applications → Monitoring/Security
```

The layers break down as:

- **Edge / Devices layer** — sensors, actuators, gateways
- **Connectivity layer** — protocols, cloud gateway, device registry
- **Cloud services layer** — analytics, storage, applications

---

## 2. Core Building Blocks

### 2.1 Devices & Edge Layer

- Sensors, cameras, industrial equipment, etc.
- Connection modes:
  - **Direct to cloud** via MQTT, AMQP, or HTTPS
  - **Via edge gateway** for protocol translation, filtering, or local inference
- Key capability: local processing (filtering, ML inference, protocol translation) that works even with intermittent connectivity

---

### 2.2 Connectivity & Ingestion

#### Cloud Gateway (IoT Hub / Broker)

- Central message broker that manages device connections at scale
- Supports:
  - Device-to-cloud telemetry
  - Cloud-to-device commands
  - Device twins / shadow state (state synchronization)
- Enables **bi-directional communication at scale**
- *Examples: AWS IoT Core, Azure IoT Hub, HiveMQ, EMQX*

#### Device Provisioning Service

- Zero-touch onboarding — automatically registers and assigns devices to the correct hub/broker
- Critical for **large fleet** scenarios (100K+ devices)
- *Examples: AWS IoT Device Provisioning, Azure DPS, custom provisioning services*

---

### 2.3 Data Processing Layer (Hot / Warm / Cold Paths)

| Path          | Purpose                                | Typical Technologies                                    |
| ------------- | -------------------------------------- | ------------------------------------------------------- |
| **Hot path**  | Real-time processing (alerts, actions) | Stream processors, serverless functions                  |
| **Warm path** | Near real-time analytics               | Time-series databases, interactive query engines         |
| **Cold path** | Batch + historical analytics           | Data lakes, data warehouses, batch processing frameworks |

Different latency requirements drive architecture choices.

---

### 2.4 Storage Layer

| Tier             | Use Case          | Typical Technologies                                |
| ---------------- | ----------------- | --------------------------------------------------- |
| **Hot storage**  | Low-latency reads | Document DBs, in-memory stores, key-value databases |
| **Cold storage** | Archival / bulk   | Object storage, data lakes                          |
| **Time-series**  | Temporal queries  | InfluxDB, TimescaleDB, managed TSDB services        |

Separating tiers improves both cost and performance.

---

### 2.5 Application & Integration Layer

- **APIs / App hosting** — REST/gRPC services, API gateways
- **Workflow orchestration** — event-driven automation, integration platforms
- **Business integration** — CRM, ERP, ticketing systems

This is where **IoT data becomes business value**.

---

### 2.6 Visualization / UI Layer

- Dashboards and BI tools (Grafana, Superset, commercial BI)
- Custom web/mobile applications
- Digital twin visualizations

---

### 2.7 Monitoring & Security (Cross-cutting)

- **Observability** — metrics, logs, distributed tracing
- **IoT security** — threat detection, firmware integrity scanning
- **Identity & access** — RBAC, per-device authentication
- **Device auth** — X.509 certificates, SAS tokens, OAuth 2.0
- **Network isolation** — private endpoints, VPN, firewall rules

---

## 3. End-to-End Data Flow

```
1. Device sends telemetry ──► Cloud Gateway
2. Gateway routes messages (rules-based / content-based routing)
3. Data branches to:
   ├── Stream processor (real-time alerts & actions)
   └── Storage (historical retention)
4. Analytics engine generates insights
5. Actions triggered:
   ├── Alerts / notifications
   └── Commands back to devices
```

Content/metadata-based routing at the gateway enables selective fan-out without additional middleware.

---

## 4. Architecture Patterns

### Pattern 1: Cloud-Connected

- Devices connect directly to cloud gateway
- Simple, scalable, low operational overhead
- Best for: consumer IoT, always-connected devices

### Pattern 2: Edge-Connected

- Devices → Edge gateway → Cloud
- Local processing + store-and-forward buffering
- Best for: industrial/OT, low-latency requirements, intermittent connectivity

---

## 5. Design Considerations

### Scale

- Cloud gateways support millions of concurrent devices
- Use provisioning services for automated onboarding
- Use hub partitioning / sharding as fleet grows
- Stagger provisioning to avoid thundering-herd connection storms

### Reliability

- Design for intermittent connectivity
- Store-and-forward at the edge layer
- Multi-region failover for the cloud tier

### Security

- Per-device identity (unique keys or certificates)
- Network isolation (private endpoints, segmentation)
- Secure firmware/OTA update pipeline

### Data Strategy

- Define what is real-time vs batch up front
- Set retention policies per tier
- Optimize cost by routing data to the appropriate storage tier early

### Operations

- Device lifecycle management (provision → update → decommission)
- OTA firmware updates with rollback capability
- Monitoring + alerting across all layers

---

## 6. Reference Stack

```
Devices / Sensors
     ↓
Edge Gateway (optional)
     ↓
Cloud Gateway + Provisioning Service
     ↓
Message Routing / Event Bus
     ↓
Stream Processor / Serverless Functions
     ↓
Data Lake / Document DB
     ↓
Analytics Engine / ML Platform
     ↓
API Gateway / App Services
     ↓
Dashboards / Applications
```

---

## 7. Enterprise Focus Areas

For large-scale, telemetry-heavy, distributed systems, prioritize:

- **Ingestion scalability** — throughput units, partitioning, back-pressure handling
- **Data pipeline design** — hot/warm/cold separation with clear SLAs
- **Cost control** — storage tiering, edge-side filtering, data sampling
- **Security posture** — per-device identity, network isolation, certificate rotation
- **Operational maturity** — device lifecycle automation, monitoring, incident response

---

## 8. Scaling at 100K+ Devices: Deployment Stamps

### The Stamp Model

For 100K+ devices, the recommended production pattern is **deployment stamps** (aka scale units / cells). Each stamp supports a bounded device population and contains:

- A cloud gateway instance
- A routing/streaming endpoint
- Processing and storage components

Benefits:

- **Horizontal scaling** — replicate stamps as fleet grows instead of scaling a single instance
- **Fault isolation** — a failure in one stamp doesn't cascade
- **Controlled rollout** — deploy changes to one stamp at a time (ring-based deployment)

### Stamp Topology

```
                            ┌──────────────────────────┐
                            │  Provisioning Service     │
                            │  (Global / Shared)        │
                            │  Routes devices → stamps  │
                            └────────────┬─────────────┘
                                         │
                     ┌───────────────────┼───────────────────┐
                     ▼                                       ▼
    ┌─────────── Stamp A ───────────┐       ┌─────────── Stamp B ───────────┐
    │ Devices ──► Cloud Gateway     │       │ (Same pattern)                │
    │               │               │       │                               │
    │     ┌─────────┼─────────┐     │       │ Gateway + Routing + Processing│
    │     ▼         ▼         ▼     │       │ + Storage + Analytics         │
    │  Hot Path  Streaming  Storage │       │                               │
    │ Processing  Endpoint   /Lake  │       │                               │
    └───────────────────────────────┘       └───────────────────────────────┘

    (Add stamps linearly as device population grows)
```

### Partitioning Approach

**Global control plane:**

- One provisioning service (or small set) per environment/region to route devices to the correct stamp

**Per stamp (scale unit):**

- Cloud gateway for device identity + command/control + state sync
- Message routing to hot path processing, storage capture, and event-driven workflows
- Prefer ordered routing over pub/sub when telemetry ordering matters

**Downstream analytics:**

- Stream processing + data lake/warehouse aggregated across stamps for cross-fleet insights

---

## 9. IoT Gateway vs Event Streaming vs Kafka: Decision Matrix

### Key Distinctions

| Component                   | Purpose                                              | Characteristics                                   |
| --------------------------- | ---------------------------------------------------- | ------------------------------------------------- |
| **IoT Gateway / Broker**    | Device connectivity + device relationship management | Bi-directional, per-device identity, device mgmt  |
| **Event Streaming Service** | High-throughput event ingestion (partitioned log)     | Append-only log, consumer groups, analytics-first |
| **Apache Kafka**            | Distributed streaming platform / ecosystem           | Self-managed or managed, broad ecosystem          |

Most IoT gateways use an event streaming backend internally for the telemetry path — they are complementary, not competing.

### When to Use Each

**Use an IoT Gateway when you need:**

- Per-device identity and security posture
- Cloud-to-device commands and device state management
- IoT-specific semantics (twins/shadows, provisioning, device lifecycle)

**Use an Event Streaming Service when you need:**

- Pure high-throughput ingestion from a bounded set of producers
- Log-style consumer patterns with multiple independent readers
- Deep integration into analytics and data pipelines

**Use Kafka when you need:**

- An existing Kafka ecosystem with established tooling
- Multi-datacenter replication (MirrorMaker, Confluent Replicator)
- Maximum control over broker configuration and topic management
- Note: many cloud providers offer Kafka-compatible managed endpoints that let existing Kafka clients connect without running brokers

### Decision Matrix

| Requirement                                                 | Best Fit                | Why                                                                       |
| ----------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------- |
| Per-device identity, secure onboarding, C2D commands        | IoT Gateway             | Built for device connectivity, bi-directional comms, device-level identity |
| Massive event ingestion from apps/services, analytics-first | Event Streaming Service | Partitioned consumer model, high throughput, analytics integration         |
| Existing Kafka clients, want managed service                | Managed Kafka endpoint  | Config-only migration, no broker management                               |
| Controlled scale-out for 100K+ devices                      | Deployment stamps       | Discrete units with bounded population; add stamps to grow                |
| Ordered message routing vs event fan-out                    | Gateway routing         | Native routing preserves order; pub/sub systems may not guarantee ordering |

### Retention Considerations

- Event streaming services are **not permanent data stores** — typical retention is 1–90 days depending on tier
- IoT gateway message retention is typically **7 days** at most
- For long-term retention, always capture/archive to durable storage (object store or data lake)

---

## 10. Cloud Gateway Capability Checklist

A production IoT cloud gateway should support:

- Device-to-cloud telemetry ingestion
- Per-device identity and authentication
- Message routing with content-based rules
- Message enrichment (append metadata at the gateway)
- HTTP, AMQP, and MQTT protocol support
- Device provisioning integration
- Monitoring and diagnostics
- Cloud-to-device messaging
- Device twins / shadow state management
- Edge runtime integration
- Plug-and-play / device model support

### Typical Device Management API Surface

| Category          | Operations                                        |
| ----------------- | ------------------------------------------------- |
| Device registry   | Create, get, update, delete device                |
| Module management | Create, get, update, delete module                |
| Device twins      | Get twin, update twin                             |
| Direct methods    | Invoke method on device                           |
| Jobs              | Create, get, cancel scheduled job                 |
| Bulk operations   | Batch create/update/delete                        |
| File upload       | Generate upload URI, update upload status          |
| Notifications     | Receive, acknowledge, abandon device notification |
| Telemetry         | Send device event, send module event              |
| Statistics        | Registry stats, service stats                     |
| Queries           | Query device registry, query jobs                 |

---

## Summary

- IoT architecture is **layered**: devices → gateway → processing → storage → apps
- The **cloud gateway + provisioning service** form the control plane
- Use **edge + cloud hybrid** for real-world enterprise workloads
- Design around **scale**, **latency paths** (hot/warm/cold), **security**, and **device lifecycle**
- At 100K+ devices, adopt the **deployment stamp** pattern for linear horizontal scaling
