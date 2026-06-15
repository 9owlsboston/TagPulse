#!/usr/bin/env python3
"""Register the inventory simulator's SGTIN EPCs in the tags registry.

The demo dashboard's **Tags** KPI counts rows in the ``tags`` registry
(``status IN ('registered', 'active')``). The asset simulator binds synthetic
``urn:epc:sim:<name>`` URIs (not hex EPCs, so they can't be registry rows) and
the inventory simulator streams real SGTIN-96 hex reads but never *registers*
those EPCs — so a freshly-seeded demo shows only the 3 transfer-seeded tags and
the KPI reads "3". This step closes that gap durably: it registers the exact
inventory units the seeder created, so every ``seed_demo_tenant`` run leaves the
Tags KPI reflecting the real inventory fleet.

The EPCs are derived **deterministically** from the same scenario catalog and
serial scheme :mod:`simulate_inventory` uses (``(product_idx+1) * 100_000 +
unit_idx``), so the registered set is exactly the streamed set — no async-worker
or read-timing dependency, no over-registration. Idempotent: a re-run sees 409s
for already-registered EPCs and converges.

Auth: the admin/editor API key (Bearer); ``POST /tags`` requires write scope.

Usage:
    python scripts/seed_register_tags.py \\
        --tenant-id <UUID> \\
        --api-key <KEY> \\
        --scenario baseline

Local/dev demo tooling. Not part of the production ingest path.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import simulate_inventory as si  # noqa: E402

API_URL = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")

# Match the 300/min write rate limit (Sprint 38) with margin.
_WRITE_THROTTLE_SECONDS = 0.25


def _headers(tenant_id: str, api_key: str) -> dict[str, str]:
    return {"X-Tenant-ID": tenant_id, "Authorization": f"Bearer {api_key}"}


def scenario_epc_hexes(scenario_name: str) -> list[str]:
    """Return the uppercase hex EPCs for every unit in ``scenario``'s catalog.

    Mirrors :func:`simulate_inventory._build_units`' serial scheme exactly so
    the registered set is identical to the streamed set.
    """
    scenario = si.SCENARIOS[scenario_name]
    epcs: list[str] = []
    for product_idx, item in enumerate(scenario.catalog):
        for unit_idx in range(item.units):
            serial = (product_idx + 1) * 100_000 + unit_idx
            epcs.append(si._sgtin96_hex(si.COMPANY_PREFIX, item.item_ref, serial).upper())
    return epcs


def register_tags(tenant_id: str, api_key: str, scenario_name: str) -> tuple[int, int, int]:
    """Register every inventory EPC for ``scenario`` in the tags registry.

    Returns ``(created, existing, failed)``. Fails fast (SystemExit) only on a
    transport error; per-EPC non-2xx (other than 409) is counted and surfaced.
    """
    epcs = scenario_epc_hexes(scenario_name)
    headers = _headers(tenant_id, api_key)
    created = existing = failed = 0
    with httpx.Client(timeout=15.0) as client:
        for epc_hex in epcs:
            try:
                resp = client.post(
                    f"{API_URL}/tags",
                    headers=headers,
                    json={"epc_hex": epc_hex, "source": "backfill"},
                )
            except httpx.HTTPError as exc:
                print(f"  FATAL: POST /tags failed: {exc}", file=sys.stderr)
                sys.exit(1)
            if resp.status_code == 201:
                created += 1
            elif resp.status_code == 409:
                existing += 1
            else:
                failed += 1
                if failed <= 3:
                    print(f"  FAIL {epc_hex}: {resp.status_code} {resp.text}", file=sys.stderr)
            time.sleep(_WRITE_THROTTLE_SECONDS)
    return created, existing, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="Target tenant UUID")
    parser.add_argument("--api-key", required=True, help="Admin/editor API key for the tenant")
    parser.add_argument(
        "--scenario",
        default=si.DEFAULT_SCENARIO,
        choices=sorted(si.SCENARIOS),
        help=(
            "Inventory scenario whose catalog EPCs to register "
            f"(default: {si.DEFAULT_SCENARIO}). Must match the scenario the "
            "inventory step seeded."
        ),
    )
    args = parser.parse_args()

    created, existing, failed = register_tags(args.tenant_id, args.api_key, args.scenario)
    total = created + existing
    print(
        f"  registered inventory tags for tenant {args.tenant_id} "
        f"(scenario {args.scenario!r}): {total} tags "
        f"(new={created}, existing={existing}, failed={failed})"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
