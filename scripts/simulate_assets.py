#!/usr/bin/env python3
"""Asset simulator — registers assets, binds tags, and drives zone transitions.

Sprint 15 — exercises the asset/zone enrichment pipeline end-to-end so the UI
Assets list, asset path timeline, and zone-occupancy panel get realistic data
without needing a real RFID fleet.

Usage:
    python scripts/simulate_assets.py --tenant-id <UUID> --assets 10 --readers 4

Prereqs:
    * The tenant must already have at least ``--readers`` registered devices —
      run ``simulate_devices.py`` first to create them. We pick the first
      ``--readers`` of them as our zone anchors.
    * Optional: pre-create reader-bound zones to see ``subject.zone_changed``
      events fire (otherwise the script just produces tag reads + bindings).

What it does:
    1. Reuses or creates ``--assets`` named pallets/cases.
    2. Binds each one to a unique synthetic EPC.
    3. Loops: picks a random asset, picks a random reader, sends a tag read
       with that EPC. Over time assets "move" between readers, so the
       enrichment pipeline emits ``subject.zone_changed`` events whenever
       reader_id maps to a different zone.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

API_URL = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")

# Optional bearer API key (admin/editor) — populated from --api-key or
# TAGPULSE_API_KEY env. Required for asset/binding writes since Sprint 12.
_API_KEY: str | None = None


def _headers(tenant_id: str) -> dict[str, str]:
    h = {"X-Tenant-ID": tenant_id}
    if _API_KEY:
        h["Authorization"] = f"Bearer {_API_KEY}"
    return h


def _ok(resp: httpx.Response) -> bool:
    return 200 <= resp.status_code < 300


# --------------------------------------------------------------------------- #
# Scenarios (Sprint 59 Phase C)
#
# ``baseline`` keeps the Sprint 15 behaviour verbatim — the combined
# ``demo-wm-dc`` tenant still runs it: a flat pool of ``Sim-Pallet-NNN`` assets
# bound to synthetic EPCs, driven by a random asset→reader loop over whatever
# devices already exist. ``fleet`` is the Sprint 59 asset-domain tenant: a
# named returnable / high-value roster across three categories, a purpose-built
# site with a geofenced authorized area + a yard/exit zone, and a movement pass
# that produces zone transitions plus two narrative events — one asset that
# goes dark ("where is X?") and one read at the exit zone (geofence breach).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AssetScenario:
    """A named asset-seeding preset.

    ``baseline`` leaves the topology fields empty, which selects the legacy
    random-reader flow (no site/zone creation). ``fleet`` supplies a full
    roster + zone topology. In ``zones`` the FIRST entry is the geofenced
    "authorized area" and the LAST is the yard/exit zone used for the
    geofence-breach narrative.
    """

    name: str
    site_name: str = ""
    address: str = ""
    zones: tuple[tuple[str, str], ...] = ()  # (zone_name, device_name)
    categories: tuple[tuple[str, str], ...] = ()  # (category_name, category_type)
    roster: tuple[tuple[str, str], ...] = ()  # (asset_name, category_name)
    missing_assets: frozenset[str] = frozenset()  # seeded but never read (gone dark)
    breach_assets: frozenset[str] = frozenset()  # read at the exit zone (breach)

    @property
    def is_topology(self) -> bool:
        """True when the scenario builds its own site + zones + named roster."""
        return bool(self.roster)


_FLEET_ZONES: tuple[tuple[str, str], ...] = (
    ("Authorized Area", "AF-Authorized"),  # [0] geofenced authorized area
    ("Receiving Yard", "AF-Receiving"),
    ("Wash Bay", "AF-WashBay"),
    ("Staging", "AF-Staging"),
    ("Loading Dock", "AF-Loading"),
    ("Maintenance Bay", "AF-Maintenance"),
    ("Yard / Exit", "AF-Exit"),  # [-1] exit zone (breach narrative)
)

_FLEET_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("Forklift", "object"),
    ("Reusable Tote", "rti_container"),
    ("IBC Container", "liquid_container"),
)


def _fleet_roster() -> tuple[tuple[str, str], ...]:
    roster: list[tuple[str, str]] = []
    roster += [(f"FORK-{i:02d}", "Forklift") for i in range(1, 7)]  # 6 forklifts
    roster += [(f"TOTE-{i:02d}", "Reusable Tote") for i in range(1, 11)]  # 10 totes
    roster += [(f"IBC-{i:02d}", "IBC Container") for i in range(1, 9)]  # 8 IBCs
    return tuple(roster)


SCENARIOS: dict[str, AssetScenario] = {
    "baseline": AssetScenario(name="baseline"),
    "fleet": AssetScenario(
        name="fleet",
        site_name="Returnable Asset Fleet Yard",
        address="9 Logistics Park, Boston, MA",
        zones=_FLEET_ZONES,
        categories=_FLEET_CATEGORIES,
        roster=_fleet_roster(),
        missing_assets=frozenset({"IBC-08"}),  # goes dark for the "where is X?" demo
        breach_assets=frozenset({"FORK-06"}),  # read at the exit zone
    ),
}

DEFAULT_SCENARIO = "baseline"


def fetch_devices(client: httpx.Client, tenant_id: str, count: int) -> list[dict[str, Any]]:
    resp = client.get(
        f"{API_URL}/device-registry",
        headers=_headers(tenant_id),
        params={"limit": 1000},
    )
    resp.raise_for_status()
    devices = resp.json()
    if len(devices) < count:
        raise SystemExit(
            f"Need {count} devices but tenant only has {len(devices)}. "
            "Run simulate_devices.py first."
        )
    return devices[:count]


def ensure_pallet_category(client: httpx.Client, tenant_id: str) -> str:
    """Find or create a ``Sim-Pallet`` category and return its UUID.

    Sprint 41 Phase H made ``category_id`` a required field on
    ``AssetCreate`` (ADR 019 close-out). The simulator now provisions a
    dedicated category up-front instead of relying on the legacy
    ``asset_type`` shadow column.
    """
    headers = _headers(tenant_id)
    # /categories caps at limit=500 (see api/routes/categories.py); we only ever
    # create the one ``Sim-Pallet`` category so 500 is plenty.
    resp = client.get(f"{API_URL}/categories", headers=headers, params={"limit": 500})
    resp.raise_for_status()
    for cat in resp.json():
        if cat["name"] == "Sim-Pallet":
            return str(cat["id"])
    create = client.post(
        f"{API_URL}/categories",
        headers=headers,
        json={
            "name": "Sim-Pallet",
            "category_type": "rti_container",
            "required_tags": 1,
        },
    )
    create.raise_for_status()
    return str(create.json()["id"])


def ensure_assets(client: httpx.Client, tenant_id: str, count: int) -> list[dict[str, Any]]:
    headers = _headers(tenant_id)
    category_id = ensure_pallet_category(client, tenant_id)
    resp = client.get(f"{API_URL}/assets", headers=headers, params={"limit": 1000})
    resp.raise_for_status()
    existing = {a["name"]: a for a in resp.json()}

    assets: list[dict[str, Any]] = []
    for i in range(count):
        name = f"Sim-Pallet-{i + 1:03d}"
        if name in existing:
            assets.append(existing[name])
            print(f"  Reusing asset: {name}")
            continue
        resp = client.post(
            f"{API_URL}/assets",
            headers=headers,
            json={
                "name": name,
                "category_id": category_id,
                "metadata": {"simulated": True},
            },
        )
        if not _ok(resp):
            print(f"  Failed to create {name}: {resp.status_code} {resp.text}")
            continue
        asset = resp.json()
        assets.append(asset)
        print(f"  Created asset: {name} ({asset['id']})")
    return assets


def ensure_bindings(
    client: httpx.Client, tenant_id: str, assets: list[dict[str, Any]]
) -> dict[str, str]:
    """Return mapping ``asset_id -> binding_value`` (EPC). Idempotent."""
    headers = _headers(tenant_id)
    out: dict[str, str] = {}
    for asset in assets:
        asset_id = asset["id"]
        # Check existing bindings first.
        resp = client.get(f"{API_URL}/assets/{asset_id}/bindings", headers=headers)
        if _ok(resp):
            active = [b for b in resp.json() if b.get("unbound_at") is None]
            if active:
                out[asset_id] = active[0]["binding_value"]
                continue
        epc = f"urn:epc:sim:{asset['name'].lower()}"
        resp = client.post(
            f"{API_URL}/assets/{asset_id}/bindings",
            headers=headers,
            json={"binding_value": epc, "binding_kind": "epc"},
        )
        if _ok(resp):
            out[asset_id] = epc
            print(f"  Bound {asset['name']} → {epc}")
        else:
            print(f"  Failed to bind {asset['name']}: {resp.status_code} {resp.text}")
    return out


def _seed_fleet_site_zones(
    client: httpx.Client, tenant_id: str, scenario: AssetScenario
) -> dict[str, dict[str, Any]]:
    """Ensure the fleet site + one zone-anchor device + reader-bound zone per
    entry in ``scenario.zones``. Return zone_name → anchor-device dict.

    Idempotent: reuses any existing site / device / zone by name.
    """
    headers = _headers(tenant_id)

    # Site.
    sites_r = client.get(f"{API_URL}/sites", headers=headers)
    sites = sites_r.json() if _ok(sites_r) else []
    site = next((s for s in sites if s["name"] == scenario.site_name), None)
    if site is None:
        r = client.post(
            f"{API_URL}/sites",
            headers=headers,
            json={"name": scenario.site_name, "address": scenario.address},
        )
        r.raise_for_status()
        site = r.json()
        print(f"  Created site: {scenario.site_name} ({site['id']})")
    else:
        print(f"  Reusing site: {scenario.site_name} ({site['id']})")
    site_id = site["id"]

    # One anchor device per zone.
    devices_r = client.get(f"{API_URL}/device-registry", headers=headers, params={"limit": 1000})
    existing_devices = {d["name"]: d for d in (devices_r.json() if _ok(devices_r) else [])}
    device_by_zone: dict[str, dict[str, Any]] = {}
    for zone_name, device_name in scenario.zones:
        device = existing_devices.get(device_name)
        if device is None:
            r = client.post(
                f"{API_URL}/device-registry",
                headers=headers,
                json={
                    "name": device_name,
                    "device_type": "rfid_reader",
                    "metadata": {"simulated": True, "profile": "asset-fleet"},
                },
            )
            if not _ok(r):
                print(f"  FAIL device {device_name}: {r.status_code} {r.text}")
                sys.exit(1)
            device = r.json()
            print(f"  Created device: {device_name}")
        else:
            print(f"  Reusing device: {device_name}")
        device_by_zone[zone_name] = device

    # Reader-bound zones.
    zones_r = client.get(f"{API_URL}/zones", headers=headers, params={"site_id": site_id})
    existing_zones = {z["name"]: z for z in (zones_r.json() if _ok(zones_r) else [])}
    for zone_name, _device_name in scenario.zones:
        if zone_name in existing_zones:
            print(f"  Reusing zone: {zone_name}")
            continue
        device = device_by_zone[zone_name]
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
        print(f"  Created zone: {zone_name}")
    return device_by_zone


def _ensure_categories(
    client: httpx.Client, tenant_id: str, scenario: AssetScenario
) -> dict[str, str]:
    """Find or create each fleet category. Return category_name → UUID."""
    headers = _headers(tenant_id)
    resp = client.get(f"{API_URL}/categories", headers=headers, params={"limit": 500})
    resp.raise_for_status()
    existing = {c["name"]: c for c in resp.json()}
    out: dict[str, str] = {}
    for cat_name, cat_type in scenario.categories:
        cat = existing.get(cat_name)
        if cat is None:
            r = client.post(
                f"{API_URL}/categories",
                headers=headers,
                json={"name": cat_name, "category_type": cat_type, "required_tags": 1},
            )
            if not _ok(r):
                print(f"  FAIL category {cat_name}: {r.status_code} {r.text}")
                sys.exit(1)
            cat = r.json()
            print(f"  Created category: {cat_name} ({cat_type})")
        else:
            print(f"  Reusing category: {cat_name}")
        out[cat_name] = str(cat["id"])
    return out


def _ensure_roster_assets(
    client: httpx.Client,
    tenant_id: str,
    scenario: AssetScenario,
    category_ids: dict[str, str],
) -> list[dict[str, Any]]:
    """Ensure each named roster asset exists in its category. Idempotent."""
    headers = _headers(tenant_id)
    resp = client.get(f"{API_URL}/assets", headers=headers, params={"limit": 1000})
    resp.raise_for_status()
    existing = {a["name"]: a for a in resp.json()}

    assets: list[dict[str, Any]] = []
    for asset_name, cat_name in scenario.roster:
        if asset_name in existing:
            assets.append(existing[asset_name])
            print(f"  Reusing asset: {asset_name}")
            continue
        r = client.post(
            f"{API_URL}/assets",
            headers=headers,
            json={
                "name": asset_name,
                "category_id": category_ids[cat_name],
                "metadata": {"simulated": True, "profile": "asset-fleet", "category": cat_name},
            },
        )
        if not _ok(r):
            print(f"  Failed to create {asset_name}: {r.status_code} {r.text}")
            continue
        asset = r.json()
        assets.append(asset)
        print(f"  Created asset: {asset_name} ({asset['id']})")
    return assets


def emit_fleet_movement(
    client: httpx.Client,
    tenant_id: str,
    scenario: AssetScenario,
    device_by_zone: dict[str, dict[str, Any]],
    assets: list[dict[str, Any]],
    bindings: dict[str, str],
    *,
    interval: float,
) -> None:
    """Drive a bounded movement pass so every fleet page shows live data.

    Each non-missing asset is read at a short path of distinct zones (so the
    enrichment pipeline emits ``subject.zone_changed`` after the seed read).
    ``missing_assets`` are deliberately never read (gone dark); ``breach_assets``
    always finish at the yard/exit zone (the geofence-breach narrative).
    """
    flow_zone_names = [z for z, _ in scenario.zones[:-1]]  # exclude exit zone
    exit_zone_name = scenario.zones[-1][0]
    sent = 0
    skipped_missing = 0
    for asset in assets:
        name = asset["name"]
        epc = bindings.get(asset["id"])
        if epc is None:
            continue
        if name in scenario.missing_assets:
            skipped_missing += 1
            continue
        path = random.sample(flow_zone_names, k=min(3, len(flow_zone_names)))
        if name in scenario.breach_assets:
            path = [path[0], exit_zone_name]
        for zone_name in path:
            device = device_by_zone[zone_name]
            body = {
                "device_id": device["id"],
                "tag_id": epc,
                "timestamp": datetime.now(UTC).isoformat(),
                "signal_strength": round(random.uniform(-75, -35), 1),
                "identity": {"epc": epc},
            }
            resp = client.post(f"{API_URL}/tag-reads", headers=_headers(tenant_id), json=body)
            if not _ok(resp):
                print(f"  Ingest failed for {name}@{zone_name}: {resp.status_code} {resp.text}")
            else:
                sent += 1
            time.sleep(interval)
    print(
        f"  Fleet movement: {sent} reads across {len(scenario.zones)} zones; "
        f"{skipped_missing} asset(s) left dark (missing); "
        f"{len(scenario.breach_assets)} routed to '{exit_zone_name}' (breach)."
    )


def emit_tag_reads(
    client: httpx.Client,
    tenant_id: str,
    devices: list[dict[str, Any]],
    bindings: dict[str, str],
    *,
    interval: float,
    iterations: int | None,
) -> None:
    headers = _headers(tenant_id)
    asset_ids = list(bindings.keys())
    if not asset_ids:
        print("No bindings to drive — exiting.")
        return
    sent = 0
    while iterations is None or sent < iterations:
        asset_id = random.choice(asset_ids)
        device = random.choice(devices)
        epc = bindings[asset_id]
        body = {
            "device_id": device["id"],
            "tag_id": epc,
            "timestamp": datetime.now(UTC).isoformat(),
            "signal_strength": round(random.uniform(-75, -35), 1),
            "identity": {"epc": epc},
        }
        resp = client.post(f"{API_URL}/tag-reads", headers=headers, json=body)
        if not _ok(resp):
            print(f"  Ingest failed: {resp.status_code} {resp.text}")
        sent += 1
        if sent % 10 == 0:
            print(f"  Sent {sent} reads (last: asset={asset_id[:8]} device={device['name']})")
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS),
        default=DEFAULT_SCENARIO,
        help=(
            "Roster/topology preset (default: baseline). 'baseline' is the "
            "Sprint 15 Sim-Pallet pool over existing devices (used by the "
            "combined demo tenant); 'fleet' is the Sprint 59 asset-domain "
            "roster (named returnables across 3 categories + a purpose-built "
            "site with a geofenced area and a yard/exit zone). Under 'fleet', "
            "--assets / --readers / --iterations are ignored (the roster and "
            "zone topology are fixed)."
        ),
    )
    parser.add_argument("--assets", type=int, default=10)
    parser.add_argument("--readers", type=int, default=4)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Stop after N reads (default: run forever).",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TAGPULSE_API_KEY"),
        help="Admin/editor API key (Bearer). Falls back to $TAGPULSE_API_KEY.",
    )
    args = parser.parse_args(argv)
    scenario = SCENARIOS[args.scenario]

    global _API_KEY
    _API_KEY = args.api_key
    if not _API_KEY:
        print(
            "WARNING: no --api-key (or $TAGPULSE_API_KEY) provided — "
            "asset/binding writes will fail with 403. "
            "See docs/quickstart.md → Step 5b for how to bootstrap one."
        )

    if scenario.is_topology:
        return _run_fleet(scenario, args)

    with httpx.Client(timeout=10.0) as client:
        print(f"Loading {args.readers} devices for tenant {args.tenant_id}…")
        devices = fetch_devices(client, args.tenant_id, args.readers)
        print(f"Ensuring {args.assets} assets…")
        assets = ensure_assets(client, args.tenant_id, args.assets)
        print("Binding tag IDs…")
        bindings = ensure_bindings(client, args.tenant_id, assets)
        print(f"Emitting tag reads every {args.interval}s…")
        try:
            emit_tag_reads(
                client,
                args.tenant_id,
                devices,
                bindings,
                interval=args.interval,
                iterations=args.iterations,
            )
        except KeyboardInterrupt:
            print("\nStopping.")
    return 0


def _run_fleet(scenario: AssetScenario, args: argparse.Namespace) -> int:
    """Seed + drive the named-roster fleet scenario (its own site + zones)."""
    tenant_id = args.tenant_id
    with httpx.Client(timeout=10.0) as client:
        print(f"\n=== TagPulse Asset Simulator (fleet scenario: {scenario.site_name}) ===")
        print(f"Tenant: {tenant_id}\n")
        print("Step 1: site + zone-anchor devices + reader-bound zones")
        device_by_zone = _seed_fleet_site_zones(client, tenant_id, scenario)
        print("\nStep 2: asset categories")
        category_ids = _ensure_categories(client, tenant_id, scenario)
        print(f"\nStep 3: roster ({len(scenario.roster)} named assets)")
        assets = _ensure_roster_assets(client, tenant_id, scenario, category_ids)
        print("\nStep 4: bind tag IDs")
        bindings = ensure_bindings(client, tenant_id, assets)
        print("\nStep 5: movement pass (zone transitions + breach + missing)")
        try:
            emit_fleet_movement(
                client,
                tenant_id,
                scenario,
                device_by_zone,
                assets,
                bindings,
                interval=args.interval if args.interval < 1.0 else 0.1,
            )
        except KeyboardInterrupt:
            print("\nStopping.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
