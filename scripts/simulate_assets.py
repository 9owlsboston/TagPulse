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

    global _API_KEY
    _API_KEY = args.api_key
    if not _API_KEY:
        print(
            "WARNING: no --api-key (or $TAGPULSE_API_KEY) provided — "
            "asset/binding writes will fail with 403. "
            "See docs/quickstart.md → Step 5b for how to bootstrap one."
        )

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


if __name__ == "__main__":
    sys.exit(main())
