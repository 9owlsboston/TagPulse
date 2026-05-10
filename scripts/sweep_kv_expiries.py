"""scripts/sweep_kv_expiries.py

Sprint 28 B4 — in-VNet KV expiry sweeper. Designed to run via
``scripts/azd-job.sh <env> sweep_kv_expiries.py`` so it has KV data-plane
access from inside the VNet.

For each accessible env's vault (defaults to the env the job is running
in), list all secrets whose ``expires`` attribute falls within
``--threshold-days`` (default 30). Emit a JSON report on stdout and
optionally upload it to a Storage container (``--blob-container``).

Pairs with the Sprint 28 D2 alert rule "KV secret expiring" so the alert
has actionable detail attached.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import sys
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kv-expiry-sweep")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--vault-name",
        default=os.environ.get("AZURE_KEYVAULT_NAME"),
        help="KV name (default: $AZURE_KEYVAULT_NAME).",
    )
    p.add_argument(
        "--threshold-days",
        type=int,
        default=30,
        help="Flag any secret whose expires falls within this many days (default: 30).",
    )
    p.add_argument(
        "--include-no-expiry",
        action="store_true",
        help="Also include secrets with no expires attribute set.",
    )
    p.add_argument(
        "--blob-container",
        default=None,
        help="If set, upload the JSON report to this Storage container "
        "(reads $AZURE_STORAGE_ACCOUNT_NAME).",
    )
    p.add_argument(
        "--blob-name",
        default=None,
        help="Blob name (default: kv-expiries/<vault>-<utc-iso>.json).",
    )
    return p.parse_args(argv)


def _to_ts(value: _dt.datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.timestamp())


def _sweep(vault_name: str, threshold_days: int, include_no_expiry: bool) -> dict[str, Any]:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    cred = DefaultAzureCredential()
    client = SecretClient(vault_url=f"https://{vault_name}.vault.azure.net/", credential=cred)

    now = _dt.datetime.now(_dt.UTC)
    threshold = now + _dt.timedelta(days=threshold_days)

    expiring: list[dict[str, Any]] = []
    no_expiry: list[dict[str, Any]] = []

    for secret in client.list_properties_of_secrets():
        attrs = secret
        expires = attrs.expires_on
        item = {
            "name": secret.name,
            "enabled": attrs.enabled,
            "created_on": attrs.created_on.isoformat() if attrs.created_on else None,
            "updated_on": attrs.updated_on.isoformat() if attrs.updated_on else None,
            "expires_on": expires.isoformat() if expires else None,
        }
        if expires is None:
            if include_no_expiry:
                no_expiry.append(item)
            continue
        if expires <= threshold:
            days_remaining = (expires - now).days
            item["days_remaining"] = days_remaining
            expiring.append(item)

    return {
        "vault_name": vault_name,
        "swept_at": now.isoformat(),
        "threshold_days": threshold_days,
        "expiring_count": len(expiring),
        "expiring": expiring,
        "no_expiry_count": len(no_expiry),
        "no_expiry": no_expiry,
    }


def _upload(report: dict[str, Any], container: str, blob_name: str | None) -> str:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    account = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME")
    if not account:
        raise SystemExit("--blob-container requires $AZURE_STORAGE_ACCOUNT_NAME to be set")
    name = blob_name or (
        f"kv-expiries/{report['vault_name']}-"
        f"{_dt.datetime.now(_dt.UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    cred = DefaultAzureCredential()
    svc = BlobServiceClient(account_url=f"https://{account}.blob.core.windows.net", credential=cred)
    blob = svc.get_blob_client(container=container, blob=name)
    blob.upload_blob(json.dumps(report, indent=2).encode("utf-8"), overwrite=True)
    return f"https://{account}.blob.core.windows.net/{container}/{name}"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if not args.vault_name:
        log.error("--vault-name not given and AZURE_KEYVAULT_NAME not set")
        return 2

    log.info("sweeping vault=%s threshold_days=%d", args.vault_name, args.threshold_days)
    report = _sweep(args.vault_name, args.threshold_days, args.include_no_expiry)

    print(json.dumps(report, indent=2))

    if args.blob_container:
        url = _upload(report, args.blob_container, args.blob_name)
        log.info("uploaded report → %s", url)

    log.info(
        "summary: %d expiring within %dd, %d with no expiry",
        report["expiring_count"],
        args.threshold_days,
        report["no_expiry_count"],
    )
    # Exit non-zero if anything is expiring soon, so a scheduled job's
    # execution status is meaningful.
    return 1 if report["expiring_count"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
