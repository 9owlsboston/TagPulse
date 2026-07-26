# Strategy: edge-AI architecture — models at the gateway

**Date:** 2026-07-26
**Status:** exploration
**Related:** [ai-landscape.md](ai-landscape.md), [sensor-wedges.md](sensor-wedges.md), [ADR-011 (device identity roadmap)](../adr/011-device-identity-roadmap.md), [TagPulse-Mobile](https://github.com/9owlsboston/TagPulse-Mobile)

---

## Summary

The gateway's differentiator ([ai-landscape.md](ai-landscape.md) §2) is **inference at the
edge** — but the product isn't "a model," it's a **model lifecycle**: where inference sits in
the pipeline, how models are packaged for a footprint-constrained device, how they update
**independently of the app**, and how the edge feeds a retraining flywheel. This note sketches
that architecture on the existing `GatewayDriver` seam. Runtime/format specifics are directional
(`unverified` on exact library choices).

## Where inference sits: an inference stage on the driver seam

Today a `GatewayDriver` turns a raw sensor read into an `Observation`. Edge-AI inserts an
**inference stage** in that path:

```
raw sensor read → [ inference stage ] → Observation → outbox → relay
                     │
                     ├─ filter    (drop boring frames — bandwidth lever)
                     ├─ classify  (label the event: idling / defect / anomaly)
                     └─ enrich    (attach a score/confidence to the Observation)
```

This keeps the outbox/relay untouched — the model is a **pre-processor**, and its output rides
the existing `Observation → TagReadCreate` mapping (e.g. as a metric or `tag_data` field).

## Runtimes & footprint

The [footprint budget](https://github.com/9owlsboston/TagPulse-Mobile) is a hard constraint, so
prefer platform-native runtimes over bundling engines:

- **Vision** — ML Kit is already bundled (barcode/VIN); extend to on-device classification/OCR.
- **General inference** — LiteRT/TFLite + ONNX Runtime Mobile, with NNAPI (Android) / Core ML
  (iOS) delegates; models **quantized** (int8) and tiny.
- **Split by cost/privacy** — edge does cheap/private/offline (pre-filter, anomaly, vision);
  cloud does heavy/accurate/retrainable. Edge is the *funnel*, cloud is the *microscope*.

## Model distribution (the part people forget)

Models must update **without an app-store release** — app-store latency kills iteration. Treat a
model as a **signed, versioned artifact** the device pulls:

- **Model registry endpoint** on TagPulse (or Firebase ML / a blob store) serving signed model
  bundles keyed by `(tenant, device_profile, model, version)`.
- **Per-device model assignment** — the device registry already carries per-device config
  profiles; extend them with a model channel (stable / canary) for **staged rollout + rollback**.
- **Signed + verified** — model artifacts are a code-injection surface; sign them and verify on
  device (reuse the device-identity trust chain, [ADR-011](../adr/011-device-identity-roadmap.md)).

## The data flywheel

Edge-AI is only a moat if it *improves*: the edge uploads **hard/uncertain cases** (low-confidence
or novel), which feed **retraining**, which redeploys via the registry above. The
demo/simulation foundation (Sprints 58–59) bootstraps before real labels exist; active-learning
sampling grows the labelled set cheaply once devices are live.

## Model observability & governance

- **Models as a monitored subject** — per-model accuracy/latency/drift is itself telemetry; the
  platform that monitors devices should monitor its models the same way (drift alert → rules).
- **Per-tenant vs shared models** — RLS covers *data*, not *models*. Decide per-use-case: shared
  base model + per-tenant fine-tune is a common middle path. On-device means the **artifact sits
  on customer hardware** — an IP-exposure consideration for proprietary models.
- **Federated option** — for privacy-sensitive verticals, train across tenants **without pooling
  raw data** (ties to [data-monetization.md](data-monetization.md)).

## Risks

- **Runtime fragmentation** — iOS (Core ML) vs Android (NNAPI/LiteRT) means two model toolchains;
  budget for it.
- **Footprint** — every model competes with the install-size/RAM budget; quantize or don't ship.
- **OTA-model security** — unsigned model delivery is as dangerous as unsigned code (see the
  downlink-auth concern in [actuation-control-loop.md](actuation-control-loop.md)).
- **No edge GPU** — inference must be CPU/NPU-friendly; heavy models stay in the cloud tier.
- **Silent drift** — a model that degrades without observability is worse than no model; drift
  monitoring is table stakes, not a follow-up.
