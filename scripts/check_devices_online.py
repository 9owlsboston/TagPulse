#!/usr/bin/env python3
"""Probe device-registry online status against the dashboard's 5-minute window.

Lists every device with its connection_state and the age of its last_seen
timestamp, and flags which ones the Dashboard would count as "online" (state
``online`` AND last seen < 300s ago). Useful when a simulator run leaves
readers idle past the online window — see the "dwell vs heartbeat" sim gap in
``docs/backlog.md`` (Post-Sprint-58 cluster).

Usage:
    export TAGPULSE_API_KEY=tp_demo-wm-dc_...
    python scripts/check_devices_online.py

Read-only; safe to run anytime. Local/dev demo tooling.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from typing import Any

import httpx

API = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")
KEY = os.environ["TAGPULSE_API_KEY"]
DEMO_TENANT_SLUG = os.environ.get("DEMO_TENANT_SLUG", "demo-wm-dc")
TID = os.environ.get("TAGPULSE_TENANT_ID") or str(
    uuid.uuid5(uuid.NAMESPACE_DNS, f"{DEMO_TENANT_SLUG}.tagpulse.local")
)
H = {"X-Tenant-ID": TID, "Authorization": f"Bearer {KEY}"}

ONLINE_WINDOW_SECONDS = 300


def main() -> None:
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{API}/device-registry?limit=1000", headers=H)
        r.raise_for_status()
        payload: Any = r.json()
    devices = payload if isinstance(payload, list) else payload.get("items", [])

    now = dt.datetime.now(dt.UTC)
    online = 0
    for device in devices:
        last_seen = device.get("last_seen")
        age = None
        if last_seen:
            seen = dt.datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            age = (now - seen).total_seconds()
        conn = device.get("connection_state")
        fresh = age is not None and age < ONLINE_WINDOW_SECONDS and conn == "online"
        online += int(fresh)
        flag = "ONLINE" if fresh else ""
        age_s = f"{age:.0f}" if age is not None else "None"
        print(f"  {str(device.get('name')):16} conn={str(conn):8} age={age_s:>6}s {flag}")
    print(f"\n==> {online}/{len(devices)} online (5-min window)")


if __name__ == "__main__":
    main()
