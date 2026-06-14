#!/usr/bin/env python3
"""Continuous demo-tenant simulator (Sprint 58 Phase C; Sprint 59 multi-tenant).

Long-running async orchestrator that keeps the demo tenants alive between
``make demo-*`` runs. Issues realistic tag reads on a shift-aware schedule,
simulates intermittent reader outages, periodically fires alert-shaped reads,
and heartbeats every reader so the dashboard stays animated — and never drops
to "0 online" — during a review session.

This script is deliberately ``HTTP-only`` (POST /tag-reads); it does not talk
to MQTT, the database, or Key Vault. The same binary runs locally under the
``sim`` docker-compose profile (``make sim-start``) and as a manual-trigger
Azure Container Apps Job in the dev environment
(``scripts/azd-job.sh dev sim_loop.py``). See sprint-58 design doc D1 + D5
+ D6 for the rationale; sprint-59 §59.4 for the multi-tenant extension.

Configuration precedence (highest first):
  1. command-line flags
  2. environment variables (``SIM_*`` + ``TAGPULSE_*``)
  3. built-in defaults

Hard rate ceiling: 600 reads/min in AGGREGATE across all driven tenants,
enforced regardless of flag/env overrides. The Sprint 38 per-tenant rate limit
sits around 1 k/min for unsubscribed tenants, so splitting the aggregate across
2–3 tenants leaves ample room for real test traffic on the same dev cluster.

Usage examples:

    # Single tenant (default = combined demo tenant), 200 reads/min
    python scripts/sim_loop.py --api-key "$TAGPULSE_API_KEY"

    # Multi-tenant: drive all three demo tenants, 200/min split ~67 each
    python scripts/sim_loop.py \\
        --tenants demo-wm-dc:KEY1,demo-inv-coldchain:KEY2,demo-asset-fleet:KEY3

    # Local: run for 30 minutes at a brisker aggregate pace
    python scripts/sim_loop.py --duration 30m --rate 400 --api-key "$KEY"

    # Dev (ACA job, 8 h ceiling matches the replicaTimeout):
    scripts/azd-job.sh dev sim_loop.py -- --duration 8h --rate 200

Environment guard: when ``ENV`` is set and not equal to ``dev`` the loop
refuses to start (defence-in-depth on Hard Constraint 6 from the design
doc; the ACA job bicep sets ``ENV=dev`` explicitly for dev, and ``ENV`` is
unset locally so docker-compose runs unimpeded).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import signal
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

API_URL = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")

# Must match scripts/seed_demo_tenant.py — same deterministic UUID so
# operators don't have to pass --tenant-id when running against the
# bog-standard demo tenant.
DEMO_TENANT_SLUG = "demo-wm-dc"
DEMO_TENANT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, f"{DEMO_TENANT_SLUG}.tagpulse.local")

# Tag pool mirrors backfill_history._DEFAULT_TAG_POOL so the live emissions
# reference the same tag identifiers the historical replay used.
_DEFAULT_TAG_POOL = [f"TAG{i:04d}" for i in range(1, 51)]

# Token-bucket / rate-limit defaults. See sprint-58 design D5.
_DEFAULT_RATE_PER_MIN = 200
_HARD_CEILING_PER_MIN = 600
_BUCKET_BURST_TOKENS = 30  # ~9s of burst headroom at 200/min

# Outage simulator (sprint-58 design "5%/min: one reader briefly offline").
_OUTAGE_PROBABILITY_PER_MIN = 0.05
_OUTAGE_MIN_SECONDS = 3 * 60
_OUTAGE_MAX_SECONDS = 8 * 60

# Alert injector ("1/15min: alert-triggering condition").
_ALERT_INTERVAL_SECONDS = 15 * 60
_ALERT_TEMP_MIN = 35.0
_ALERT_TEMP_MAX = 42.0

# Heartbeat (Sprint 59 §59.7): guarantee every active reader emits at least one
# read within this window so a low-rate / off-hours tenant never drifts past the
# Dashboard's 5-min ``devices_online`` window and shows "0 online" on a cold
# open. Kept comfortably under that 5-min window.
_HEARTBEAT_INTERVAL_SECONDS = 4 * 60

# Shift schedule. Peaks at 08:00 and 13:00 local time (1 h window), 1.5x
# multiplier; off-hours (20:00-06:00) damped to 0.3x. The local timezone is
# taken from ``TZ`` env or the host's ``time.localtime`` baseline.
_PEAK_MULTIPLIER = 1.5
_OFF_HOURS_MULTIPLIER = 0.3
_PEAK_HOURS = (8, 13)  # peak windows centered on these hours, 1h wide
_OFF_HOURS_START = 20
_OFF_HOURS_END = 6  # inclusive on both ends (wraps midnight)

# Duration parser: 30s, 5m, 2h, 1d. ``0`` or unset = run forever.
_DURATION_RE = re.compile(r"^(\d+)([smhd]?)$")
_DURATION_MULTIPLIER = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


# ----------------------------------------------------------------------------
# Token bucket
# ----------------------------------------------------------------------------


@dataclass
class TokenBucket:
    """Classic leaky-bucket rate limiter (refill on take)."""

    rate_per_sec: float
    capacity: float
    tokens: float = field(init=False)
    last_refill: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        # Start full so the simulator hits target rate immediately.
        self.tokens = self.capacity

    def try_take(self, n: float = 1.0) -> bool:
        """Return True and consume ``n`` if available; else return False."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_sec)
            self.last_refill = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False


# ----------------------------------------------------------------------------
# Shift schedule
# ----------------------------------------------------------------------------


def shift_multiplier(now_local: datetime) -> float:
    """Return the rate multiplier for the given local time.

    Pure function for testability — pass any ``datetime`` and assert.
    """
    hour = now_local.hour
    # Off-hours window (wraps midnight)
    if hour >= _OFF_HOURS_START or hour < _OFF_HOURS_END:
        return _OFF_HOURS_MULTIPLIER
    # Peak windows: ±30 min around configured peak hours.
    minutes_since_midnight = hour * 60 + now_local.minute
    for peak_h in _PEAK_HOURS:
        peak_min = peak_h * 60
        if abs(minutes_since_midnight - peak_min) <= 30:
            return _PEAK_MULTIPLIER
    return 1.0


# ----------------------------------------------------------------------------
# State + IO
# ----------------------------------------------------------------------------


@dataclass
class SimState:
    devices: list[str]
    tags: list[str]
    offline_until: dict[str, float] = field(default_factory=dict)
    last_alert_at: float = 0.0
    total_reads: int = 0
    total_alerts: int = 0
    started_at: float = field(default_factory=time.monotonic)
    # device_id -> monotonic timestamp of its most recent emitted read. Drives
    # the heartbeat (see ``_devices_needing_heartbeat``).
    last_emit: dict[str, float] = field(default_factory=dict)

    def active_devices(self, now: float) -> list[str]:
        return [d for d in self.devices if self.offline_until.get(d, 0.0) <= now]


def _headers(tenant_id: str, api_key: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": tenant_id,
        "Authorization": f"Bearer {api_key}",
    }


async def _discover_devices(client: httpx.AsyncClient, tenant_id: str, api_key: str) -> list[str]:
    """GET /device-registry for the tenant, return list of device UUIDs.

    Aborts the loop with a clear message if the tenant has no devices —
    operators should run ``make demo-tenant`` first.
    """
    resp = await client.get(f"{API_URL}/device-registry", headers=_headers(tenant_id, api_key))
    resp.raise_for_status()
    devices = resp.json()
    if not devices:
        print(
            "ERROR: tenant has no devices. Run `make demo-tenant` first.",
            file=sys.stderr,
        )
        sys.exit(2)
    return [d["id"] for d in devices]


def _build_normal_read(device_id: str, tag_id: str) -> dict[str, object]:
    sensor_data: dict[str, float] = {
        "temperature": round(random.uniform(18.0, 28.0), 1),  # noqa: S311
    }
    if random.random() < 0.3:  # noqa: S311
        sensor_data["humidity"] = round(random.uniform(30.0, 80.0), 1)  # noqa: S311
    return {
        "device_id": device_id,
        "tag_id": tag_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "signal_strength": round(random.uniform(-80.0, -20.0), 1),  # noqa: S311
        "sensor_data": sensor_data,
    }


def _build_alert_read(device_id: str, tag_id: str) -> dict[str, object]:
    """High-temperature read that should trip the demo high-temp rule."""
    return {
        "device_id": device_id,
        "tag_id": tag_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "signal_strength": round(random.uniform(-80.0, -20.0), 1),  # noqa: S311
        "sensor_data": {
            "temperature": round(  # noqa: S311
                random.uniform(_ALERT_TEMP_MIN, _ALERT_TEMP_MAX), 1
            ),
            "humidity": round(random.uniform(40.0, 70.0), 1),  # noqa: S311
        },
    }


async def _post_read(
    client: httpx.AsyncClient,
    tenant_id: str,
    api_key: str,
    payload: dict[str, object],
) -> bool:
    """POST a single tag read. Return True on 2xx, False on error.

    Errors are logged to stderr but do not crash the loop — a transient
    503 from the API is normal during dev-stack restarts and the loop must
    keep ticking.
    """
    try:
        resp = await client.post(
            f"{API_URL}/tag-reads",
            headers=_headers(tenant_id, api_key),
            json=payload,
            timeout=10.0,
        )
        if resp.status_code >= 400:
            print(
                f"  WARN: POST /tag-reads → {resp.status_code} {resp.text[:120]}",
                file=sys.stderr,
            )
            return False
        return True
    except httpx.HTTPError as exc:
        print(f"  WARN: POST /tag-reads failed: {exc}", file=sys.stderr)
        return False


# ----------------------------------------------------------------------------
# Tick scheduler
# ----------------------------------------------------------------------------


async def _emit_tick(
    client: httpx.AsyncClient,
    tenant_id: str,
    api_key: str,
    state: SimState,
    bucket: TokenBucket,
    base_rate_per_min: float,
) -> None:
    """One 1-second tick: emit ~rate/60 reads, gated by the token bucket."""
    now = time.monotonic()
    now_local = datetime.now()
    multiplier = shift_multiplier(now_local)
    # Reads we'd like to emit this second.
    target_reads = (base_rate_per_min / 60.0) * multiplier
    # Convert to integer count with probabilistic fractional emission so
    # very low rates (off-hours) still average out correctly.
    whole = int(target_reads)
    if random.random() < (target_reads - whole):  # noqa: S311
        whole += 1

    active = state.active_devices(now)
    if not active:
        return

    for _ in range(whole):
        if not bucket.try_take(1.0):
            return  # back off; resume next tick
        device_id = random.choice(active)  # noqa: S311
        tag_id = random.choice(state.tags)  # noqa: S311
        payload = _build_normal_read(device_id, tag_id)
        if await _post_read(client, tenant_id, api_key, payload):
            state.total_reads += 1
            state.last_emit[device_id] = now


def _maybe_schedule_outage(state: SimState, now: float) -> None:
    """Each minute, 5% chance to take one device offline for 3-8 min."""
    if random.random() >= _OUTAGE_PROBABILITY_PER_MIN:  # noqa: S311
        return
    candidates = [d for d in state.devices if state.offline_until.get(d, 0.0) <= now]
    if not candidates:
        return
    victim = random.choice(candidates)  # noqa: S311
    duration = random.uniform(_OUTAGE_MIN_SECONDS, _OUTAGE_MAX_SECONDS)  # noqa: S311
    state.offline_until[victim] = now + duration
    print(
        f"  [outage] device {victim[:8]} offline for {int(duration)}s",
        file=sys.stdout,
    )


async def _maybe_fire_alert(
    client: httpx.AsyncClient,
    tenant_id: str,
    api_key: str,
    state: SimState,
    bucket: TokenBucket,
) -> None:
    """Every 15 min, fire one alert-shaped read (consumes a bucket token)."""
    now = time.monotonic()
    if state.last_alert_at and (now - state.last_alert_at) < _ALERT_INTERVAL_SECONDS:
        return
    active = state.active_devices(now)
    if not active:
        return
    if not bucket.try_take(1.0):
        return  # bucket dry — try again next minute
    device_id = random.choice(active)  # noqa: S311
    tag_id = random.choice(state.tags)  # noqa: S311
    payload = _build_alert_read(device_id, tag_id)
    if await _post_read(client, tenant_id, api_key, payload):
        state.last_alert_at = now
        state.total_alerts += 1
        print(
            f"  [alert] fired high-temp read on device {device_id[:8]}",
            file=sys.stdout,
        )


# ----------------------------------------------------------------------------
# Multi-tenant runtime (Sprint 59 §59.4)
# ----------------------------------------------------------------------------


@dataclass
class TenantRuntime:
    """Per-tenant simulator state: identity + its own bucket + read state.

    One ``TenantRuntime`` per active demo tenant. Each carries an independent
    ``TokenBucket`` sized to its slice of the aggregate budget, so the loop
    drives N tenants concurrently while the *combined* emission rate stays
    under the aggregate ceiling.
    """

    slug: str
    tenant_id: str
    api_key: str
    state: SimState
    bucket: TokenBucket
    base_rate_per_min: float


def _devices_needing_heartbeat(state: SimState, now: float, interval: float) -> list[str]:
    """Active devices whose most recent emitted read is older than ``interval``.

    Pure function for testability. At startup ``last_emit`` is empty so every
    active device qualifies (warm-up); thereafter only devices the normal
    shift-weighted emission hasn't touched recently surface here.

    A device with no recorded ``last_emit`` is always stale — we must NOT fall
    back to a ``0.0`` sentinel, because ``now`` is ``time.monotonic()`` whose
    origin is arbitrary (on a freshly-booted host it can be < ``interval``,
    which would wrongly suppress the cold-start warm-up).
    """
    return [
        d
        for d in state.active_devices(now)
        if d not in state.last_emit or now - state.last_emit[d] >= interval
    ]


async def _emit_heartbeats(
    client: httpx.AsyncClient,
    tenant_id: str,
    api_key: str,
    state: SimState,
    bucket: TokenBucket,
    now: float,
) -> None:
    """Emit one read per stale active device so no reader drifts offline.

    Bounded by the same token bucket as normal traffic, but stale devices are
    served first (called before the next tick) so a low-rate tenant still keeps
    every reader inside the Dashboard's online window.
    """
    for device_id in _devices_needing_heartbeat(state, now, _HEARTBEAT_INTERVAL_SECONDS):
        if not bucket.try_take(1.0):
            return
        tag_id = random.choice(state.tags)  # noqa: S311
        payload = _build_normal_read(device_id, tag_id)
        if await _post_read(client, tenant_id, api_key, payload):
            state.total_reads += 1
            state.last_emit[device_id] = now


def _print_status(tenants: list[TenantRuntime], now: float, started: float) -> None:
    """One aggregate status line with a per-tenant breakdown (R2 mitigation)."""
    uptime = max(int(now - started), 1)
    total_reads = 0
    total_alerts = 0
    parts: list[str] = []
    for t in tenants:
        offline = sum(1 for ts in t.state.offline_until.values() if ts > now)
        rate = t.state.total_reads / uptime * 60
        parts.append(f"{t.slug}={t.state.total_reads}({rate:.0f}/min,off={offline})")
        total_reads += t.state.total_reads
        total_alerts += t.state.total_alerts
    agg_rate = total_reads / uptime * 60
    print(
        f"  [status] uptime={uptime}s reads={total_reads} ({agg_rate:.0f}/min agg)"
        f" alerts={total_alerts} | " + " ".join(parts),
        flush=True,
    )


async def _run_loop(
    *,
    tenants: list[TenantRuntime],
    duration_seconds: float,
    stop_event: asyncio.Event,
) -> None:
    """Drive N tenants until duration elapses or stop_event is set."""
    started = time.monotonic()
    deadline = started + duration_seconds if duration_seconds > 0 else None
    last_minute_check = started

    async with httpx.AsyncClient() as client:
        # Discover each tenant's devices ONCE at startup so a mid-run tenant
        # change isn't auto-picked up (operator restarts the loop to refresh).
        for t in tenants:
            t.state.devices = await _discover_devices(client, t.tenant_id, t.api_key)
            t.state.started_at = started
            print(f"  [{t.slug}] discovered {len(t.state.devices)} devices")

        while not stop_event.is_set():
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                print(f"  duration elapsed ({duration_seconds}s); shutting down")
                break

            for t in tenants:
                await _emit_tick(
                    client, t.tenant_id, t.api_key, t.state, t.bucket, t.base_rate_per_min
                )

            # Once-per-minute housekeeping: outages, heartbeats, alerts, status.
            if now - last_minute_check >= 60.0:
                for t in tenants:
                    _maybe_schedule_outage(t.state, now)
                    await _emit_heartbeats(client, t.tenant_id, t.api_key, t.state, t.bucket, now)
                    await _maybe_fire_alert(client, t.tenant_id, t.api_key, t.state, t.bucket)
                _print_status(tenants, now, started)
                last_minute_check = now

            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=1.0)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _parse_duration(text: str) -> float:
    """Parse ``30s`` / ``5m`` / ``2h`` / ``1d`` → seconds. ``0`` = forever."""
    match = _DURATION_RE.match(text.strip())
    if not match:
        raise argparse.ArgumentTypeError(
            f"invalid duration: {text!r} (expected e.g. 30s, 5m, 2h, 1d, or 0)"
        )
    value, unit = match.groups()
    return float(value) * _DURATION_MULTIPLIER[unit]


def _tenant_id_for_slug(slug: str) -> str:
    """Deterministic ``uuid5`` for a demo slug — must match seed_demo_tenant.py."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{slug}.tagpulse.local"))


def _parse_tenants(text: str) -> list[tuple[str, str, str]]:
    """Parse ``slug:key,slug:key`` → ``[(slug, tenant_id, key)]``.

    Multi-tenant entry point (Sprint 59 §59.4 D3): each demo tenant carries its
    own admin key, and the tenant UUID is derived from the slug via the same
    deterministic ``uuid5`` the composer uses, so operators only paste
    ``slug:key`` pairs (no UUIDs). Raises ``ValueError`` on a malformed or
    duplicate entry.
    """
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"invalid tenant spec {chunk!r}; expected 'slug:key'")
        slug, key = (part.strip() for part in chunk.split(":", 1))
        if not slug or not key:
            raise ValueError(f"invalid tenant spec {chunk!r}; expected 'slug:key'")
        if slug in seen:
            raise ValueError(f"duplicate tenant slug {slug!r}")
        seen.add(slug)
        out.append((slug, _tenant_id_for_slug(slug), key))
    if not out:
        raise ValueError("no tenants parsed from --tenants / $SIM_TENANTS")
    return out


def _split_rate(aggregate_per_min: float, n_tenants: int) -> float:
    """Divide the aggregate read budget evenly across active tenants.

    The aggregate ceiling is preserved (sum of per-tenant rates == aggregate),
    so adding tenants thins each one's stream rather than multiplying load —
    keeping every tenant well under the Sprint 38 per-tenant limit.
    """
    if n_tenants <= 0:
        raise ValueError("n_tenants must be positive")
    return aggregate_per_min / n_tenants


def _resolve_tenant_specs(args: argparse.Namespace) -> list[tuple[str, str, str]]:
    """Return ``[(slug, tenant_id, key)]`` for the configured run.

    ``--tenants`` / ``$SIM_TENANTS`` selects multi-tenant mode; otherwise the
    legacy single-tenant ``--tenant-id`` / ``--api-key`` path is used unchanged
    (R4: single-tenant stays the default, multi-tenant is opt-in).
    """
    if args.tenants:
        return _parse_tenants(args.tenants)
    label = DEMO_TENANT_SLUG if args.tenant_id == str(DEMO_TENANT_ID) else args.tenant_id
    return [(label, args.tenant_id, args.api_key)]


def _env_guard() -> None:
    """Refuse to run if ``ENV`` is set to anything other than ``dev``.

    Locally ``ENV`` is unset → no-op. In Azure the ACA job bicep sets
    ``ENV=dev`` explicitly; staging/prod jobs would never have this script
    invoked, but the guard is defence-in-depth against a fat-fingered
    ``scripts/azd-job.sh staging sim_loop.py``.
    """
    env = os.environ.get("ENV")
    if env and env != "dev":
        print(
            f"ERROR: refusing to run with ENV={env!r}. sim_loop is dev-only.",
            file=sys.stderr,
        )
        sys.exit(3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenant-id",
        default=os.environ.get("TAGPULSE_TENANT_ID", str(DEMO_TENANT_ID)),
        help=f"Single-tenant mode: tenant UUID (default: {DEMO_TENANT_ID})",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TAGPULSE_API_KEY", ""),
        help="Single-tenant mode: API key (default: $TAGPULSE_API_KEY)",
    )
    parser.add_argument(
        "--tenants",
        default=os.environ.get("SIM_TENANTS", ""),
        help=(
            "Multi-tenant mode: comma-separated 'slug:key' pairs "
            "(e.g. 'demo-wm-dc:KEY1,demo-inv-coldchain:KEY2'). Each tenant "
            "UUID is derived from its slug. Overrides --tenant-id/--api-key. "
            "Defaults to $SIM_TENANTS."
        ),
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=int(os.environ.get("SIM_RATE_PER_MIN", _DEFAULT_RATE_PER_MIN)),
        help=(
            f"Target reads/min in AGGREGATE across all tenants (default:"
            f" {_DEFAULT_RATE_PER_MIN}, hard aggregate ceiling:"
            f" {_HARD_CEILING_PER_MIN}). Split evenly per tenant."
        ),
    )
    parser.add_argument(
        "--duration",
        type=_parse_duration,
        default=_parse_duration(os.environ.get("SIM_DURATION", "0")),
        help="Run for this long, then exit (e.g. 30m, 8h). 0 = forever.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=int(os.environ.get("SIM_SEED", "0")) or None,
        help="Seed PRNG for deterministic replay (default: nondeterministic)",
    )
    args = parser.parse_args()

    _env_guard()

    try:
        specs = _resolve_tenant_specs(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for slug, _tid, key in specs:
        if not key:
            print(
                f"ERROR: missing API key for tenant {slug!r}"
                " (set --api-key/$TAGPULSE_API_KEY or include it in --tenants)",
                file=sys.stderr,
            )
            return 2

    if args.rate > _HARD_CEILING_PER_MIN:
        print(
            f"ERROR: --rate {args.rate} exceeds aggregate hard ceiling {_HARD_CEILING_PER_MIN}",
            file=sys.stderr,
        )
        return 2
    if args.rate <= 0:
        print(f"ERROR: --rate must be positive, got {args.rate}", file=sys.stderr)
        return 2

    if args.seed is not None:
        random.seed(args.seed)

    per_tenant_rate = _split_rate(args.rate, len(specs))
    rate_per_sec = per_tenant_rate / 60.0

    runtimes: list[TenantRuntime] = []
    for slug, tenant_id, key in specs:
        runtimes.append(
            TenantRuntime(
                slug=slug,
                tenant_id=tenant_id,
                api_key=key,
                state=SimState(devices=[], tags=list(_DEFAULT_TAG_POOL)),
                bucket=TokenBucket(
                    rate_per_sec=rate_per_sec * _PEAK_MULTIPLIER,
                    capacity=_BUCKET_BURST_TOKENS,
                ),
                base_rate_per_min=per_tenant_rate,
            )
        )

    print("Sim loop starting:")
    print(f"  api_url:    {API_URL}")
    print(f"  tenants:    {len(runtimes)} ({', '.join(r.slug for r in runtimes)})")
    print(
        f"  rate:       {args.rate} reads/min aggregate"
        f" (~{per_tenant_rate:.0f}/tenant, peak ×{_PEAK_MULTIPLIER})"
    )
    print(f"  duration:   {'forever' if args.duration == 0 else f'{int(args.duration)}s'}")

    stop_event = asyncio.Event()

    def _shutdown(signum: int, _frame: object | None) -> None:
        print(f"  received signal {signum}; shutting down gracefully", flush=True)
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    started = time.monotonic()
    with suppress(KeyboardInterrupt):
        asyncio.run(
            _run_loop(
                tenants=runtimes,
                duration_seconds=args.duration,
                stop_event=stop_event,
            )
        )

    uptime = int(time.monotonic() - started)
    total_reads = sum(r.state.total_reads for r in runtimes)
    total_alerts = sum(r.state.total_alerts for r in runtimes)
    print("Sim loop finished:")
    print(f"  uptime:       {uptime}s")
    print(f"  total_reads:  {total_reads}")
    print(f"  total_alerts: {total_alerts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
