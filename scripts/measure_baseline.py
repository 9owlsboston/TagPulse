"""Sprint 58 Phase D — capture API-side latency baseline for the 5 operator tasks.

The §55.C primary pass criterion is a stopwatch test: a human runs 5 UI
tasks against the demo tenant and the new UI must be ≥30 % faster on 4 of 5.
That measurement covers everything between the human's eyes and the
database — render time, query fetch, paint, click latency.

This script captures the *backend* slice of that envelope. For each of the
5 operator tasks it hits the hot-path HTTP endpoint(s) the UI calls when
that task is exercised, repeats N times, and reports p50 / p95 / p99
latency in milliseconds. The numbers slot into
``docs/measurements/sprint-58-baseline.md`` §"API-side baseline" so the
human-run stopwatch + Lighthouse session has a server-side reference to
subtract from when reasoning about UI regressions.

The 5 tasks come from `docs/roadmap.md` §55.C:

    1. Find asset by EPC          → GET /assets?q=<epc>
    2. Triage newest open alert   → GET /alerts?status=open&limit=10
    3. Diagnose offline reader    → GET /device-registry
    4. Check inventory for product → GET /stock-levels
    5. Start tag import           → GET /bulk-operations  (entry list page)

Usage:

    export TAGPULSE_API_KEY=tp_demo-wm-dc_...
    python scripts/measure_baseline.py [--iterations 20] [--json out.json]

Honours TAGPULSE_API_URL (default http://localhost:8000) and the
demo-tenant slug/id baked into ``scripts/seed_demo_tenant.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

API_URL = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")
DEMO_TENANT_SLUG = "demo-wm-dc"
DEMO_TENANT_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{DEMO_TENANT_SLUG}.tagpulse.local"))


@dataclass
class TaskResult:
    name: str
    endpoint: str
    iterations: int
    status_codes: dict[int, int] = field(default_factory=dict)
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(200 <= sc < 300 for sc in self.status_codes)

    def percentile(self, p: float) -> float:
        if not self.latencies_ms:
            return float("nan")
        ordered = sorted(self.latencies_ms)
        # Inclusive linear interpolation, same convention as statistics.quantiles
        k = (len(ordered) - 1) * (p / 100)
        f = int(k)
        c = min(f + 1, len(ordered) - 1)
        if f == c:
            return ordered[f]
        return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def _headers(api_key: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": DEMO_TENANT_ID,
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }


def _measure(
    client: httpx.Client,
    name: str,
    endpoint: str,
    iterations: int,
    api_key: str,
) -> TaskResult:
    result = TaskResult(name=name, endpoint=endpoint, iterations=iterations)
    headers = _headers(api_key)
    # One warmup hit so connection setup / TLS handshake doesn't pollute the
    # first percentile — we report steady-state latency, not cold-start.
    client.get(f"{API_URL}{endpoint}", headers=headers)
    for _ in range(iterations):
        t0 = time.perf_counter()
        resp = client.get(f"{API_URL}{endpoint}", headers=headers)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        result.latencies_ms.append(elapsed_ms)
        result.status_codes[resp.status_code] = result.status_codes.get(resp.status_code, 0) + 1
    return result


def _resolve_sample_epc(client: httpx.Client, api_key: str) -> str:
    """Pick a real EPC from the demo tenant for the 'find asset by EPC' task.

    Falls back to a known-deterministic urn pattern if the API call fails so
    the script never blocks on data-shape drift.
    """
    fallback = "urn:epc:sim:sim-pallet-001"
    try:
        resp = client.get(
            f"{API_URL}/assets",
            headers=_headers(api_key),
            params={"limit": 1},
        )
        if resp.status_code != 200:
            return fallback
        assets = resp.json()
        if not assets:
            return fallback
        asset_id = assets[0]["id"]
        bindings_resp = client.get(
            f"{API_URL}/assets/{asset_id}/bindings",
            headers=_headers(api_key),
        )
        if bindings_resp.status_code != 200:
            return fallback
        bindings = bindings_resp.json()
        if not bindings:
            return fallback
        return bindings[0].get("binding_value", fallback)
    except httpx.HTTPError:
        return fallback


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TAGPULSE_API_KEY"),
        help="Defaults to $TAGPULSE_API_KEY",
    )
    parser.add_argument(
        "--json",
        type=str,
        default=None,
        help="If set, write the full per-task result list to this path as JSON.",
    )
    args = parser.parse_args(argv)

    if not args.api_key:
        print(
            "ERROR: --api-key not supplied and $TAGPULSE_API_KEY unset. "
            "Run scripts/seed_demo_tenant.py first and export the printed key.",
            file=sys.stderr,
        )
        return 2

    print(f"Baseline target → {API_URL}  tenant={DEMO_TENANT_ID}  iters={args.iterations}")
    print()

    with httpx.Client(timeout=30.0) as client:
        # Health gate
        try:
            health = client.get(f"{API_URL}/health")
            if health.status_code != 200:
                print(f"ERROR: /health returned {health.status_code}", file=sys.stderr)
                return 3
        except httpx.HTTPError as exc:
            print(f"ERROR: cannot reach {API_URL}: {exc}", file=sys.stderr)
            return 3

        sample_epc = _resolve_sample_epc(client, args.api_key)
        # The /assets ?q= filter is a substring match on name+labels+bindings;
        # use the last URN segment so the filter exercises the binding-value
        # branch the way the UI does when an operator types an EPC suffix.
        epc_q_term = sample_epc.rsplit(":", 1)[-1] if ":" in sample_epc else sample_epc

        tasks: list[tuple[str, str]] = [
            ("Task 1: find asset by EPC", f"/assets?q={epc_q_term}&limit=25"),
            ("Task 2: triage newest open alert", "/alerts?status=open&limit=10"),
            ("Task 3: diagnose offline reader", "/device-registry?limit=100"),
            ("Task 4: check inventory for product", "/stock-levels?limit=100"),
            ("Task 5: start tag import", "/bulk-operations?limit=50"),
        ]

        results: list[TaskResult] = []
        for name, endpoint in tasks:
            res = _measure(client, name, endpoint, args.iterations, args.api_key)
            results.append(res)
            status_summary = ", ".join(f"{k}×{v}" for k, v in sorted(res.status_codes.items()))
            mark = "OK " if res.all_ok else "ERR"
            print(
                f"  [{mark}] {name:<42} p50={res.percentile(50):6.1f} ms  "
                f"p95={res.percentile(95):6.1f} ms  "
                f"p99={res.percentile(99):6.1f} ms  "
                f"min={min(res.latencies_ms):5.1f}  "
                f"max={max(res.latencies_ms):6.1f}  "
                f"[{status_summary}]"
            )

    print()
    print("Markdown table (paste into docs/measurements/sprint-58-baseline.md):")
    print()
    print("| Task | Endpoint | p50 (ms) | p95 (ms) | p99 (ms) | n | status |")
    print("|---|---|---:|---:|---:|---:|---|")
    for r in results:
        status_summary = ", ".join(f"{k}×{v}" for k, v in sorted(r.status_codes.items()))
        print(
            f"| {r.name} | `GET {r.endpoint}` "
            f"| {r.percentile(50):.1f} | {r.percentile(95):.1f} | {r.percentile(99):.1f} "
            f"| {r.iterations} | {status_summary} |"
        )

    if args.json:
        payload: dict[str, Any] = {
            "api_url": API_URL,
            "tenant_id": DEMO_TENANT_ID,
            "tenant_slug": DEMO_TENANT_SLUG,
            "iterations": args.iterations,
            "sample_epc": sample_epc,
            "results": [
                {
                    "task": r.name,
                    "endpoint": r.endpoint,
                    "status_codes": r.status_codes,
                    "p50_ms": r.percentile(50),
                    "p95_ms": r.percentile(95),
                    "p99_ms": r.percentile(99),
                    "min_ms": min(r.latencies_ms),
                    "max_ms": max(r.latencies_ms),
                    "mean_ms": statistics.fmean(r.latencies_ms),
                    "samples_ms": r.latencies_ms,
                }
                for r in results
            ],
        }
        with open(args.json, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\nWrote {args.json}")

    return 0 if all(r.all_ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
