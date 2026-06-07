#!/usr/bin/env python3
"""Continuous demo-tenant simulator (Sprint 58 Phase C).

Long-running async orchestrator that keeps the demo tenant alive between
``make demo-tenant`` runs. Issues realistic tag reads on a shift-aware
schedule, simulates intermittent reader outages, and periodically fires
alert-shaped reads so the dashboard stays animated during a review session.

This script is deliberately ``HTTP-only`` (POST /tag-reads); it does not talk
to MQTT, the database, or Key Vault. The same binary runs locally under the
``sim`` docker-compose profile (``make sim-start``) and as a manual-trigger
Azure Container Apps Job in the dev environment
(``scripts/azd-job.sh dev sim_loop.py``). See sprint-58 design doc D1 + D5
+ D6 for the rationale.

Configuration precedence (highest first):
  1. command-line flags
  2. environment variables (``SIM_*`` + ``TAGPULSE_*``)
  3. built-in defaults

Hard rate ceiling: 600 reads/min/tenant, enforced regardless of flag/env
overrides. The Sprint 38 per-tenant rate limit sits around 1 k/min for
unsubscribed tenants, so the ceiling leaves room for real test traffic on
the same dev cluster.

Usage examples:

    # Local: run forever against docker-compose stack, default 200 reads/min
    python scripts/sim_loop.py

    # Local: run for 30 minutes at a brisker pace
    python scripts/sim_loop.py --duration 30m --rate 400

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

    def active_devices(self, now: float) -> list[str]:
        return [d for d in self.devices if self.offline_until.get(d, 0.0) <= now]


def _headers(tenant_id: str, api_key: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": tenant_id,
        "Authorization": f"Bearer {api_key}",
    }


async def _discover_devices(
    client: httpx.AsyncClient, tenant_id: str, api_key: str
) -> list[str]:
    """GET /devices for the tenant, return list of device UUIDs.

    Aborts the loop with a clear message if the tenant has no devices —
    operators should run ``make demo-tenant`` first.
    """
    resp = await client.get(f"{API_URL}/devices", headers=_headers(tenant_id, api_key))
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
    base_rate_per_min: int,
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


async def _run_loop(
    *,
    tenant_id: str,
    api_key: str,
    rate_per_min: int,
    duration_seconds: float,
    state: SimState,
    bucket: TokenBucket,
    stop_event: asyncio.Event,
) -> None:
    """Drive the simulator until duration elapses or stop_event is set."""
    deadline = state.started_at + duration_seconds if duration_seconds > 0 else None
    last_outage_check = state.started_at
    last_status_print = state.started_at

    async with httpx.AsyncClient() as client:
        # Discover devices ONCE at startup so a tenant-add mid-run isn't
        # auto-picked up (operator can restart the loop to refresh).
        state.devices = await _discover_devices(client, tenant_id, api_key)
        print(f"  discovered {len(state.devices)} devices")

        while not stop_event.is_set():
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                print(f"  duration elapsed ({duration_seconds}s); shutting down")
                break

            await _emit_tick(
                client, tenant_id, api_key, state, bucket, rate_per_min
            )

            # Outage check once per minute (tick is 1s).
            if now - last_outage_check >= 60.0:
                _maybe_schedule_outage(state, now)
                last_outage_check = now

            # Alert check once per minute (we self-rate-limit to 15 min).
            if int(now) % 60 == 0:
                await _maybe_fire_alert(client, tenant_id, api_key, state, bucket)

            # Status line every 60s.
            if now - last_status_print >= 60.0:
                uptime = int(now - state.started_at)
                rate = state.total_reads / max(uptime, 1) * 60
                offline = sum(1 for ts in state.offline_until.values() if ts > now)
                print(
                    f"  [status] uptime={uptime}s reads={state.total_reads}"
                    f" ({rate:.0f}/min) alerts={state.total_alerts}"
                    f" offline_devices={offline}",
                    flush=True,
                )
                last_status_print = now

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
        help=f"Tenant UUID (default: {DEMO_TENANT_ID})",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TAGPULSE_API_KEY", ""),
        help="API key (default: $TAGPULSE_API_KEY)",
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=int(os.environ.get("SIM_RATE_PER_MIN", _DEFAULT_RATE_PER_MIN)),
        help=(
            f"Target reads/min/tenant (default: {_DEFAULT_RATE_PER_MIN},"
            f" hard ceiling: {_HARD_CEILING_PER_MIN})"
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

    if not args.api_key:
        print("ERROR: --api-key or $TAGPULSE_API_KEY required", file=sys.stderr)
        return 2

    if args.rate > _HARD_CEILING_PER_MIN:
        print(
            f"ERROR: --rate {args.rate} exceeds hard ceiling"
            f" {_HARD_CEILING_PER_MIN}",
            file=sys.stderr,
        )
        return 2
    if args.rate <= 0:
        print(f"ERROR: --rate must be positive, got {args.rate}", file=sys.stderr)
        return 2

    if args.seed is not None:
        random.seed(args.seed)

    rate_per_sec = args.rate / 60.0
    bucket = TokenBucket(
        rate_per_sec=rate_per_sec * _PEAK_MULTIPLIER,  # account for peak multiplier
        capacity=_BUCKET_BURST_TOKENS,
    )
    state = SimState(devices=[], tags=list(_DEFAULT_TAG_POOL))

    print("Sim loop starting:")
    print(f"  api_url:    {API_URL}")
    print(f"  tenant_id:  {args.tenant_id}")
    print(f"  rate:       {args.rate} reads/min (peak ×{_PEAK_MULTIPLIER})")
    print(
        f"  duration:   {'forever' if args.duration == 0 else f'{int(args.duration)}s'}"
    )

    stop_event = asyncio.Event()

    def _shutdown(signum: int, _frame: object | None) -> None:
        print(f"  received signal {signum}; shutting down gracefully", flush=True)
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        asyncio.run(
            _run_loop(
                tenant_id=args.tenant_id,
                api_key=args.api_key,
                rate_per_min=args.rate,
                duration_seconds=args.duration,
                state=state,
                bucket=bucket,
                stop_event=stop_event,
            )
        )
    except KeyboardInterrupt:
        pass

    uptime = int(time.monotonic() - state.started_at)
    print("Sim loop finished:")
    print(f"  uptime:       {uptime}s")
    print(f"  total_reads:  {state.total_reads}")
    print(f"  total_alerts: {state.total_alerts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
