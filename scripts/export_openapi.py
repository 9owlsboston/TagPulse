"""Export the FastAPI OpenAPI spec to stdout with stable normalization.

Sprint 28 H6 — the CI ``OpenAPI drift check`` runs ``git diff --exit-code``
against ``openapi.json``, so the output has to be byte-stable across
contributor environments. Two things vary by Python version / FastAPI
release otherwise:

1. **Duplicate per-operation ``security`` entries.** A few routes depend
   on multiple ``APIKeyHeader``-typed dependencies (e.g., the
   ``Authorization`` + ``X-Tenant-ID`` pair in
   ``src/tagpulse/core/user_auth.py``). FastAPI emits one entry per
   dependency, but ``components.securitySchemes`` only registers a
   single ``APIKeyHeader`` scheme, so the duplicates are meaningless
   noise. CPython 3.12 happens to collapse them during dict
   construction; 3.11 doesn't.
2. **Key ordering.** ``json.dumps(..., sort_keys=True)`` already covers
   this.

We dedupe (1) by hashing each security requirement as its sorted-items
JSON form, and write the result with sorted keys and a 2-space indent.
"""

from __future__ import annotations

import json
import sys

from tagpulse.api.main import app


def _dedupe_security(spec: dict) -> None:
    for path_item in spec.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for op in path_item.values():
            if not isinstance(op, dict):
                continue
            security = op.get("security")
            if not isinstance(security, list):
                continue
            seen: set[str] = set()
            deduped: list[dict] = []
            for req in security:
                key = json.dumps(req, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(req)
            op["security"] = deduped


def main() -> None:
    spec = app.openapi()
    _dedupe_security(spec)
    json.dump(spec, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
