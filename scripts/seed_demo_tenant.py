#!/usr/bin/env python3
"""Composer — bring up the demo tenant end-to-end.

Per Sprint 58 design doc (Phase B "concrete deliverables" #1), this script
runs the canonical demo-tenant build in one shot:

    1. Ensure tenant + api key                 (smoke_setup.py --full)
    2. Seed devices                            (simulate_devices.py --seed-only)
    3. Seed inventory                          (simulate_inventory.py --seed-only)
    4. Seed assets + bind tags                 (simulate_assets.py one pass)
    5. Backfill ~3 days of history             (backfill_history.py)
    6. Seed open + resolved alerts             (seed_alerts.py)
    7. Seed one in-flight transfer             (seed_transfer.py)

**Composition, not rewrite** — the four existing simulators stay untouched.
This composer just `subprocess.run`-s them in order with explicit CLI args
pinned per-call (R1 mitigation: no ``**kwargs`` pass-through; every flag
is named at the call site so a downstream CLI rename surfaces as a
predictable subprocess error, not silent skip).

Idempotency:
  * Demo tenant identity is deterministic — ``uuid5(NAMESPACE_DNS, ...)``
    on the demo slug. Re-runs converge to the same tenant row.
  * Devices, products, assets, recipient tenant are upsert-shaped in their
    respective scripts.
  * Backfill *appends* another window of reads — set ``--reads 0`` (or
    pass ``DEMO_SKIP_BACKFILL=1``) to skip on a subsequent run.
  * API key is rotated on each call by default (so the printed key is
    always usable). Set ``DEMO_KEEP_KEY=1`` to skip rotation; you must
    then have ``$TAGPULSE_API_KEY`` already exported.

Usage:
    python scripts/seed_demo_tenant.py
    python scripts/seed_demo_tenant.py --reads 2000 --days 1
    DEMO_KEEP_KEY=1 python scripts/seed_demo_tenant.py
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Deterministic demo tenant identity (Sprint 58 D2).
DEMO_TENANT_SLUG = "demo-wm-dc"
DEMO_TENANT_NAME = "WM Distribution Center"
DEMO_TENANT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, f"{DEMO_TENANT_SLUG}.tagpulse.local")
DEMO_ADMIN_EMAIL = "admin@demo-wm-dc.tagpulse.local"
DEMO_ADMIN_NAME = "Demo Admin"

# Parsed from smoke_setup stdout: "  export TAGPULSE_API_KEY=<key>"
_EXPORT_KEY_RE = re.compile(r"^\s*export\s+TAGPULSE_API_KEY=(\S+)\s*$", re.MULTILINE)


def _print_header(step: int, total: int, title: str) -> None:
    bar = "=" * 64
    print()
    print(bar)
    print(f"[{step}/{total}] {title}")
    print(bar)


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> str:
    """Run a subprocess, stream output to stdout, return captured stdout.

    Raises ``SystemExit`` on non-zero exit so the composer fails fast.
    """
    print("  $ " + " ".join(cmd))
    proc = subprocess.run(
        cmd,
        env=env or os.environ.copy(),
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    if proc.returncode != 0:
        print(
            f"  FATAL: {cmd[1] if len(cmd) > 1 else cmd[0]} exited "
            f"with code {proc.returncode}",
            file=sys.stderr,
        )
        sys.exit(proc.returncode)
    return proc.stdout


def _step_smoke_setup(*, keep_key: bool) -> str:
    """Run smoke_setup and return the admin API key.

    ``DEMO_KEEP_KEY=1`` skips key regeneration and reuses the existing
    ``$TAGPULSE_API_KEY`` (which must be set).
    """
    if keep_key:
        existing = os.environ.get("TAGPULSE_API_KEY")
        if not existing:
            print(
                "ERROR: DEMO_KEEP_KEY=1 set but $TAGPULSE_API_KEY is empty",
                file=sys.stderr,
            )
            sys.exit(2)
        # Still run smoke_setup (idempotent) so tenant/users/zones/rules
        # are guaranteed in place, but don't rotate the key.
        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "smoke_setup.py"),
            "--full",
            "--tenant-id", str(DEMO_TENANT_ID),
            "--tenant-slug", DEMO_TENANT_SLUG,
            "--tenant-name", DEMO_TENANT_NAME,
            "--admin-email", DEMO_ADMIN_EMAIL,
            "--admin-name", DEMO_ADMIN_NAME,
        ]
        _run(cmd)
        print(f"  reusing existing $TAGPULSE_API_KEY ({existing[:10]}…)")
        return existing

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "smoke_setup.py"),
        "--full",
        "--regenerate-key",
        "--print-full-key",
        "--tenant-id", str(DEMO_TENANT_ID),
        "--tenant-slug", DEMO_TENANT_SLUG,
        "--tenant-name", DEMO_TENANT_NAME,
        "--admin-email", DEMO_ADMIN_EMAIL,
        "--admin-name", DEMO_ADMIN_NAME,
    ]
    stdout = _run(cmd)
    match = _EXPORT_KEY_RE.search(stdout)
    if not match:
        print(
            "FATAL: could not parse 'export TAGPULSE_API_KEY=' from "
            "smoke_setup stdout — did Key Vault flags get set?",
            file=sys.stderr,
        )
        sys.exit(2)
    key = match.group(1)
    print(f"  parsed admin API key: {key[:10]}…")
    return key


def _step_simulate_devices(api_key: str, *, devices: int, tags: int) -> None:
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "simulate_devices.py"),
        "--tenant-id", str(DEMO_TENANT_ID),
        "--api-key", api_key,
        "--devices", str(devices),
        "--tags", str(tags),
        "--seed-only",
    ]
    _run(cmd)


def _step_simulate_inventory(api_key: str, *, units: int) -> None:
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "simulate_inventory.py"),
        "--tenant-id", str(DEMO_TENANT_ID),
        "--api-key", api_key,
        "--units", str(units),
        "--seed-only",
    ]
    _run(cmd)


def _step_simulate_assets(
    api_key: str, *, assets: int, readers: int, iterations: int
) -> None:
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "simulate_assets.py"),
        "--tenant-id", str(DEMO_TENANT_ID),
        "--api-key", api_key,
        "--assets", str(assets),
        "--readers", str(readers),
        "--iterations", str(iterations),
        "--interval", "0.1",
    ]
    _run(cmd)


def _step_backfill_history(
    api_key: str, *, days: float, reads: int, batch_size: int
) -> None:
    if reads <= 0:
        print("  skipped (reads=0)")
        return
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "backfill_history.py"),
        "--tenant-id", str(DEMO_TENANT_ID),
        "--api-key", api_key,
        "--days", str(days),
        "--reads", str(reads),
        "--batch-size", str(batch_size),
    ]
    _run(cmd)


def _step_seed_alerts(
    api_key: str, *, natural: int, resolved: int
) -> None:
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "seed_alerts.py"),
        "--tenant-id", str(DEMO_TENANT_ID),
        "--api-key", api_key,
        "--natural-count", str(natural),
        "--resolved-count", str(resolved),
    ]
    _run(cmd)


def _step_seed_transfer(api_key: str, *, epc_count: int) -> None:
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "seed_transfer.py"),
        "--tenant-id", str(DEMO_TENANT_ID),
        "--api-key", api_key,
        "--epc-count", str(epc_count),
    ]
    _run(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--devices",
        type=int,
        default=10,
        help="Number of reader devices to provision (default: 10)",
    )
    parser.add_argument(
        "--tags",
        type=int,
        default=50,
        help="Size of the tag pool the simulators share (default: 50)",
    )
    parser.add_argument(
        "--inventory-units",
        type=int,
        default=60,
        help="Inventory stock units to seed (default: 60)",
    )
    parser.add_argument(
        "--assets",
        type=int,
        default=12,
        help="Assets to seed and bind (default: 12)",
    )
    parser.add_argument(
        "--readers",
        type=int,
        default=4,
        help="Reader pool size for asset simulation (default: 4)",
    )
    parser.add_argument(
        "--asset-iterations",
        type=int,
        default=20,
        help="Iterations of the asset simulator one-pass (default: 20)",
    )
    parser.add_argument(
        "--days",
        type=float,
        default=3.0,
        help="Days of history to backfill (default: 3.0)",
    )
    parser.add_argument(
        "--reads",
        type=int,
        default=5000,
        help="Total reads to backfill across the window (default: 5000)",
    )
    parser.add_argument(
        "--backfill-batch-size",
        type=int,
        default=500,
        help="Reads per backfill POST batch (default: 500)",
    )
    parser.add_argument(
        "--natural-alerts",
        type=int,
        default=4,
        help="Number of naturally-triggered open alerts (default: 4)",
    )
    parser.add_argument(
        "--resolved-alerts",
        type=int,
        default=3,
        help="Number of resolved alerts seeded directly (default: 3)",
    )
    parser.add_argument(
        "--transfer-epc-count",
        type=int,
        default=3,
        help="EPCs in the seeded in-flight transfer (default: 3)",
    )
    args = parser.parse_args()

    keep_key = os.environ.get("DEMO_KEEP_KEY") == "1"
    skip_backfill = os.environ.get("DEMO_SKIP_BACKFILL") == "1"
    reads = 0 if skip_backfill else args.reads

    print(
        f"Demo tenant composer → slug={DEMO_TENANT_SLUG} id={DEMO_TENANT_ID}"
    )
    if keep_key:
        print("  DEMO_KEEP_KEY=1: reusing existing $TAGPULSE_API_KEY")
    if skip_backfill:
        print("  DEMO_SKIP_BACKFILL=1: skipping historical backfill")

    t0 = time.monotonic()
    total_steps = 7

    _print_header(1, total_steps, "smoke_setup — tenant + admin + rules + zones")
    api_key = _step_smoke_setup(keep_key=keep_key)

    _print_header(2, total_steps, "simulate_devices — seed reader devices")
    _step_simulate_devices(api_key, devices=args.devices, tags=args.tags)

    _print_header(3, total_steps, "simulate_inventory — seed products and lots")
    _step_simulate_inventory(api_key, units=args.inventory_units)

    _print_header(4, total_steps, "simulate_assets — seed assets and bind tags")
    _step_simulate_assets(
        api_key,
        assets=args.assets,
        readers=args.readers,
        iterations=args.asset_iterations,
    )

    _print_header(
        5, total_steps,
        f"backfill_history — replay {reads} reads across {args.days} day(s)",
    )
    _step_backfill_history(
        api_key,
        days=args.days,
        reads=reads,
        batch_size=args.backfill_batch_size,
    )

    _print_header(6, total_steps, "seed_alerts — open + resolved alert mix")
    _step_seed_alerts(
        api_key,
        natural=args.natural_alerts,
        resolved=args.resolved_alerts,
    )

    _print_header(7, total_steps, "seed_transfer — one in-flight cross-tenant transfer")
    _step_seed_transfer(api_key, epc_count=args.transfer_epc_count)

    elapsed = time.monotonic() - t0
    print()
    print("=" * 64)
    print(f"Demo tenant ready in {elapsed:.1f}s")
    print(f"  tenant_id:   {DEMO_TENANT_ID}")
    print(f"  tenant_slug: {DEMO_TENANT_SLUG}")
    print()
    print("  export TAGPULSE_API_KEY=" + api_key)
    print()
    print("Open the UI and log in as the demo admin to inspect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
