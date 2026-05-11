#!/usr/bin/env python3
"""One-shot smoke-test bootstrap.

Idempotently brings a fresh local TagPulse instance from "just-migrated" to
"ready to push data and watch the Map":

  1. Upserts the demo tenant (default slug ``test-corp``).
  2. Upserts the admin user and (re)issues an API key.
  3. Enables ``asset`` tracking mode via ``PATCH /tenant/config``.
  4. Creates ``--assets`` simulated assets and binds them to ``TAG0001``…
     ``TAG000N`` so the Map populates as soon as you start the device
     simulator with ``--with-gps``.

After it finishes you get a single ``export TAGPULSE_API_KEY=…`` line — eval
that, then run ``scripts/simulate_devices.py --with-gps`` and open the Map.

Usage:
    python scripts/smoke_setup.py                         # all defaults
    python scripts/smoke_setup.py --assets 10
    python scripts/smoke_setup.py --regenerate-key        # rotate admin key
    python scripts/smoke_setup.py --tenant-slug acme \\
        --tenant-id 22222222-2222-2222-2222-222222222222

The script is **safe to re-run** — it never deletes data, only upserts.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any
from uuid import UUID

import asyncpg
import httpx

from tagpulse.core.user_auth import generate_api_key

API_URL = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000")
DB_URL = os.environ.get(
    "TAGPULSE_SMOKE_DB_URL",
    "postgresql://tagpulse:secret@localhost:5432/tagpulse",
)


async def _connect_db() -> asyncpg.Connection:
    """Connect to Postgres.

    Inside the tools-job, ``POSTGRES_HOST`` / ``POSTGRES_USER`` /
    ``POSTGRES_PASSWORD`` / ``POSTGRES_DB`` are wired separately by Bicep
    (see ``deploy/azure/bicep/modules/tools-job.bicep``). When those are
    present we use them as kwargs so passwords containing URL-special
    characters (``:``, ``@``, ``/``) don't break asyncpg's DSN parser.
    Otherwise fall back to ``TAGPULSE_SMOKE_DB_URL`` for local dev.
    """
    host = os.environ.get("POSTGRES_HOST")
    password = os.environ.get("POSTGRES_PASSWORD")
    if host and password:
        return await asyncpg.connect(
            host=host,
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "tagpulse_admin"),
            password=password,
            database=os.environ.get("POSTGRES_DB", "tagpulse"),
            ssl="require",
        )
    return await asyncpg.connect(DB_URL)


DEFAULT_TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
DEFAULT_TENANT_SLUG = "test-corp"
DEFAULT_TENANT_NAME = "Test Corp"
DEFAULT_ADMIN_EMAIL = "admin@example.com"
DEFAULT_ADMIN_NAME = "Admin"


async def upsert_tenant(conn: asyncpg.Connection, tenant_id: UUID, slug: str, name: str) -> None:
    await conn.execute(
        """
        INSERT INTO tenants (id, name, slug, plan, status, tracking_modes)
        VALUES ($1, $2, $3, 'standard', 'active', '["asset"]'::jsonb)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            slug = EXCLUDED.slug,
            status = 'active'
        """,
        tenant_id,
        name,
        slug,
    )


async def upsert_role_user(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    email: str,
    name: str,
    role: str,
    tenant_slug: str,
    regenerate: bool,
) -> tuple[str | None, bool]:
    """Ensure a user with the given role exists.

    Returns (raw_key_or_None, key_was_issued). Raw key is only returned when a
    new one was generated (user is new, has no key yet, or ``regenerate`` is
    True). Otherwise we cannot recover the existing plaintext (only its hash
    is stored). The user's role and status are kept in sync on every call.
    """
    if role not in {"admin", "editor", "viewer"}:
        raise ValueError(f"unsupported role: {role!r}")

    row = await conn.fetchrow(
        "SELECT id, api_key_hash FROM users WHERE tenant_id = $1 AND email = $2",
        tenant_id,
        email,
    )
    needs_key = row is None or row["api_key_hash"] is None or regenerate

    raw_key: str | None = None
    prefix: str | None = None
    key_hash: str | None = None
    if needs_key:
        raw_key, prefix, key_hash = generate_api_key(tenant_slug)

    if row is None:
        await conn.execute(
            """
            INSERT INTO users (
                id, tenant_id, email, name, role, status,
                api_key_hash, api_key_prefix
            )
            VALUES (gen_random_uuid(), $1, $2, $3, $4, 'active', $5, $6)
            """,
            tenant_id,
            email,
            name,
            role,
            key_hash,
            prefix,
        )
        return raw_key, True

    if needs_key:
        await conn.execute(
            "UPDATE users SET role = $1, status = 'active', "
            "api_key_hash = $2, api_key_prefix = $3 WHERE id = $4",
            role,
            key_hash,
            prefix,
            row["id"],
        )
        return raw_key, True

    # User exists with a key already — leave it alone (but keep role/status).
    await conn.execute(
        "UPDATE users SET role = $1, status = 'active' WHERE id = $2",
        role,
        row["id"],
    )
    return None, False


def _kv_secret_name(tenant_slug: str, role: str) -> str:
    """Derive the Key Vault secret name for a given tenant + role.

    Format: ``tagpulse-<tenant-slug>-<role>-key``. KV secret names must
    match ``[A-Za-z0-9-]{1,127}``; we assume the tenant slug already
    satisfies that (the API enforces it on tenant creation).
    """
    return f"tagpulse-{tenant_slug}-{role}-key"


def push_key_to_keyvault(
    vault_name: str,
    secret_name: str,
    api_key: str,
) -> str:
    """Push an API key to Azure Key Vault, return the secret's version id.

    Imports are lazy so the script keeps running in pure-local dev where
    the optional ``azure`` extra isn't installed. Auth is via
    ``DefaultAzureCredential`` — when run inside the planned tools-job
    (Sprint 26 B1), that resolves to the job's managed identity, which
    must have ``Key Vault Secrets Officer`` on the target vault.
    """
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError as exc:  # pragma: no cover - exercised via CLI
        raise SystemExit(
            "azure-identity / azure-keyvault-secrets not installed. "
            "Reinstall with `pip install -e .[azure]` or use the api "
            "image (which ships the extra by default)."
        ) from exc

    vault_url = f"https://{vault_name}.vault.azure.net"
    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
    secret = client.set_secret(secret_name, api_key)
    # ``secret.id`` looks like ``https://<vault>/secrets/<name>/<version>`` —
    # operators only ever care about the trailing version segment.
    return secret.id.rsplit("/", 1)[-1] if secret.id else "unknown"


def _api_headers(tenant_id: UUID, api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "X-Tenant-ID": str(tenant_id),
        "Content-Type": "application/json",
    }


def enable_asset_tracking(client: httpx.Client, tenant_id: UUID, api_key: str) -> list[str]:
    headers = _api_headers(tenant_id, api_key)
    cfg = client.get(f"{API_URL}/tenant/config", headers=headers)
    cfg.raise_for_status()
    modes = list(cfg.json().get("tracking_modes", []))
    if "asset" in modes:
        return modes
    new_modes = sorted({*modes, "asset"})
    resp = client.patch(
        f"{API_URL}/tenant/config",
        headers=headers,
        json={"tracking_modes": new_modes},
    )
    resp.raise_for_status()
    return new_modes


def enable_subject_telemetry(
    client: httpx.Client,
    tenant_id: UUID,
    api_key: str,
    *,
    kinds: list[str],
) -> list[str]:
    """Sprint 19: opt the tenant into subject-scoped telemetry by adding
    the requested ``subject_kind``\\ s to ``tenants.telemetry_subject_kinds``.

    Idempotent — a no-op when every kind is already opted in. ``device``
    is always present and does not need to be passed. Triggers the
    Sprint 21 ``invalidate_subject_kinds`` cross-process invalidation
    hook so workers pick up the new flags within one cache-TTL window.
    """
    headers = _api_headers(tenant_id, api_key)
    cfg = client.get(f"{API_URL}/tenant/config", headers=headers)
    cfg.raise_for_status()
    current = list(cfg.json().get("telemetry_subject_kinds", ["device"]))
    target = sorted({*current, *kinds})
    if set(target) == set(current):
        return current
    resp = client.patch(
        f"{API_URL}/tenant/config",
        headers=headers,
        json={"telemetry_subject_kinds": target},
    )
    resp.raise_for_status()
    return list(resp.json().get("telemetry_subject_kinds", target))


def assert_legacy_telemetry_models_404(client: httpx.Client, tenant_id: UUID, api_key: str) -> bool:
    """Sprint 28 H6 final removal: the legacy
    ``GET /telemetry-models/{device_type}`` route is gone (the Sprint 21
    410 Gone tombstone was retired after a full retention window). The
    single-segment path is still registered for DELETE/PATCH on
    ``{model_id}``, so a GET hits FastAPI's method router — either 404
    (no method match) or 405 (method not allowed) confirms the legacy
    GET-by-device_type contract is gone.
    """
    headers = _api_headers(tenant_id, api_key)
    resp = client.get(
        f"{API_URL}/telemetry-models/_smoke_probe_device_type",
        headers=headers,
    )
    if resp.status_code in (404, 405):
        print(f"    Sprint 28 H6 cutover OK: legacy endpoint returns {resp.status_code}")
        return True
    print(
        f"    WARN: legacy GET /telemetry-models/{{device_type}} returned "
        f"{resp.status_code} (expected 404 or 405)"
    )
    return False


# Geofence polygon covering the western half of the Bay Area smoke-test
# block (anchor 37.7749, -122.4194). Half the simulated assets start inside
# this box and roughly half outside, so as they wander the geofence eval
# fires zone.entered / zone.exited events steadily — perfect for populating
# the Alerts page.
_GEOFENCE_POLYGON_GEOJSON: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [
        [
            [-122.4204, 37.7739],
            [-122.4194, 37.7739],
            [-122.4194, 37.7759],
            [-122.4204, 37.7759],
            [-122.4204, 37.7739],
        ]
    ],
}


def ensure_site_and_zones(
    client: httpx.Client,
    tenant_id: UUID,
    api_key: str,
) -> tuple[str, str | None, str | None]:
    """Create one site + one geofence zone (and one reader-bound zone if
    any simulated readers exist).

    Returns ``(site_id, geofence_zone_id, reader_zone_id_or_None)``.
    Idempotent: reuses by name.
    """
    headers = _api_headers(tenant_id, api_key)

    # -- Site --
    sites = client.get(f"{API_URL}/sites", headers=headers)
    sites.raise_for_status()
    site_name = "Bay Area HQ"
    site = next((s for s in sites.json() if s["name"] == site_name), None)
    if site is None:
        resp = client.post(
            f"{API_URL}/sites",
            headers=headers,
            json={
                "name": site_name,
                "address": "1 Market St, San Francisco, CA",
                "default_timezone": "America/Los_Angeles",
                "metadata": {"smoke_setup": True},
            },
        )
        resp.raise_for_status()
        site = resp.json()
        print(f"  Created site: {site_name} ({site['id']})")
    else:
        print(f"  Reusing site: {site_name} ({site['id']})")

    # -- Existing zones --
    zones = client.get(f"{API_URL}/zones", headers=headers)
    zones.raise_for_status()
    by_name = {z["name"]: z for z in zones.json()}

    # -- Geofence zone --
    geo_name = "Bay Area West Block"
    geofence_zone_id: str | None = None
    if geo_name in by_name:
        geofence_zone_id = by_name[geo_name]["id"]
        print(f"  Reusing geofence zone: {geo_name} ({geofence_zone_id})")
    else:
        resp = client.post(
            f"{API_URL}/zones",
            headers=headers,
            json={
                "site_id": site["id"],
                "name": geo_name,
                "kind": "geofence",
                "polygon_geojson": _GEOFENCE_POLYGON_GEOJSON,
                "metadata": {"smoke_setup": True},
            },
        )
        if resp.status_code == 201:
            geofence_zone_id = resp.json()["id"]
            print(f"  Created geofence zone: {geo_name} ({geofence_zone_id})")
        else:
            print(f"  FAILED to create geofence zone: {resp.status_code} {resp.text}")

    # -- Reader-bound zone (best-effort, only if a Sim-Reader exists) --
    reader_zone_id: str | None = None
    devices = client.get(
        f"{API_URL}/device-registry",
        headers=headers,
        params={"limit": 1000},
    )
    if devices.status_code == 200:
        sim_readers = [d for d in devices.json() if d.get("name", "").startswith("Sim-Reader-")]
        if sim_readers:
            rb_name = "Sim-Reader-01 Dock"
            if rb_name in by_name:
                reader_zone_id = by_name[rb_name]["id"]
                print(f"  Reusing reader-bound zone: {rb_name} ({reader_zone_id})")
            else:
                resp = client.post(
                    f"{API_URL}/zones",
                    headers=headers,
                    json={
                        "site_id": site["id"],
                        "name": rb_name,
                        "kind": "reader_bound",
                        "fixed_reader_ids": [sim_readers[0]["id"]],
                        "metadata": {"smoke_setup": True},
                    },
                )
                if resp.status_code == 201:
                    reader_zone_id = resp.json()["id"]
                    print(f"  Created reader-bound zone: {rb_name} ({reader_zone_id})")

    return site["id"], geofence_zone_id, reader_zone_id


def ensure_telemetry_model(
    client: httpx.Client,
    tenant_id: UUID,
    api_key: str,
) -> bool:
    """Define a `rfid_reader` telemetry model (temperature + humidity +
    battery_pct) so the Telemetry UI page renders charts.

    Returns True if created or already present.
    """
    headers = _api_headers(tenant_id, api_key)
    desired_metric_names = {
        "temperature",
        "temperature_c",
        "humidity",
        "battery_pct",
    }
    existing = client.get(f"{API_URL}/telemetry-models/rfid_reader", headers=headers)
    if existing.status_code == 200:
        existing_names = {m["name"] for m in existing.json().get("metrics", [])}
        if existing_names >= desired_metric_names:
            print("  Reusing telemetry model: rfid_reader")
            return True
        # Metrics drift (e.g. older smoke runs missed `temperature_c`).
        # Recreate so sensor-tag readings stop landing in quarantine.
        model_id = existing.json()["id"]
        client.delete(f"{API_URL}/telemetry-models/{model_id}", headers=headers)
        print(
            "  Recreating telemetry model rfid_reader to add: "
            f"{sorted(desired_metric_names - existing_names)}"
        )
    resp = client.post(
        f"{API_URL}/telemetry-models",
        headers=headers,
        json={
            "device_type": "rfid_reader",
            "metrics": [
                {
                    "name": "temperature",
                    "unit": "C",
                    "min_value": -20.0,
                    "max_value": 60.0,
                    "description": "Ambient temperature at reader",
                },
                {
                    # Cold-chain reading emitted by sensor-tags (the
                    # simulator embeds this in `tag_data.temperature_c`
                    # 25% of reads). Different metric than the reader's
                    # own `temperature` because it represents the tagged
                    # asset's temperature, not the reader environment.
                    "name": "temperature_c",
                    "unit": "C",
                    "min_value": -40.0,
                    "max_value": 60.0,
                    "description": "Cold-chain tag temperature",
                },
                {
                    "name": "humidity",
                    "unit": "pct",
                    "min_value": 0.0,
                    "max_value": 100.0,
                    "description": "Relative humidity",
                },
                {
                    "name": "battery_pct",
                    "unit": "pct",
                    "min_value": 0.0,
                    "max_value": 100.0,
                    "description": "Reader battery level",
                },
            ],
        },
    )
    if resp.status_code == 201:
        print("  Created telemetry model: rfid_reader (4 metrics)")
        return True
    print(f"  FAILED to create telemetry model: {resp.status_code} {resp.text}")
    return False


def ensure_rules(
    client: httpx.Client,
    tenant_id: UUID,
    api_key: str,
    *,
    geofence_zone_id: str | None,
) -> int:
    """Create a few demo rules so Rules + Alerts pages have data.

    Returns the count of rules created (or already present).
    """
    headers = _api_headers(tenant_id, api_key)
    existing = client.get(f"{API_URL}/rules", headers=headers)
    existing.raise_for_status()
    by_name = {r["name"]: r for r in existing.json()}

    wanted: list[dict[str, Any]] = [
        {
            "name": "High temperature on RFID reader",
            "description": "Fires when a reader reports temperature > 30 C.",
            "condition_type": "threshold",
            "condition_config": {
                "metric_name": "temperature",
                "operator": ">",
                "threshold": 30.0,
                "cooldown_s": 60,
            },
            "action_type": "notification",
            "action_config": {"severity": "warning"},
            "enabled": True,
        },
    ]
    if geofence_zone_id is not None:
        wanted.append(
            {
                "name": "Asset entered Bay Area West Block",
                "description": "Notification when any asset enters the smoke-test geofence.",
                "condition_type": "zone.entered",
                "condition_config": {
                    "zone_id": geofence_zone_id,
                    "cooldown_s": 30,
                },
                "action_type": "notification",
                "action_config": {"severity": "info"},
                "enabled": True,
            }
        )
        wanted.append(
            {
                "name": "Asset exited Bay Area West Block",
                "description": "Notification when any asset leaves the smoke-test geofence.",
                "condition_type": "zone.exited",
                "condition_config": {
                    "zone_id": geofence_zone_id,
                    "cooldown_s": 30,
                },
                "action_type": "notification",
                "action_config": {"severity": "info"},
                "enabled": True,
            }
        )

    count = 0
    for spec in wanted:
        if spec["name"] in by_name:
            print(f"  Reusing rule: {spec['name']}")
            count += 1
            continue
        resp = client.post(f"{API_URL}/rules", headers=headers, json=spec)
        if resp.status_code == 201:
            print(f"  Created rule: {spec['name']}")
            count += 1
        else:
            print(f"  FAILED to create rule '{spec['name']}': {resp.status_code} {resp.text}")
    return count


def _find_binding_holder(
    client: httpx.Client,
    headers: dict[str, str],
    binding_value: str,
) -> str | None:
    """Return the asset id that currently holds an active binding for
    ``binding_value`` (across all assets in the tenant), or None if no
    holder is found."""
    resp = client.get(f"{API_URL}/assets", headers=headers, params={"limit": 1000})
    resp.raise_for_status()
    for asset in resp.json():
        b_resp = client.get(f"{API_URL}/assets/{asset['id']}/bindings", headers=headers)
        if b_resp.status_code != 200:
            continue
        for b in b_resp.json():
            if b.get("unbound_at") is None and b.get("binding_value") == binding_value:
                return str(asset["id"])
    return None


def ensure_assets_with_bindings(
    client: httpx.Client,
    tenant_id: UUID,
    api_key: str,
    *,
    count: int,
    binding_prefix: str,
    binding_kind: str,
) -> list[dict[str, Any]]:
    """Create ``count`` assets named Sim-Pallet-NN, each bound to TAG000N.

    Idempotent: reuses assets by name and skips bindings that already exist.
    """
    headers = _api_headers(tenant_id, api_key)

    # Existing assets by name.
    resp = client.get(f"{API_URL}/assets", headers=headers, params={"limit": 1000})
    resp.raise_for_status()
    existing = {a["name"]: a for a in resp.json()}

    out: list[dict[str, Any]] = []
    for i in range(1, count + 1):
        name = f"Sim-Pallet-{i:02d}"
        binding_value = f"{binding_prefix}{i:04d}"
        if name in existing:
            asset = existing[name]
            print(f"  Reusing asset: {name} ({asset['id']})")
        else:
            create = client.post(
                f"{API_URL}/assets",
                headers=headers,
                json={
                    "name": name,
                    "asset_type": "pallet",
                    "metadata": {"simulated": True, "smoke_setup": True},
                },
            )
            if create.status_code != 201:
                print(f"  FAILED to create {name}: {create.status_code} {create.text}")
                continue
            asset = create.json()
            print(f"  Created asset: {name} ({asset['id']})")

        # Skip binding if already present.
        existing_bindings = client.get(f"{API_URL}/assets/{asset['id']}/bindings", headers=headers)
        existing_bindings.raise_for_status()
        active = [
            b
            for b in existing_bindings.json()
            if b.get("unbound_at") is None and b.get("binding_value") == binding_value
        ]
        if active:
            print(f"    Binding already present: {binding_value}")
            out.append(asset)
            continue

        bind = client.post(
            f"{API_URL}/assets/{asset['id']}/bindings",
            headers=headers,
            json={
                "binding_value": binding_value,
                "binding_kind": binding_kind,
            },
        )
        if bind.status_code in (200, 201):
            print(f"    Bound → {binding_value}")
        elif bind.status_code == 409:
            # binding_value is already actively bound to *another* asset
            # (likely from an earlier smoke run with a different asset
            # layout). Find the holder, unbind it, and retry once.
            print(f"    {binding_value} is bound elsewhere — stealing it back")
            holder_id = _find_binding_holder(client, headers, binding_value)
            if holder_id is None:
                print(f"    FAILED: 409 but couldn't locate holder of {binding_value}")
                continue
            unbind = client.delete(
                f"{API_URL}/assets/{holder_id}/bindings/{binding_value}",
                headers=headers,
            )
            if unbind.status_code not in (200, 204):
                print(
                    f"    FAILED to unbind {binding_value} from "
                    f"{holder_id}: {unbind.status_code} {unbind.text}"
                )
                continue
            print(f"    Unbound {binding_value} from {holder_id}")
            retry = client.post(
                f"{API_URL}/assets/{asset['id']}/bindings",
                headers=headers,
                json={
                    "binding_value": binding_value,
                    "binding_kind": binding_kind,
                },
            )
            if retry.status_code in (200, 201):
                print(f"    Bound → {binding_value} (after steal)")
            else:
                print(f"    FAILED to rebind {binding_value}: {retry.status_code} {retry.text}")
        else:
            print(f"    FAILED to bind {binding_value}: {bind.status_code} {bind.text}")
        out.append(asset)
    return out


async def _run(args: argparse.Namespace) -> int:
    print("=== TagPulse smoke setup ===")
    pg_host = os.environ.get("POSTGRES_HOST")
    if pg_host:
        pg_port = os.environ.get("POSTGRES_PORT", "5432")
        pg_db = os.environ.get("POSTGRES_DB", "tagpulse")
        print(f"DB:  {pg_host}:{pg_port}/{pg_db}")
    else:
        print(f"DB:  {DB_URL.split('@')[-1]}")
    print(f"API: {API_URL}\n")

    print("[1/4] Connecting to database…")
    try:
        conn = await _connect_db()
    except (OSError, asyncpg.PostgresError) as exc:
        print(f"  ERROR: cannot connect: {exc}")
        print("  Set TAGPULSE_SMOKE_DB_URL or check that `docker compose up -d db` is running.")
        return 1

    try:
        print(f"[2/4] Upserting tenant '{args.tenant_slug}' ({args.tenant_id})…")
        await upsert_tenant(conn, args.tenant_id, args.tenant_slug, args.tenant_name)

        print(f"[3/4] Upserting admin user '{args.admin_email}'…")
        raw_key, issued = await upsert_role_user(
            conn,
            tenant_id=args.tenant_id,
            email=args.admin_email,
            name=args.admin_name,
            role="admin",
            tenant_slug=args.tenant_slug,
            regenerate=args.regenerate_key,
        )

        # Optional: also create one editor and one viewer so the UI's role
        # gating and the API's 403 behavior can be exercised end-to-end.
        # Any non-admin users that already exist in the tenant (e.g. from a
        # prior --full run) are picked up automatically so --regenerate-key
        # rotates *all* keys, not just the admin's.
        existing_role_rows = await conn.fetch(
            "SELECT email, name, role FROM users "
            "WHERE tenant_id = $1 AND email <> $2 "
            "AND role IN ('editor', 'viewer') "
            "ORDER BY role, email",
            args.tenant_id,
            args.admin_email,
        )
        role_targets: list[tuple[str, str, str]] = [
            (r["role"], r["email"], r["name"]) for r in existing_role_rows
        ]
        if args.with_roles:
            existing_emails = {r["email"] for r in existing_role_rows}
            for role, email, display in (
                ("editor", "editor@example.com", "Editor"),
                ("viewer", "viewer@example.com", "Viewer"),
            ):
                if email not in existing_emails:
                    role_targets.append((role, email, display))

        role_users: list[tuple[str, str, str | None]] = []
        for role, email, display in role_targets:
            print(f"      Upserting {role} user '{email}'…")
            role_key, _ = await upsert_role_user(
                conn,
                tenant_id=args.tenant_id,
                email=email,
                name=display,
                role=role,
                tenant_slug=args.tenant_slug,
                regenerate=args.regenerate_key,
            )
            role_users.append((role, email, role_key))
    finally:
        await conn.close()

    if raw_key is None:
        existing_key = os.environ.get("TAGPULSE_API_KEY")
        if not existing_key:
            print(
                "\nERROR: admin user already has an API key but plaintext is "
                "not stored. Re-run with --regenerate-key to rotate it, or "
                "export TAGPULSE_API_KEY first so we can finish provisioning."
            )
            return 2
        print(f"  Reusing existing key from $TAGPULSE_API_KEY ({existing_key[:10]}…)")
        api_key = existing_key
    else:
        action = "regenerated" if args.regenerate_key else "issued"
        print(f"  API key {action}: {raw_key[:10]}… ({len(raw_key)} chars)")
        api_key = raw_key

    print("\n[4/4] Provisioning via API…")
    with httpx.Client(timeout=10.0) as client:
        # Sanity-check the API is reachable.
        try:
            health = client.get(f"{API_URL}/health")
            if health.status_code != 200:
                print(f"  API unhealthy: {health.status_code}")
                return 3
        except httpx.ConnectError:
            print(f"  ERROR: cannot reach API at {API_URL}")
            print("  Is `make run` running in another terminal?")
            return 3

        modes = enable_asset_tracking(client, args.tenant_id, api_key)
        print(f"  tracking_modes: {modes}")

        if args.with_subject_telemetry:
            print("  Opting tenant into subject-scoped telemetry…")
            kinds = enable_subject_telemetry(
                client,
                args.tenant_id,
                api_key,
                kinds=["lot", "stock_item"],
            )
            print(f"    telemetry_subject_kinds: {kinds}")
            assert_legacy_telemetry_models_404(client, args.tenant_id, api_key)

        print(f"  Ensuring {args.assets} assets + bindings…")
        ensure_assets_with_bindings(
            client,
            args.tenant_id,
            api_key,
            count=args.assets,
            binding_prefix=args.binding_prefix,
            binding_kind=args.binding_kind,
        )

        geofence_zone_id: str | None = None
        if args.with_zones:
            print("  Provisioning sites + zones…")
            _, geofence_zone_id, _ = ensure_site_and_zones(client, args.tenant_id, api_key)

        if args.with_telemetry_model:
            print("  Provisioning telemetry model…")
            ensure_telemetry_model(client, args.tenant_id, api_key)

        if args.with_rules:
            print("  Provisioning rules…")
            ensure_rules(
                client,
                args.tenant_id,
                api_key,
                geofence_zone_id=geofence_zone_id,
            )
    ui_url = os.environ.get("TAGPULSE_UI_URL", "http://localhost:5173")

    # Sprint 26 D3 — when run inside the tools-job (or any environment where
    # plaintext keys must not hit stdout / Log Analytics), push freshly
    # issued keys to Key Vault and print only the secret coordinates.
    kv_pushes: list[tuple[str, str, str]] = []  # (role, secret_name, version)
    if args.key_vault_name:
        if raw_key is not None:
            secret_name = _kv_secret_name(args.tenant_slug, "admin")
            version = push_key_to_keyvault(args.key_vault_name, secret_name, raw_key)
            kv_pushes.append(("admin", secret_name, version))
        for role, _email, role_key in role_users:
            if role_key is None:
                continue
            secret_name = _kv_secret_name(args.tenant_slug, role)
            version = push_key_to_keyvault(args.key_vault_name, secret_name, role_key)
            kv_pushes.append((role, secret_name, version))

    print()
    print("=" * 60)
    print("Smoke setup complete.")
    print("=" * 60)
    print()
    print("UI login credentials:")
    print(f"  URL:      {ui_url}")
    print(f"  Email:    {args.admin_email}")
    print("  Role:     admin")
    if args.key_vault_name and raw_key is not None:
        admin_secret = _kv_secret_name(args.tenant_slug, "admin")
        print("  API key:  (redacted — pushed to Key Vault)")
        print(f"  KV vault: {args.key_vault_name}")
        print(f"  KV name:  {admin_secret}")
    else:
        print(f"  API key:  {api_key}")
        if raw_key is None:
            print("  (reused from $TAGPULSE_API_KEY)")
        else:
            print("  (NOTE: this is the only time the full key is shown — save it now)")

    if role_users:
        for role, email, role_key in role_users:
            print()
            print(f"  Email:    {email}")
            print(f"  Role:     {role}")
            if role_key is None:
                print(
                    "  API key:  (already issued earlier; re-run with --regenerate-key to rotate)"
                )
            elif args.key_vault_name:
                print("  API key:  (redacted — pushed to Key Vault)")
                print(f"  KV vault: {args.key_vault_name}")
                print(f"  KV name:  {_kv_secret_name(args.tenant_slug, role)}")
            else:
                print(f"  API key:  {role_key}")
                print("  (NOTE: this is the only time the full key is shown — save it now)")
    print()
    if args.key_vault_name and kv_pushes:
        admin_push = next((p for p in kv_pushes if p[0] == "admin"), kv_pushes[0])
        print("Retrieve a key from Key Vault (requires `Key Vault Secrets User`):")
        print()
        print(
            f"  export TAGPULSE_API_KEY=$(az keyvault secret show "
            f"--vault-name {args.key_vault_name} "
            f"--name {admin_push[1]} --query value -o tsv)"
        )
        print()
        print("Secrets written this run:")
        for role, name, version in kv_pushes:
            print(f"  • {role:6s}  {name}  (version {version[:8]}…)")
        print()
    else:
        print("Shell env for the simulator scripts:")
        print()
        print(f"  export TAGPULSE_API_KEY={api_key}")
        print()
    print("Run the data-push loop:")
    print()
    print("  python scripts/simulate_devices.py \\")
    print(f"    --tenant-id {args.tenant_id} \\")
    print(f"    --devices {args.assets} --tags {args.assets} --interval 2 --with-gps")
    print()
    print("  (--tags matches --assets so every read hits a bound tag → all markers appear.)")
    print()
    print("Then open the Map in the UI and pan to the Bay Area")
    print("(~37.7749, -122.4194). Markers should appear within 5 seconds.")
    print()
    if args.full or (
        args.with_zones
        and args.with_telemetry_model
        and args.with_rules
        and args.with_roles
        and args.with_subject_telemetry
    ):
        print("Fixtures provisioned (--full):")
        if args.with_roles:
            print("  • Users → admin@, editor@, viewer@example.com (one API key each)")
        if args.with_zones:
            print("  • Sites & Zones → site 'Bay Area HQ', geofence 'Bay Area West Block'")
        if args.with_telemetry_model:
            print("  • Telemetry → model 'rfid_reader' (temperature, humidity, battery_pct)")
        if args.with_rules:
            print("  • Rules → high-temperature threshold + zone.entered/exited notifications")
        if args.with_subject_telemetry:
            print(
                "  • Tenant → telemetry_subject_kinds includes "
                "'lot' + 'stock_item' (Sprint 19 opt-in)"
            )
            print(
                "  • Sprint 28 H6 cutover verified: GET /telemetry-models/{device_type} → 404/405"
            )
        print("  • Alerts will populate within ~1 min as wandering assets cross the geofence.")
        print()
        print(
            "TIP: pair with `simulate_devices.py --cold-chain` to drive "
            "lot/stock_item telemetry and trigger the Sprint 20 "
            "cold-chain rule."
        )
        print()
    elif not (
        args.with_zones
        or args.with_telemetry_model
        or args.with_rules
        or args.with_subject_telemetry
    ):
        print(
            "TIP: re-run with `--full` to also populate Sites & Zones, "
            "Telemetry, Rules, Alerts, and subject-scoped telemetry."
        )
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenant-id",
        type=UUID,
        default=DEFAULT_TENANT_ID,
        help=f"Tenant UUID (default: {DEFAULT_TENANT_ID})",
    )
    parser.add_argument(
        "--tenant-slug",
        default=DEFAULT_TENANT_SLUG,
        help=f"Tenant slug (default: {DEFAULT_TENANT_SLUG})",
    )
    parser.add_argument(
        "--tenant-name",
        default=DEFAULT_TENANT_NAME,
        help=f"Tenant display name (default: {DEFAULT_TENANT_NAME})",
    )
    parser.add_argument(
        "--admin-email",
        default=DEFAULT_ADMIN_EMAIL,
        help=f"Admin user email (default: {DEFAULT_ADMIN_EMAIL})",
    )
    parser.add_argument(
        "--admin-name",
        default=DEFAULT_ADMIN_NAME,
        help=f"Admin user display name (default: {DEFAULT_ADMIN_NAME})",
    )
    parser.add_argument(
        "--assets",
        type=int,
        default=5,
        help="Number of simulated assets to create + bind (default: 5)",
    )
    parser.add_argument(
        "--binding-prefix",
        default="TAG",
        help="Tag binding prefix (default: TAG → TAG0001, TAG0002, …). "
        "Match the format your simulator emits.",
    )
    parser.add_argument(
        "--binding-kind",
        default="device",
        choices=["device", "epc", "tid"],
        help="Binding kind for the synthetic tags (default: device)",
    )
    parser.add_argument(
        "--regenerate-key",
        action="store_true",
        help="Rotate the admin API key even if one exists. Also rotates "
        "keys for any existing editor/viewer users in the tenant so all "
        "three role keys are reissued together.",
    )
    parser.add_argument(
        "--with-zones",
        action="store_true",
        help="Also provision a site, a geofence zone covering the western "
        "half of the Bay Area smoke-test block, and (if Sim-Reader devices "
        "already exist) one reader-bound zone.",
    )
    parser.add_argument(
        "--with-telemetry-model",
        action="store_true",
        help="Also define a 'rfid_reader' telemetry model (temperature, "
        "humidity, battery_pct) so the Telemetry page renders charts.",
    )
    parser.add_argument(
        "--with-rules",
        action="store_true",
        help="Also create demo rules: high-temperature threshold + (if a "
        "geofence zone exists) zone.entered/exited notifications.",
    )
    parser.add_argument(
        "--with-roles",
        action="store_true",
        help="Also create one editor (editor@example.com) and one viewer "
        "(viewer@example.com) user, each with their own API key, so the "
        "UI's role gating and the API's 403 enforcement can be exercised.",
    )
    parser.add_argument(
        "--with-subject-telemetry",
        action="store_true",
        help="Sprint 19/21: opt the tenant into subject-scoped telemetry "
        "by adding 'lot' and 'stock_item' to telemetry_subject_kinds, and "
        "verify that the Sprint 21 cutover of "
        "GET /telemetry-models/{device_type} returns 404/405 (route removed in Sprint 28 H6). Required "
        "for `simulate_devices.py --cold-chain` to actually populate the "
        "lot/stock_item telemetry rows.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Shortcut for --with-zones --with-telemetry-model --with-rules "
        "--with-roles --with-subject-telemetry. Populates every sidebar "
        "page, creates one user per role, and opts the tenant into "
        "subject-scoped telemetry for lots and stock items.",
    )
    parser.add_argument(
        "--key-vault-name",
        default=os.environ.get("TAGPULSE_SMOKE_KEY_VAULT_NAME"),
        help="Sprint 26 D3: Azure Key Vault name (e.g. 'tpdev-kv'). When "
        "set, freshly issued admin/role API keys are pushed to KV as "
        "'tagpulse-<tenant-slug>-<role>-key' instead of being printed in "
        "plaintext. Required when running this script via the tools-job "
        "so plaintext never lands in Log Analytics. Auth is via "
        "DefaultAzureCredential — the caller (tools-job's managed "
        "identity, or your `az login`) must have `Key Vault Secrets "
        "Officer` on the vault. Defaults to env "
        "$TAGPULSE_SMOKE_KEY_VAULT_NAME if unset.",
    )
    args = parser.parse_args(argv)
    if args.full:
        args.with_zones = True
        args.with_telemetry_model = True
        args.with_rules = True
        args.with_roles = True
        args.with_subject_telemetry = True
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
