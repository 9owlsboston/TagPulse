"""Print a Key Vault secret value to stdout.

Designed to run inside the tools-job (Sprint 26 B1) so operators on locked-down
laptops (corporate proxy, rotating SNAT IPs, KV publicNetworkAccess=Disabled
per Sprint 23-B) can retrieve secrets without flipping the KV firewall.

The job runs in-VNet with the workload's UAMI, which already holds
``Key Vault Secrets User`` on the deployment KV (granted by
deploy/azure/bicep/modules/identity.bicep at provision time).

Usage from a laptop::

    scripts/azd-job.sh dev get_kv_secret.py -- --name tagpulse-test-corp-admin-key

To fetch several secrets in a single job run (avoids paying the tools-job
cold-start cost once per secret)::

    scripts/azd-job.sh dev get_kv_secret.py -- \
        --names tagpulse-demo-wm-dc-admin-key,tagpulse-demo-wm-dc-editor-key

The script prints two sentinel-bracketed lines so the wrapper can robustly
extract just the value from the streamed log output:

    ===KV_SECRET_BEGIN===
    <secret value>
    ===KV_SECRET_END===

In ``--names`` mode the block is bracketed by ``===KV_SECRET_MULTI_BEGIN===`` /
``===KV_SECRET_MULTI_END===`` and contains one ``name=value`` line per secret.
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--name",
        help="Secret name (e.g. tagpulse-test-corp-admin-key). Required unless --names or --list.",
    )
    ap.add_argument(
        "--names",
        help="Comma-separated secret names to fetch in a single job run "
        "(e.g. tagpulse-demo-wm-dc-admin-key,tagpulse-demo-wm-dc-editor-key). "
        "Avoids paying the tools-job cold-start cost once per secret. Each "
        "value is emitted as a sentinel-bracketed 'name=value' line so the "
        "wrapper can extract them; missing secrets are reported as "
        "'name=<NOT_FOUND>' without aborting the rest.",
    )
    ap.add_argument(
        "--list",
        action="store_true",
        dest="list_names",
        help="List all secret names in the vault and exit (does not print values).",
    )
    ap.add_argument(
        "--vault",
        default=os.environ.get("TAGPULSE_SMOKE_KEY_VAULT_NAME"),
        help="Key Vault name (not URI). Defaults to $TAGPULSE_SMOKE_KEY_VAULT_NAME, "
        "which the tools-job sets to the deployment KV.",
    )
    ap.add_argument(
        "--version",
        default=None,
        help="Secret version. Omit for latest.",
    )
    args = ap.parse_args()

    if not args.list_names and not args.name and not args.names:
        print(
            "error: one of --name, --names or --list is required",
            file=sys.stderr,
        )
        return 2

    if not args.vault:
        print(
            "error: --vault not set and TAGPULSE_SMOKE_KEY_VAULT_NAME not in env",
            file=sys.stderr,
        )
        return 2

    # Soft-import so a developer running this on a laptop without the
    # azure-identity extra installed gets a clear error.
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError as exc:
        print(
            f"error: missing azure SDK ({exc}); install with `pip install '.[azure]'`",
            file=sys.stderr,
        )
        return 2

    vault_url = f"https://{args.vault}.vault.azure.net"
    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())

    if args.list_names:
        # Same sentinel pattern so scripts/azd-kv-get.sh --list can extract
        # the names cleanly out of the streamed Log Analytics tail.
        print("===KV_SECRET_LIST_BEGIN===")
        for prop in client.list_properties_of_secrets():
            print(prop.name)
        print("===KV_SECRET_LIST_END===")
        return 0

    if args.names:
        # Batch mode: fetch several secrets in this single job execution so
        # the caller pays the tools-job cold-start once instead of once per
        # secret. Output is a sentinel-bracketed block of 'name=value' lines;
        # a missing secret is reported inline so one typo doesn't abort the
        # rest of the batch.
        from azure.core.exceptions import ResourceNotFoundError

        wanted = [n.strip() for n in args.names.split(",") if n.strip()]
        print("===KV_SECRET_MULTI_BEGIN===")
        for name in wanted:
            try:
                value = client.get_secret(name).value
            except ResourceNotFoundError:
                value = "<NOT_FOUND>"
            print(f"{name}={value}")
        print("===KV_SECRET_MULTI_END===")
        return 0

    secret = client.get_secret(args.name, version=args.version)

    # Sentinel-bracketed output so scripts/azd-kv-get.sh can grep just the value
    # out of the streamed Log Analytics tail (which interleaves az CLI noise).
    print("===KV_SECRET_BEGIN===")
    print(secret.value)
    print("===KV_SECRET_END===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
