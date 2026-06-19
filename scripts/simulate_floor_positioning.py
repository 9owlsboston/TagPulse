#!/usr/bin/env python3
"""Floor-positioning simulator + ground-truth validator (Sprint 66, Phase 2).

The existing ``simulate_assets.py`` is *not* positioning-shaped: it sends
single-reader reads with **random** RSSI, so it can never exercise
triangulation. This script places virtual assets at **known** floor ``(x, y)``
and emits reads to the **nearest K readers** with **RSSI derived from distance**
— the multi-antenna, distance-correlated signal the
``rssi_weighted_centroid`` estimator needs.

Two modes:

- ``--validate`` (default-safe, **no API, no DB**): build the observation set the
  same way it would arrive, run the **real estimator**, and print *estimated vs
  placed* error per asset + a summary RMSE. This is the ADR-024 "surveyed ground
  truth" check — it proves the estimator recovers known positions.
- emit (``--emit``): back-fill the floor survey (site ``coord_system`` + reader
  ``antennas`` at known ``(x, y)`` + ``device.site_id``), bind EPCs to assets,
  and stream distance-based multi-reader reads to a live API. After this runs,
  flip ``position_estimator_enabled`` to watch the worker write
  ``asset_positions(source='computed')`` and the UI draw trails.

Run ``--validate`` anywhere (it only needs ``src`` importable); emit needs a
reachable API + ``TAGPULSE_API_KEY``.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from tagpulse.services.positioning import (  # noqa: E402
    AntennaObservation,
    PositionFix,
    PositionStrategy,
    rssi_weighted_centroid,
)

# ---------------------------------------------------------------------------
# Pure model (unit-tested — no I/O)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reader:
    """A fixed reader at a known floor position (its port-0 antenna spot)."""

    id: UUID
    name: str
    x: float
    y: float


@dataclass(frozen=True)
class PlacedAsset:
    """A virtual asset at a known ground-truth floor position."""

    name: str
    epc: str
    x: float
    y: float


def euclidean(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def rssi_from_distance(
    dist: float,
    *,
    rssi0: float = -40.0,
    path_loss: float = 2.0,
    ref_m: float = 1.0,
    floor_dbm: float = -90.0,
) -> float:
    """Log-distance path-loss RSSI: ``rssi0`` at ``ref_m``, decreasing with range.

    Monotonic non-increasing in ``dist``; clamped to ``[floor_dbm, 0]``.
    """
    d = max(dist, ref_m)
    rssi = rssi0 - 10.0 * path_loss * math.log10(d / ref_m)
    return max(floor_dbm, min(0.0, rssi))


def nearest_k(ax: float, ay: float, readers: list[Reader], k: int) -> list[tuple[Reader, float]]:
    """The ``k`` readers nearest ``(ax, ay)``, as ``(reader, distance)`` ascending."""
    ranked = sorted(readers, key=lambda r: euclidean(ax, ay, r.x, r.y))
    return [(r, euclidean(ax, ay, r.x, r.y)) for r in ranked[: max(0, k)]]


def grid_readers(extent_x: float, extent_y: float, n: int) -> list[Reader]:
    """Place ``n`` readers on an inset grid covering the floor."""
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    inset_x, inset_y = extent_x * 0.1, extent_y * 0.1
    span_x = extent_x - 2 * inset_x
    span_y = extent_y - 2 * inset_y
    readers: list[Reader] = []
    for i in range(n):
        r, c = divmod(i, cols)
        x = inset_x + (span_x * (c / max(1, cols - 1)) if cols > 1 else span_x / 2)
        y = inset_y + (span_y * (r / max(1, rows - 1)) if rows > 1 else span_y / 2)
        readers.append(Reader(id=uuid4(), name=f"reader-{i + 1:02d}", x=round(x, 2), y=round(y, 2)))
    return readers


def place_assets(n: int, extent_x: float, extent_y: float, rng: random.Random) -> list[PlacedAsset]:
    """Scatter ``n`` assets at random known positions inside the floor."""
    margin_x, margin_y = extent_x * 0.05, extent_y * 0.05
    out: list[PlacedAsset] = []
    for i in range(n):
        out.append(
            PlacedAsset(
                name=f"asset-{i + 1:02d}",
                epc=f"urn:epc:sim:floor:{i + 1:04d}",
                x=round(rng.uniform(margin_x, extent_x - margin_x), 2),
                y=round(rng.uniform(margin_y, extent_y - margin_y), 2),
            )
        )
    return out


def observations_for_asset(
    asset: PlacedAsset,
    readers: list[Reader],
    k: int,
    *,
    now: datetime,
    rssi0: float = -40.0,
    path_loss: float = 2.0,
    noise_db: float = 0.0,
    rng: random.Random | None = None,
) -> list[AntennaObservation]:
    """Build the estimator's observations for one asset from its nearest readers."""
    obs: list[AntennaObservation] = []
    for reader, dist in nearest_k(asset.x, asset.y, readers, k):
        rssi = rssi_from_distance(dist, rssi0=rssi0, path_loss=path_loss)
        if noise_db and rng is not None:
            rssi = max(-127.0, min(0.0, rssi + rng.uniform(-noise_db, noise_db)))
        obs.append(
            AntennaObservation(
                antenna_id=reader.id, x=reader.x, y=reader.y, rssi=rssi, cnt=1, ts=now
            )
        )
    return obs


def estimate_for_asset(
    asset: PlacedAsset,
    readers: list[Reader],
    k: int,
    config: PositionStrategy,
    *,
    now: datetime,
    rssi0: float = -40.0,
    path_loss: float = 2.0,
    noise_db: float = 0.0,
    rng: random.Random | None = None,
) -> tuple[PositionFix | None, float | None]:
    """Estimate an asset's position and return ``(fix, error_metres)``."""
    obs = observations_for_asset(
        asset, readers, k, now=now, rssi0=rssi0, path_loss=path_loss, noise_db=noise_db, rng=rng
    )
    fix = rssi_weighted_centroid(obs, now=now, config=config)
    if fix is None:
        return None, None
    return fix, euclidean(fix.x, fix.y, asset.x, asset.y)


# ---------------------------------------------------------------------------
# Validate mode (no I/O)
# ---------------------------------------------------------------------------


def run_validate(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    readers = grid_readers(args.extent_x, args.extent_y, args.readers)
    assets = place_assets(args.assets, args.extent_x, args.extent_y, rng)
    now = datetime.now(UTC)
    config = PositionStrategy(
        half_life_s=args.half_life_s,
        rssi_floor_dbm=args.rssi_floor_dbm,
        min_antennas=args.min_antennas,
    )

    print(
        f"Floor {args.extent_x:.0f}x{args.extent_y:.0f} · {args.readers} readers · "
        f"{args.assets} assets · nearest-{args.k} · noise=±{args.noise_db}dB · "
        f"tau={args.half_life_s}s rssi_floor={args.rssi_floor_dbm}"
    )
    print(f"{'asset':<12} {'placed (x,y)':<18} {'estimated (x,y)':<18} {'err_m':>8} {'conf':>5}")
    errors: list[float] = []
    for asset in assets:
        fix, err = estimate_for_asset(
            asset,
            readers,
            args.k,
            config,
            now=now,
            noise_db=args.noise_db,
            rng=rng,
        )
        placed = f"({asset.x:.1f}, {asset.y:.1f})"
        if fix is None:
            print(f"{asset.name:<12} {placed:<18} {'(no fix)':<18} {'-':>8} {'-':>5}")
            continue
        est = f"({fix.x:.1f}, {fix.y:.1f})"
        assert err is not None
        errors.append(err)
        print(f"{asset.name:<12} {placed:<18} {est:<18} {err:>8.2f} {fix.confidence:>5.2f}")

    if errors:
        rmse = math.sqrt(sum(e * e for e in errors) / len(errors))
        mean = sum(errors) / len(errors)
        print(
            f"\nfixes={len(errors)}/{len(assets)}  "
            f"mean_err={mean:.2f}m  RMSE={rmse:.2f}m  max={max(errors):.2f}m"
        )
    else:
        print("\nNo fixes produced — check --rssi-floor-dbm / --min-antennas / --k.")
    return 0


# ---------------------------------------------------------------------------
# Emit mode (live API)
# ---------------------------------------------------------------------------


def run_emit(args: argparse.Namespace) -> int:
    import httpx  # local import — only needed for live emission

    api = args.api_url.rstrip("/")
    key = os.environ.get("TAGPULSE_API_KEY")
    tenant = args.tenant_id
    if not key or not tenant:
        sys.exit("emit mode needs --tenant-id and $TAGPULSE_API_KEY")
    headers = {"X-Tenant-ID": tenant, "Authorization": f"Bearer {key}"}

    rng = random.Random(args.seed)
    readers = grid_readers(args.extent_x, args.extent_y, args.readers)
    assets = place_assets(args.assets, args.extent_x, args.extent_y, rng)

    with httpx.Client(timeout=10.0) as client:
        site_id = _ensure_floor_site(client, api, headers, args)
        device_ids = _ensure_readers(client, api, headers, site_id, readers)
        _ensure_assets(client, api, headers, assets)
        print(f"Survey ready: site={site_id} readers={len(readers)} assets={len(assets)}")

        sent = 0
        ticks = 0
        while args.duration <= 0 or ticks * args.interval < args.duration:
            now_iso = datetime.now(UTC).isoformat()
            for asset in assets:
                for reader, dist in nearest_k(asset.x, asset.y, readers, args.k):
                    rssi = rssi_from_distance(dist)
                    if args.noise_db:
                        rssi = max(
                            -127.0, min(0.0, rssi + rng.uniform(-args.noise_db, args.noise_db))
                        )
                    body = {
                        "device_id": device_ids[reader.id],
                        "tag_id": asset.epc,
                        "timestamp": now_iso,
                        "signal_strength": round(rssi, 1),
                        "reader_antenna": 0,
                        "identity": {"epc": asset.epc},
                    }
                    r = client.post(f"{api}/tag-reads", headers=headers, json=body)
                    if r.status_code // 100 == 2:
                        sent += 1
                    elif sent < 3:
                        print(f"  read failed: {r.status_code} {r.text}")
            ticks += 1
            time.sleep(args.interval)
        print(f"Emitted {sent} reads over {ticks} ticks.")
    return 0


def _ensure_floor_site(client, api, headers, args) -> str:  # type: ignore[no-untyped-def]
    name = args.site_name
    sites = client.get(f"{api}/sites", headers=headers).json()
    site = next((s for s in sites if s["name"] == name), None)
    coord_system = {
        "units": "meters",
        "extent_x": args.extent_x,
        "extent_y": args.extent_y,
        "origin_anchor": "nw_corner",
    }
    if site is None:
        r = client.post(
            f"{api}/sites", headers=headers, json={"name": name, "coord_system": coord_system}
        )
        r.raise_for_status()
        return str(r.json()["id"])
    # Ensure the existing site has a floor frame.
    if not site.get("coord_system"):
        client.patch(
            f"{api}/sites/{site['id']}", headers=headers, json={"coord_system": coord_system}
        )
    return str(site["id"])


def _ensure_readers(client, api, headers, site_id, readers):  # type: ignore[no-untyped-def]
    existing = {
        d["name"]: d
        for d in client.get(
            f"{api}/device-registry", headers=headers, params={"limit": 1000}
        ).json()
    }
    device_ids: dict[UUID, str] = {}
    for reader in readers:
        device = existing.get(reader.name)
        if device is None:
            r = client.post(
                f"{api}/device-registry",
                headers=headers,
                json={
                    "name": reader.name,
                    "device_type": "rfid_reader",
                    "mobility": "fixed",
                    "site_id": site_id,
                    "metadata": {"simulated": True, "profile": "floor-positioning"},
                },
            )
            r.raise_for_status()
            device = r.json()
        else:
            client.patch(
                f"{api}/device-registry/{device['id']}",
                headers=headers,
                json={"site_id": site_id, "mobility": "fixed"},
            )
        device_ids[reader.id] = device["id"]
        # Survey port-0 = the reader's nominal floor spot.
        client.put(
            f"{api}/device-registry/{device['id']}/antennas/0",
            headers=headers,
            json={"x": reader.x, "y": reader.y, "label": reader.name},
        )
    return device_ids


def _ensure_assets(client, api, headers, assets):  # type: ignore[no-untyped-def]
    existing = {
        a["name"]: a
        for a in client.get(f"{api}/assets", headers=headers, params={"limit": 1000}).json()
    }
    for asset in assets:
        a = existing.get(asset.name)
        if a is None:
            r = client.post(
                f"{api}/assets",
                headers=headers,
                json={
                    "name": asset.name,
                    "metadata": {"simulated": True, "placed_x": asset.x, "placed_y": asset.y},
                },
            )
            r.raise_for_status()
            a = r.json()
        bindings = client.get(f"{api}/assets/{a['id']}/bindings", headers=headers).json()
        active = [b for b in bindings if b.get("unbound_at") is None]
        if not any(b["binding_value"] == asset.epc for b in active):
            client.post(
                f"{api}/assets/{a['id']}/bindings",
                headers=headers,
                json={"binding_value": asset.epc, "binding_kind": "epc"},
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--emit", action="store_true", help="Stream reads to a live API.")
    mode.add_argument(
        "--validate",
        action="store_true",
        help="Offline ground-truth check (no API/DB). This is the default.",
    )
    p.add_argument("--api-url", default=os.environ.get("API_URL", "http://localhost:8000"))
    p.add_argument("--tenant-id", default=os.environ.get("DEMO_TENANT_ID"))
    p.add_argument("--site-name", default="Floor-Positioning Demo")
    p.add_argument("--readers", type=int, default=4)
    p.add_argument("--assets", type=int, default=8)
    p.add_argument("--extent-x", type=float, default=600.0)
    p.add_argument("--extent-y", type=float, default=400.0)
    p.add_argument("--k", type=int, default=3, help="Reads per asset per tick (nearest readers).")
    p.add_argument("--noise-db", type=float, default=2.0)
    p.add_argument(
        "--half-life-s", type=float, default=1.0e9, help="tau; large = no decay (same-ts obs)."
    )
    p.add_argument("--rssi-floor-dbm", type=float, default=-127.0)
    p.add_argument("--min-antennas", type=int, default=1)
    p.add_argument("--duration", type=float, default=60.0, help="emit seconds (<=0 = forever).")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return run_emit(args) if args.emit else run_validate(args)


if __name__ == "__main__":
    raise SystemExit(main())
