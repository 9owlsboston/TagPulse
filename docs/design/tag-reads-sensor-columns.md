# Tag Reads page — Antenna, Temperature, Humidity columns

> Status: **Planning** — design captured on a chore branch
> (`chore/tag-reads-sensor-columns`). Lightweight: this is a **UI-only**
> change touching a single page, so it is *below* the 3+-component
> design-doc threshold — this doc exists to record the discussion and the
> non-obvious data-shape decision before code ships. No code on this branch.
> Implementation lands as a separate PR in **TagPulse-UI**.

## Why now

The Tag Reads page ([`TagPulse-UI/src/pages/telemetry/TagReads.tsx`]) renders
Tag ID, EPC, Scheme, EPC (hex), TID, User Memory, Device, Timestamp, Signal,
Latitude, and Longitude — but **not** Antenna, Temperature, or Humidity, even
though operators expect those for an RFID + cold-chain fleet.

The data is **already in the API contract** — this is purely a UI rendering
gap, not a backend or schema gap:

| Field | Where it lives in `TagReadResponse` | Status |
|---|---|---|
| **Antenna** | `reader_antenna` — typed `number \| null` (0–255) | already in API + UI type |
| **Temperature** | nested in `sensor_data` (free-form JSONB dict) | already in API + UI type |
| **Humidity** | nested in `sensor_data` (free-form JSONB dict) | already in API + UI type |

So **no backend change, no `openapi.json` regen** is required. `reader_antenna`
and `sensor_data` are both present on the generated UI `TagReadResponse` type.

## The one non-obvious constraint — two key conventions for the same value

`sensor_data` is a free-form dict, and the platform writes the **same physical
quantity under different keys depending on the ingestion path**:

| Source | Temperature key | Humidity key |
|---|---|---|
| **Real edge devices** (wire-format v2 / MQTT) — [`mqtt_subscriber.py` `_wm_sensor_data`] | `temperature_c` | `humidity_pct` |
| **Simulators & demo seeds** (HTTP) — `sim_loop.py`, `backfill_history.py`, `seed_alerts.py` | `temperature` | `humidity` |

A single-key column would therefore show data for **either** production devices
**or** the demo, never both. This shapes the whole feature: the columns must
resolve a **fallback chain**, not a single `dataIndex`.

This also resolves an earlier worry — real cold-chain temperatures **do** land
in `sensor_data` (as `temperature_c`), not only in the separate `tag_data`
blob, so reading `sensor_data` alone is sufficient.

## Decisions (resolved)

| # | Question | Decision |
|---|----------|----------|
| D1 | Key resolution | **Fallback chain.** temp = `sensor_data.temperature ?? sensor_data.temperature_c`; humidity = `sensor_data.humidity ?? sensor_data.humidity_pct`. Covers both real devices and demos. |
| D2 | Unit labels | **Static headers** "Temp (°C)" / "Humidity (%)". All current producers are metric; `°F` handling is explicitly out of scope. |
| D3 | Sortability | **Sortable**, via an explicit comparator that extracts the same resolved value (free-form dict → no automatic `dataIndex` sort). |
| D4 | Default visibility | **All three default-visible** (the point of the request). Not added to `DEFAULT_ADVANCED_COLUMNS`. |
| D5 | Scope | **Only** Antenna, Temp, Humidity. `battery_pct` / `cnt`, a catch-all "Other sensors" column, and a chart-mode temp series are out of scope. |

## Design

**Scope:** TagPulse-UI only — [`src/pages/telemetry/TagReads.tsx`].

**Three new columns, all default-visible:**

| Column | Source | Render | Sort |
|---|---|---|---|
| **Antenna** | `reader_antenna` (typed scalar) | `v ?? '—'` | numeric |
| **Temp (°C)** | `sensor_data.temperature ?? sensor_data.temperature_c` | numeric guard → `.toFixed(1)`, else `—` | by resolved value |
| **Humidity (%)** | `sensor_data.humidity ?? sensor_data.humidity_pct` | numeric guard → `.toFixed(1)`, else `—` | by resolved value |

**Implementation notes:**

- **Shared resolver** (reused by render, sorter, and CSV):
  ```ts
  const num = (v: unknown): number | undefined => (typeof v === 'number' ? v : undefined);
  const temp = (r: TagReadResponse) => num(r.sensor_data?.temperature) ?? num(r.sensor_data?.temperature_c);
  const humidity = (r: TagReadResponse) => num(r.sensor_data?.humidity) ?? num(r.sensor_data?.humidity_pct);
  ```
  The numeric type guard is required because `sensor_data` is
  `Record<string, unknown> | null` — `.toFixed()` on an `unknown` won't compile.
- **Sorters** need an explicit comparator (no auto `dataIndex` sort on a derived
  value): `(a, b) => (temp(a) ?? -Infinity) - (temp(b) ?? -Infinity)`.
- **Placement:** Antenna next to Signal (both reader-side metadata); Temp and
  Humidity immediately after.
- **Column config (ADR-032):** register the keys `reader_antenna`,
  `sensor_temperature`, `sensor_humidity` so they flow through
  `applyColumnConfig` / `chooserCandidates` and appear in the `ColumnChooser`.
  Keep them out of `DEFAULT_ADVANCED_COLUMNS` so they stay default-visible.
- **CSV parity:** add all three to `handleExportCsv` using the same resolver;
  headers `reader_antenna`, `temperature_c`, `humidity_pct`.
- **Tests** ([`TagReads.test.tsx`]): assert both key conventions render, the `—`
  fallback when absent, sort order, and CSV inclusion.

## Out of scope

- Backend / schema / `openapi.json` change (contract already sufficient).
- `battery_pct`, `cnt`, or a generic catch-all sensor column.
- Chart-mode temperature series.
- Fahrenheit / unit-metadata handling (all current producers are metric).
