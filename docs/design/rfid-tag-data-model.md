# Design Document: RFID Tag Data Model

**Date:** 2026-05-02
**Status:** proposed
**Related:** [data-models.md](../data-models.md), [telemetry-and-location.md](telemetry-and-location.md), [asset-tracking-gap-analysis.md](asset-tracking-gap-analysis.md), [mobile-carriers-and-manifests.md](mobile-carriers-and-manifests.md) (extends `binding_kind` with the `device` value for mobile-reader carrier semantics), [docs/refs/edge-hardware-and-rfid-primer.md](../refs/edge-hardware-and-rfid-primer.md) (RFID 101, EPC scheme overview, sensor-tag vendors)

---

## 1. Problem Statement

Today the `tag_reads` table has a single `tag_id` text column. That conflates several distinct things an RFID tag actually carries:

- **TID** — the immutable factory-programmed Tag Identifier.
- **EPC** — the writable Electronic Product Code (the business identifier).
- **User memory** — variable-size, customer-writable bank for application data (expiration, batch, lot, etc.).
- **Sensor data** — for sensor-enabled tags (e.g., Axzon Magnus-S, RFMicron, Asygn, ams AS321x, Farsens), readings like temperature or moisture are returned **as part of the inventory response**, not as a separate telemetry event.

Without first-class fields, the platform cannot:

- Distinguish "tag whose EPC was reprogrammed" from "different tag" (TID is the only stable identity).
- Decode EPCs into GS1 schemes (SGTIN, SSCC, GIAI) for ERP joins.
- Capture cold-chain temperature reported *by the tag itself* (vs. the reader's ambient sensor).
- Surface user-memory data (expiration, batch) consistently across devices.

This document defines what we capture, where it lands, and how it relates to the existing `device_telemetry` stream.

---

## 2. Background — RFID Memory Model (EPC Gen2 / ISO 18000-63)

A Gen2 tag has four memory banks:

| Bank | Name | Writable | Typical Size | Contents |
|------|------|----------|--------------|----------|
| 00 | **Reserved** | yes (with password) | 64 bits | Kill password, access password |
| 01 | **EPC** | yes | 96–496 bits | PC bits + EPC + (optional XPC) |
| 10 | **TID** | factory-locked | 32–96+ bits | Mask Designer ID, tag model number, unique serial |
| 11 | **User** | yes | 0 – 8 KB | Application data (TLV-encoded by GS1 EPCIS or vendor format) |

### 2.1 EPC encoding schemes (GS1)

Common patterns we should be able to recognize and parse:

| Scheme | Length | Carries | Example use |
|---|---|---|---|
| `SGTIN-96` | 96 bits | GTIN + serial | Item-level retail |
| `SGTIN-198` | 198 bits | GTIN + 20-char serial | Item-level with long serial |
| `SSCC-96` | 96 bits | Logistics unit (pallet/case) | Warehouse |
| `GIAI-96` / `GIAI-202` | 96 / 202 bits | Returnable asset | Tools, pallets |
| `GRAI-96` / `GRAI-170` | 96 / 170 bits | Returnable asset + serial | Containers |
| `Raw` | any | Vendor-specific | Catch-all |

EPCs are typically transmitted as a **hex string** by readers. We store both the raw hex and the decoded scheme + parts.

### 2.2 Sensor-enabled tags

Two families:

1. **Self-tuning passive sensors (e.g., RFMicron / Axzon Magnus-S)** — temperature/moisture is encoded into the EPC's lower bits or returned as `OnChip RSSI` plus a calibration table. The reader produces a single inventory event that contains both the tag identity and the sensor reading.
2. **Battery-Assisted Passive (BAP) sensors with custom commands (e.g., ams AS321x, Asygn AS321x, Farsens)** — sensor values fetched via vendor-specific Gen2v2 commands, returned alongside or after the inventory.

In both cases, **the sensor value is intrinsically tied to a single tag read**, not a free-standing device telemetry stream. We must capture it in a way that preserves that tie.

---

## 3. Decisions

### D1. Capture TID and EPC separately

`tag_reads` gains `epc` (the writable business identifier) and `tid` (the immutable factory identifier). Existing `tag_id` is retained as the "primary identifier the application uses" — by default this equals `epc`, but the value is set by the ingestion pipeline so callers can opt for `tid` per-tenant.

### D2. Store raw + decoded EPC

Store `epc_hex` (the wire value) and a structured `epc_decoded` JSONB with the scheme name and parsed parts. Decoding is best-effort; unknown schemes fall through with `{"scheme": "raw"}`.

### D3. User memory is opaque + decoded

Store `user_memory_hex` (raw) and `tag_data` (JSONB) for the decoded fields the device or backend extracted (expiration, batch, lot, etc.). Decoding rules are vendor- or tenant-defined; we do not impose a single schema.

### D4. Tag-borne sensor data lands in **`device_telemetry`** with provenance

Sprint 14 already introduces `device_telemetry`. Tag-borne sensor readings (temperature, moisture, etc.) are written there with:

```json
metadata = {
  "source": "tag",
  "epc": "30340…",
  "tid": "E2801160…",
  "tag_read_id": "<uuid of the originating tag_read row>"
}
```

This keeps the unified time-series store, lets rules engine treat tag-borne and device-borne temperature uniformly, and avoids duplicating sensor pipelines. The originating `tag_read` row is the join key for "what tag/asset produced this reading."

For very small inline values (e.g., a single temperature point alongside the inventory), we **also** mirror the value into `tag_reads.tag_data` as `{"temperature_c": 4.2, "moisture_pct": …}` so a one-row query answers "show me the latest read for this tag" without a join. The mirror is convenience; `device_telemetry` remains the source of truth for analytics.

### D5. Antenna / port number is structured

Today antenna is sometimes stuffed into `sensor_data`. Promote `reader_antenna` to a typed column on `tag_reads` so dedup, ENTER/EXIT, and signal-quality analytics use it without JSON access.

### D6. Backward compatibility

- All new columns are nullable. Existing producers (reference edge client, simulator, third-party HTTP callers) that send only `tag_id` continue to work; the ingestion service copies `tag_id` → `epc` when EPC is absent for a transitional period (one minor version), then warns.
- `Pydantic` schemas: new fields optional with sensible defaults. No breaking change to `POST /tag-reads`.

---

## 4. Data Model Changes (additive — bundled into migration 016)

### 4.1 New `tag_reads` columns

| Column | Type | Notes |
|---|---|---|
| `epc` | VARCHAR(256) | Decoded EPC string (URI form, e.g. `urn:epc:id:sgtin:0614141.012345.6789`); indexed |
| `epc_hex` | VARCHAR(128) | Raw wire-format EPC hex |
| `epc_scheme` | VARCHAR(32) | `sgtin-96` \| `sgtin-198` \| `sscc-96` \| `giai-96` \| `giai-202` \| `grai-96` \| `grai-170` \| `raw` |
| `epc_decoded` | JSONB | Parsed parts: `{ "scheme": …, "company_prefix": …, "item_ref": …, "serial": … }` |
| `tid` | VARCHAR(64) | Factory-programmed TID hex; indexed |
| `user_memory_hex` | TEXT | Raw bank-11 hex (truncated to first 4 KB if larger) |
| `tag_data` | JSONB | Decoded user-memory + inline sensor mirrors |
| `reader_antenna` | SMALLINT | Antenna / port number, 0–255 |

Existing `tag_id` keeps its meaning (primary identifier used by the application — defaults to `epc`).

### 4.2 Indexes

```sql
CREATE INDEX ix_tag_reads_epc       ON tag_reads (tenant_id, epc, timestamp DESC);
CREATE INDEX ix_tag_reads_tid       ON tag_reads (tenant_id, tid, timestamp DESC);
```

The TID index is the join target for "is this the same physical tag even if EPC was rewritten?"

### 4.3 Asset binding (Sprint 15) — bind by EPC **or** TID

`asset_tag_bindings.tag_id` becomes ambiguous if tags get re-encoded. Sprint 15 adds a `binding_kind` column (`'epc' | 'tid'`) so operators can choose the more stable identifier per asset class:

| binding_kind | When to use |
|---|---|
| `epc` | Static, never-rewritten EPC encoding (most retail / GS1 deployments) |
| `tid` | Tags get re-encoded across lifecycle (returnable assets, leased tools) |

Active binding lookup at ingest time tries TID first, then EPC, when both are present.

### 4.4 Security model — what each `binding_kind` actually defends against

`binding_kind` is sometimes mistaken for an anti-counterfeit feature. It is not, except in a narrow sense. The table below makes the threat-model boundary explicit so operators (and our own sales/support docs) don't over-claim:

| Binding kind | Resists | Does *not* resist | Notes |
|---|---|---|---|
| `epc` | Casual mis-identification when EPCs are stable | EPC re-encoding (legitimate or malicious), tag cloning, RF replay | Lowest-trust binding; appropriate for environments where EPCs are sealed-commission and never re-written |
| `tid` | Operator re-encoding of EPC; legitimate-rebind churn | TID cloning by capable adversaries, RF replay, hardware emulation | **Stronger than `epc`, not cryptographic.** Useful for returnable-asset and leased-tool fleets where EPCs change but the chip doesn't |
| `device` | Spoofing of *which device sourced a read* (binding is to a device UUID, not a tag) | N/A — orthogonal to tag-counterfeit threats; complements them | Used by mobile-carrier semantics ([mobile-carriers-and-manifests.md](mobile-carriers-and-manifests.md) §4); device authenticity is enforced by the device-identity layer ([identity-device-provisioning.md](identity-device-provisioning.md), ADR-011) |
| Gen2v2 Authenticate **(roadmap, gated)** | Tag cloning, RF replay (cryptographic challenge-response) | Advanced physical attacks (chip de-cap, side-channel) | Requires Gen2v2-capable tags + readers + per-tag key management. See [roadmap.md](../roadmap.md) backlog. |

**Operator guidance.** If your threat model includes a motivated counterfeiter, none of `epc` / `tid` / `device` is sufficient — you need Gen2v2 Authenticate. If your threat model is "honest operators occasionally swap inlays or reflash EPCs", `tid` is the right choice. If neither applies, `epc` is fine and cheapest.

**What this means for our docs and pitch.** TID-binding is documented as *"stronger than EPC, not cryptographic."* Anti-counterfeit and chain-of-custody compliance (DSCSA, EU FMD) are not v1 features; they ride with the Gen2v2 backlog item.

---

## 5. EPC Decoder

A pure-Python module `tagpulse.rfid.epc` decodes the common schemes. It:

- Takes the raw hex or URI form.
- Returns `(scheme, decoded_dict)` or `('raw', {})` on unknown.
- Has no external dependencies (decoder fits in ~300 LOC).
- Is unit-tested against the GS1 EPC TDS examples.

If we later need exotic schemes (ITIP, GLN, GDTI), we add per-scheme parsers without touching the table shape.

---

## 6. Ingestion Pipeline Updates

For every accepted `tag_read`:

1. If `epc_hex` is present and `epc` is missing, decode → fill `epc`, `epc_scheme`, `epc_decoded`.
2. If `tag_id` is missing, set `tag_id = epc` (or tenant default).
3. If `tag_data` contains keys mapped by the device's `telemetry_model` (e.g., `temperature_c`), emit corresponding `device_telemetry` rows with `metadata.source='tag'` and `metadata.tag_read_id=<row id>`.
4. If `tid` is present and the asset's binding is by TID (Sprint 15), use it for asset lookup; else fall back to `epc`.

Validation errors (malformed hex, unknown scheme when scheme is required) follow the existing dead-letter path with `reason='tag_decode_failed'`.

---

## 7. UI Implications

| Surface | Change |
|---|---|
| Data Explorer | Show `epc` (default), with toggle to also display `tid`. Filter by EPC scheme. |
| Asset detail | Display all bound identifiers with their `binding_kind`. |
| Device detail | "Last read" panel surfaces `epc`, `tid`, `tag_data` keys, and inline sensor values. |
| Telemetry tab (Sprint 14) | Tag-borne sensor readings get a "source: tag" badge with click-through to the originating read. |
| Rules wizard | Threshold conditions can target `tag_data.temperature_c` (or any `device_telemetry.metric_name`) — same UX. |

UI parity bundled with the same Sprint 14 release.

---

## 8. Out of Scope

- **Write path** (encoding tags from the platform — i.e., `POST /tag-reads/encode-job`) — backlog G8 (cloud-to-device commands).
- **Cryptographic tag authentication** (Gen2v2 Untraceable / Authenticate) — defer; orthogonal to data model.
- **GS1 EPCIS event format compliance** — design only mentions it; full EPCIS interop is a future integration target.
- **NFC / HF tags** — Gen2 UHF only for v1.

---

## 9. Decisions & Open Questions

### Resolved

| # | Question | Decision |
|---|---|---|
| 1 | Deprecate `tag_id` in favor of `epc`? | **Yes**, with a one-version warning window. Keep `tag_id` working for external integrations during deprecation; emit logs when it's used. |
| 2 | User-memory size cap? | **4 KB inline cap with silent truncate + `tag_data._truncated=true` flag.** Quarantining a read because user memory is large would lose the EPC + location, which is the data we care about most; quarantine is for malformed / unknown data, not too much data. Add a Prometheus counter `tag_data_truncations_total{tenant}` so unexpected truncation rates are visible \u2014 unexpected nonzero rates usually mean a misconfigured tag fleet (wrong memory bank or size assumption). An overflow-table opt-in for fidelity-critical tenants (sensor-tag-on-implant, document-on-tag) is a backlog item, not v1. |\n| 3 | Sensor mirror: all `tag_data` values or declared only? | **Declared-only** \u2014 only metrics declared in `telemetry_models` mirror into `device_telemetry`. Keeps ingestion deterministic and the quarantine path meaningful. |
| 5 | Decoder library: write our own or take a dependency? | **Write our own** for the common GS1 schemes (SGTIN-96/198, SSCC-96, GIAI-96/202, GRAI-96/170). Small, controlled, no transitive risk. Reconsider if customers bring exotic schemes. |

### Still open

4. **TID-binding framing.** TID is globally unique by spec, but cheap clones exist. Is "bind by TID" a **security claim** (anti-counterfeit, audit-defensible) or just a **convenience** (fewer rebinds when EPCs are re-encoded)? Affects what we promise customers and how we document anti-counterfeit features. Recommend documenting as *"stronger than EPC, not cryptographic"* until Gen2v2 Authenticate support lands.
