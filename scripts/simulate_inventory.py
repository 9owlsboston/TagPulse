#!/usr/bin/env python3
"""Inventory tracking simulator — practical warehouse scenario.

Models a small distribution center for perishable / pharma goods so the
inventory branch (products → lots → stock_items → stock_movements) gets
realistic data exercised end-to-end:

Site / zones
    1 site ("Boston DC") with 4 reader-bound zones, one device each:
        - Receiving Dock      (inbound from suppliers)
        - Cold Storage        (long-dwell holding for cold-chain SKUs)
        - Pick Floor          (orders being assembled)
        - Shipping Dock       (outbound to customers)

Catalog
    4 distinct products spanning food and pharma, each with a *uniquely
    named* lot so the UI doesn't show three rows all called LOT-A:
        - Vaccine-X 0.5 mL vial (cold-chain pharma)        lot VAX-2604-A
        - Milk 1L                                          lot MILK-0501  ← near-expiry
        - Yogurt 4-pack                                    lot YOG-0428-B
        - Cheese 200g                                      lot CHS-0301-K

Stock unit lifecycle
    A finite pool of stock_items per lot is seeded with stable serials
    (so re-running the script reuses existing units instead of inflating
    inventory). Each unit follows a per-unit timeline:

        Receiving (t=0) → Cold Storage (t=Δ1) → Pick Floor (t=Δ2) → Ship (t=Δ3)

    Per-unit deltas are randomized; ~30% of units stop at Cold Storage,
    ~20% reach Pick Floor and stay, ~50% travel all the way to Shipping.
    This produces ``subject.zone_changed`` events at every stage and
    populates ``stock_movements`` with ENTER / TRANSFER / EXIT rows.

Why this is more useful than the old version
    * Distinct lot codes per product (matches real-world labelling).
    * Diverse expirations — Milk lot is < 5 days out so the
      ``stock.expiring_within`` rule actually fires.
    * Stable EPC serials → repeated reads update an existing stock_item
      and emit ``stock_movements``, instead of inflating to thousands of
      one-shot rows.
    * Reads happen at zone-bound readers, so the Stock Levels view shows
      meaningful per-zone counts (not "unzoned").

Usage
    python scripts/simulate_inventory.py \
        --tenant-id <UUID> --units 40 --duration 240 --api-key tp_…

The script is idempotent: re-running re-uses any existing site, zones,
devices, products, lots, and stock_items.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

API_URL = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")

_API_KEY: str | None = None


# --------------------------------------------------------------------------- #
# Catalog definition
# --------------------------------------------------------------------------- #


@dataclass
class CatalogItem:
    """Static product definition consumed by the seeder."""

    sku: str
    name: str
    category: str
    item_ref: str  # 6-digit SGTIN-96 item reference
    lot_code: str
    expires_in_days: int
    units: int  # how many physical units to seed for this lot

    # Filled in by _seed_catalog after API calls.
    product_id: str = ""
    lot_id: str = ""
    gtin: str = ""


COMPANY_PREFIX = "0614141"  # 7-digit GS1 test prefix

CATALOG: list[CatalogItem] = [
    CatalogItem(
        sku="SKU-VAX-X-05ML",
        name="Vaccine-X 0.5 mL vial",
        category="pharma",
        item_ref="200001",
        lot_code="VAX-2604-A",
        expires_in_days=30,
        units=10,
    ),
    CatalogItem(
        sku="SKU-MILK-1L",
        name="Milk 1L",
        category="food/dairy",
        item_ref="100001",
        lot_code="MILK-0501",
        expires_in_days=4,  # near-expiry → triggers stock.expiring_within
        units=12,
    ),
    CatalogItem(
        sku="SKU-YOGURT-4PK",
        name="Yogurt 4-pack",
        category="food/dairy",
        item_ref="100002",
        lot_code="YOG-0428-B",
        expires_in_days=15,
        units=10,
    ),
    CatalogItem(
        sku="SKU-CHEESE-200G",
        name="Cheese 200g",
        category="food/dairy",
        item_ref="100003",
        lot_code="CHS-0301-K",
        expires_in_days=90,
        units=8,
    ),
]


# Zone narrative — order matters: each unit advances through this sequence.
ZONE_PIPELINE: list[tuple[str, str]] = [
    ("Receiving Dock", "DC-Receiving"),
    ("Cold Storage", "DC-ColdStorage"),
    ("Pick Floor", "DC-PickFloor"),
    ("Shipping Dock", "DC-Shipping"),
]


SITE_NAME = "Boston DC"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _headers(tenant_id: str) -> dict[str, str]:
    h = {"X-Tenant-ID": tenant_id}
    if _API_KEY:
        h["Authorization"] = f"Bearer {_API_KEY}"
    return h


def _ok(resp: httpx.Response) -> bool:
    return 200 <= resp.status_code < 300


def _gtin14(company_prefix: str, item_ref: str) -> str:
    ref_short = item_ref[-5:]
    body = "0" + company_prefix + ref_short
    if len(body) != 13 or not body.isdigit():
        raise ValueError(f"invalid gtin13 body: {body}")
    odds = sum(int(c) for c in body[-1::-2])
    evens = sum(int(c) for c in body[-2::-2])
    total = odds * 3 + evens
    check = (10 - (total % 10)) % 10
    return body + str(check)


def _sgtin96_hex(company_prefix: str, item_ref: str, serial: int) -> str:
    """Encode an SGTIN-96 EPC as a 24-hex string (matches tagpulse.rfid.epc)."""
    if len(company_prefix) != 7 or len(item_ref) != 6:
        raise ValueError("test data assumes 7+6 partition")
    header = 0x30
    filter_value = 0b001
    partition = 0b101  # 7+6
    prefix = int(company_prefix)
    item = int(item_ref)
    serial &= (1 << 38) - 1
    bits = (
        (header << 88)
        | (filter_value << 85)
        | (partition << 82)
        | (prefix << 58)
        | (item << 38)
        | serial
    )
    return f"{bits:024x}"


# --------------------------------------------------------------------------- #
# Seeders (idempotent)
# --------------------------------------------------------------------------- #


def _seed_site_and_devices(
    client: httpx.Client, tenant_id: str
) -> tuple[str, list[dict[str, Any]]]:
    """Ensure the site + 4 zone-anchor devices exist. Return (site_id, devices)."""
    headers = _headers(tenant_id)

    # Site.
    sites_r = client.get(f"{API_URL}/sites", headers=headers)
    sites = sites_r.json() if _ok(sites_r) else []
    site = next((s for s in sites if s["name"] == SITE_NAME), None)
    if site is None:
        r = client.post(
            f"{API_URL}/sites",
            headers=headers,
            json={"name": SITE_NAME, "address": "1 Warehouse Way, Boston, MA"},
        )
        if not _ok(r):
            print(f"  FAIL site: {r.status_code} {r.text}")
            sys.exit(1)
        site = r.json()
        print(f"  Created site: {SITE_NAME} ({site['id']})")
    else:
        print(f"  Reusing site: {SITE_NAME} ({site['id']})")

    # Devices — one per zone in ZONE_PIPELINE.
    devices_r = client.get(f"{API_URL}/device-registry", headers=headers, params={"limit": 1000})
    existing_devices = {d["name"]: d for d in (devices_r.json() if _ok(devices_r) else [])}
    devices: list[dict[str, Any]] = []
    for _zone_name, device_name in ZONE_PIPELINE:
        if device_name in existing_devices:
            devices.append(existing_devices[device_name])
            print(f"  Reusing device: {device_name}")
            continue
        r = client.post(
            f"{API_URL}/device-registry",
            headers=headers,
            json={
                "name": device_name,
                "device_type": "rfid_reader",
                "metadata": {"simulated": True, "profile": "inventory-warehouse"},
            },
        )
        if not _ok(r):
            print(f"  FAIL device {device_name}: {r.status_code} {r.text}")
            sys.exit(1)
        devices.append(r.json())
        print(f"  Created device: {device_name}")

    return site["id"], devices


def _seed_zones(
    client: httpx.Client, tenant_id: str, site_id: str, devices: list[dict[str, Any]]
) -> dict[str, str]:
    """Ensure reader-bound zones exist. Return device_id → zone_id map."""
    headers = _headers(tenant_id)
    zones_r = client.get(f"{API_URL}/zones", headers=headers, params={"site_id": site_id})
    existing = {z["name"]: z for z in (zones_r.json() if _ok(zones_r) else [])}

    device_zone: dict[str, str] = {}
    for (zone_name, _device_name), device in zip(ZONE_PIPELINE, devices, strict=True):
        if zone_name in existing:
            zone = existing[zone_name]
            print(f"  Reusing zone: {zone_name}")
        else:
            r = client.post(
                f"{API_URL}/zones",
                headers=headers,
                json={
                    "site_id": site_id,
                    "name": zone_name,
                    "kind": "reader_bound",
                    "fixed_reader_ids": [device["id"]],
                },
            )
            if not _ok(r):
                print(f"  FAIL zone {zone_name}: {r.status_code} {r.text}")
                sys.exit(1)
            zone = r.json()
            print(f"  Created zone: {zone_name}")
        device_zone[device["id"]] = zone["id"]
    return device_zone


def _seed_catalog(client: httpx.Client, tenant_id: str) -> list[CatalogItem]:
    headers = _headers(tenant_id)
    products_r = client.get(f"{API_URL}/products", headers=headers, params={"limit": 1000})
    existing_products = {p["sku"]: p for p in (products_r.json() if _ok(products_r) else [])}

    seeded: list[CatalogItem] = []
    for item in CATALOG:
        item.gtin = _gtin14(COMPANY_PREFIX, item.item_ref)

        # Product.
        product = existing_products.get(item.sku)
        if product is None:
            r = client.post(
                f"{API_URL}/products",
                headers=headers,
                json={
                    "sku": item.sku,
                    "gtin": item.gtin,
                    "name": item.name,
                    "category": item.category,
                    "unit": "each",
                },
            )
            if not _ok(r):
                print(f"  FAIL product {item.sku}: {r.status_code} {r.text}")
                continue
            product = r.json()
            print(f"  Created product: {item.sku}")
        else:
            print(f"  Reusing product: {item.sku}")
        item.product_id = product["id"]

        # Lot.
        lots_r = client.get(f"{API_URL}/products/{item.product_id}/lots", headers=headers)
        lots = lots_r.json() if _ok(lots_r) else []
        lot = next((lot for lot in lots if lot["lot_code"] == item.lot_code), None)
        if lot is None:
            expires_at = datetime.now(UTC) + timedelta(days=item.expires_in_days)
            r = client.post(
                f"{API_URL}/products/{item.product_id}/lots",
                headers=headers,
                json={
                    "lot_code": item.lot_code,
                    "expires_at": expires_at.isoformat(),
                    "manufactured_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
                },
            )
            if not _ok(r):
                print(f"  FAIL lot {item.lot_code}: {r.status_code} {r.text}")
                continue
            lot = r.json()
            print(f"  Created lot: {item.lot_code} (expires in {item.expires_in_days}d)")
        else:
            print(f"  Reusing lot: {item.lot_code}")
        item.lot_id = lot["id"]
        seeded.append(item)

    # Tag-data mapping so ingestion can decode tag_data.lot → lot_code.
    mappings_r = client.get(
        f"{API_URL}/tag-data-mappings", headers=headers, params={"scope_kind": "tenant"}
    )
    existing_mappings = mappings_r.json() if _ok(mappings_r) else []
    if not any(m.get("semantic_field") == "lot_code" for m in existing_mappings):
        r = client.post(
            f"{API_URL}/tag-data-mappings",
            headers=headers,
            json={
                "scope_kind": "tenant",
                "scope_id": None,
                "semantic_field": "lot_code",
                "tag_data_key": "lot",
            },
        )
        if _ok(r):
            print("  Registered tag_data_mapping: tag_data.lot -> lot_code")

    return seeded


# --------------------------------------------------------------------------- #
# Per-unit movement plan
# --------------------------------------------------------------------------- #


@dataclass
class StockUnit:
    """One simulated physical unit moving through the warehouse."""

    item: CatalogItem
    serial: int
    epc_hex: str
    # Schedule: list of (offset_seconds, zone_index) — when to read at which
    # zone in ZONE_PIPELINE. Generated once per simulator run.
    schedule: list[tuple[float, int]] = field(default_factory=list)
    next_step: int = 0

    @property
    def lot_code(self) -> str:
        return self.item.lot_code


def _build_units(catalog: list[CatalogItem], duration: float) -> list[StockUnit]:
    """Generate stock units with stable EPCs and a per-unit movement schedule.

    Serial numbering scheme (stable across runs):
        product_index * 100_000 + unit_index_within_lot
    so re-running the simulator hits the same stock_item rows.
    """
    units: list[StockUnit] = []
    for product_idx, item in enumerate(catalog):
        for unit_idx in range(item.units):
            serial = (product_idx + 1) * 100_000 + unit_idx
            epc_hex = _sgtin96_hex(COMPANY_PREFIX, item.item_ref, serial)

            # Pick a destination stage for this unit.
            r = random.random()
            if r < 0.30:
                final_stage = 1  # stays in Cold Storage
            elif r < 0.50:
                final_stage = 2  # reaches Pick Floor
            else:
                final_stage = 3  # ships out

            # Spread reads across the duration so movement is observable.
            schedule: list[tuple[float, int]] = []
            t = random.uniform(0, duration * 0.10)  # arrival jitter
            for stage in range(final_stage + 1):
                schedule.append((t, stage))
                # Dwell in current stage before moving on.
                if stage < final_stage:
                    dwell = random.uniform(duration * 0.10, duration * 0.30)
                    t += dwell

            units.append(StockUnit(item=item, serial=serial, epc_hex=epc_hex, schedule=schedule))
    random.shuffle(units)
    return units


# --------------------------------------------------------------------------- #
# Read emission
# --------------------------------------------------------------------------- #


def _send_read(
    client: httpx.Client,
    tenant_id: str,
    device_id: str,
    unit: StockUnit,
) -> int:
    body: dict[str, Any] = {
        "device_id": device_id,
        "tag_id": unit.epc_hex,
        "timestamp": datetime.now(UTC).isoformat(),
        "signal_strength": round(random.uniform(-65.0, -35.0), 1),
        "identity": {"epc_hex": unit.epc_hex},
        "tag_data": {"lot": unit.lot_code},
    }
    r = client.post(f"{API_URL}/tag-reads", headers=_headers(tenant_id), json=body)
    return r.status_code


def _run_pipeline(
    client: httpx.Client,
    tenant_id: str,
    devices: list[dict[str, Any]],
    units: list[StockUnit],
    duration: float,
    tick: float,
) -> tuple[int, int]:
    """Drive the simulation. Returns (sent, failed)."""
    sent = 0
    failed = 0
    by_stage_count: dict[int, int] = {i: 0 for i in range(len(ZONE_PIPELINE))}
    start = time.monotonic()

    while True:
        now = time.monotonic() - start
        progressed = False
        for unit in units:
            if unit.next_step >= len(unit.schedule):
                continue
            offset, stage = unit.schedule[unit.next_step]
            if now < offset:
                continue
            device = devices[stage]
            code = _send_read(client, tenant_id, device["id"], unit)
            if code == 201:
                sent += 1
                by_stage_count[stage] += 1
            else:
                failed += 1
            unit.next_step += 1
            progressed = True

        # Status line.
        parts = [f"{ZONE_PIPELINE[i][0]}={by_stage_count[i]}" for i in range(len(ZONE_PIPELINE))]
        print(f"  t={now:5.1f}s  sent={sent} failed={failed}  " + "  ".join(parts), end="\r")

        if now >= duration:
            break
        # Sleep less when something just moved so we don't lag the schedule.
        time.sleep(tick if progressed else tick * 2)

    print()  # newline after status line
    return sent, failed


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def _check_uuid(value: str, label: str) -> None:
    try:
        UUID(value)
    except ValueError:
        print(f"Invalid {label}: {value!r} is not a UUID")
        sys.exit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="TagPulse inventory simulator (warehouse)")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument(
        "--units",
        type=int,
        default=None,
        help="Override total stock units (default: sum of CATALOG.units = "
        f"{sum(c.units for c in CATALOG)}).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=240.0,
        help="Total simulation seconds (default 240). Movements are spread over this window.",
    )
    parser.add_argument(
        "--tick",
        type=float,
        default=0.5,
        help="Scheduler sleep interval in seconds (default 0.5).",
    )
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TAGPULSE_API_KEY"),
        help="Admin/editor API key (Bearer). Falls back to $TAGPULSE_API_KEY.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible runs.",
    )
    args = parser.parse_args()

    _check_uuid(args.tenant_id, "tenant-id")

    if args.seed is not None:
        random.seed(args.seed)

    global _API_KEY
    _API_KEY = args.api_key
    if not _API_KEY:
        print(
            "WARNING: no --api-key (or $TAGPULSE_API_KEY) provided — "
            "site/zone/product/lot writes will fail with 403. "
            "See docs/quickstart.md → Step 5b for how to bootstrap one."
        )

    client = httpx.Client(timeout=10.0)
    try:
        h = client.get(f"{API_URL}/health")
        if h.status_code != 200:
            print(f"API unhealthy: {h.status_code}")
            sys.exit(1)
    except httpx.ConnectError:
        print(f"Cannot reach API at {API_URL}")
        sys.exit(1)

    print("\n=== TagPulse Inventory Simulator (warehouse scenario) ===")
    print(f"Tenant: {args.tenant_id}\n")

    print("Step 1: site + zone-anchor devices")
    site_id, devices = _seed_site_and_devices(client, args.tenant_id)

    print("\nStep 2: reader-bound zones")
    _seed_zones(client, args.tenant_id, site_id, devices)

    print("\nStep 3: products, lots, tag-data mapping")
    catalog = _seed_catalog(client, args.tenant_id)
    if not catalog:
        print("No catalog items — aborting.")
        sys.exit(1)

    if args.seed_only:
        print("\n--seed-only: skipping read stream.")
        return

    # Optional override of total units (proportional across catalog).
    if args.units is not None and args.units > 0:
        default_total = sum(c.units for c in catalog)
        scale = args.units / default_total
        for item in catalog:
            item.units = max(1, round(item.units * scale))

    units = _build_units(catalog, duration=args.duration)
    print(
        f"\nStep 4: streaming reads for {len(units)} stock units across "
        f"{len(devices)} readers over {args.duration:.0f}s "
        f"(SGTIN-96, stable serials)."
    )
    print("        Per-unit flow: Receiving → Cold Storage → (Pick Floor → Shipping)\n")

    try:
        sent, failed = _run_pipeline(
            client, args.tenant_id, devices, units, args.duration, args.tick
        )
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return

    print(
        f"\nDone: {sent} reads sent, {failed} failed across {len(units)} units. "
        "Check the UI:\n"
        "  • Inventory → Stock Levels  (per-zone counts)\n"
        "  • Inventory → Stock Movements (zone transitions)\n"
        "  • Inventory → Lot Expiry Queue (Milk lot should appear)\n"
        "  • Sites & Zones → Boston DC (each zone shows current occupants)"
    )


if __name__ == "__main__":
    main()
