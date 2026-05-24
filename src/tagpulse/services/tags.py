"""Service-layer helpers for the tag registry (Sprint 50, ADR 028).

Two concerns live here, both pure-Python so they're cheaply unit
testable and reusable from both the API routes (Phase B) and the
registrar worker (Phase D, not yet built):

- :func:`normalize_epc_hex` — canonicalize operator input to the
  uppercase-no-separator form the DB CHECK constraint expects.
- :func:`parse_gs1_uri` — lenient GS1 EPC decoder. Returns the
  ``urn:epc:id:...`` string for EPCs whose header maps to a known
  scheme (SGTIN, SSCC, GIAI, GRAI per
  :mod:`tagpulse.rfid.epc`); ``None`` for raw / proprietary /
  malformed inputs. "Lenient" = a bad EPC does not raise; it just
  yields ``None`` and the caller stores ``gs1_uri=NULL`` (ADR 028
  OQ 2 resolution).
- :func:`validate_status_transition` — enforce the ADR 028
  §"Status enum" transition graph. Only the *operator-driven*
  transitions are exposed here; the registrar worker
  (``registered → active``) and the transfer flow
  (``* → transferred_out``) call dedicated paths and bypass this
  check.
- :func:`parse_tag_import_csv` — Sprint 50 C1 CSV parser for
  ``POST /tags/import``. Pure: takes raw bytes, returns
  ``(valid_rows, errors)``. The route handles file size / row
  cap / rate limit / persistence; this function owns format,
  header, and per-row syntactic validation only.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from tagpulse.models.schemas import TagImportRowError
from tagpulse.rfid.epc import decode_epc_hex


def normalize_epc_hex(raw: str) -> str:
    """Canonicalise an operator-provided EPC.

    Strips whitespace and upper-cases. Does NOT validate length /
    charset — the Pydantic schema's regex does that on the
    normalised value.
    """
    return raw.strip().upper()


def parse_gs1_uri(epc_hex: str) -> str | None:
    """Return the GS1 ``urn:epc:id:...`` URI for a known scheme.

    Returns ``None`` for any input that does not decode cleanly —
    no exceptions escape. The caller stores ``NULL`` in
    ``tags.gs1_uri`` for the ``None`` case; the partial index
    ``ix_tags_tenant_gs1_uri`` keeps NULLs out of the index.
    """
    scheme, decoded = decode_epc_hex(epc_hex)
    if scheme == "raw":
        return None
    uri = decoded.get("uri")
    return uri if isinstance(uri, str) else None


# ADR 028 §"Status enum" transition graph for operator-driven PATCH.
# `registered → active` is the registrar worker's job (Phase D); it is
# intentionally absent here so an admin cannot manually promote a tag
# to active without a corresponding read. `* → transferred_out` belongs
# to the transfer flow (POST /tag-transfers acknowledgement); also
# absent here. Terminal states (`retired`, `defective`,
# `transferred_out`) have no outgoing edges — once retired stays
# retired (operator must mint a new registry row to re-introduce a
# physical tag, which is the audit-trail behaviour we want).
_OPERATOR_TRANSITIONS: dict[str, frozenset[str]] = {
    "registered": frozenset({"retired", "defective"}),
    "active": frozenset({"retired", "defective"}),
    "retired": frozenset(),
    "defective": frozenset(),
    "transferred_out": frozenset(),
}


class StatusTransitionError(ValueError):
    """Raised when a PATCH attempts a status transition the operator
    surface is not allowed to perform.

    Distinct from a 400 on a malformed enum value (Pydantic handles
    that) — this is the *semantic* refusal of e.g. trying to
    un-retire a tag or manually promote ``registered → active``
    (which is the registrar worker's privilege).
    """


def validate_status_transition(current: str, target: str) -> None:
    """Raise :class:`StatusTransitionError` if ``current → target``
    is not an operator-permitted transition.

    Same-state PATCHes (``current == target``) are silently allowed
    so idempotent retries don't 409.
    """
    if current == target:
        return
    allowed = _OPERATOR_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise StatusTransitionError(
            f"transition {current!r} → {target!r} is not permitted via the "
            "operator API (see ADR 028 §Status enum)"
        )


# Mirrors ck_tags_epc_hex_format (migration 043) post-normalisation.
_EPC_HEX_RE = re.compile(r"^[0-9A-F]{16,128}$")


@dataclass(frozen=True)
class TagImportRow:
    """A validated CSV row, normalised and ready for insert.

    ``epc_hex`` is post-:func:`normalize_epc_hex` so the repo can
    insert it verbatim. C1 supports only the ``epc_hex`` column;
    metadata / labels are out of scope for the import endpoint per
    ADR 028 §"Phase C" (labels go through the labels API after
    import).
    """

    epc_hex: str


def parse_tag_import_csv(
    raw: bytes,
) -> tuple[list[TagImportRow], list[TagImportRowError]]:
    """Parse a tag-import CSV into validated rows + per-line errors.

    Contract:

    - UTF-8 with optional BOM (``utf-8-sig``). Any decode error is
      reported as a single ``row=0`` error so the caller can return
      a clean 422 without crashing.
    - A header row is required and must contain ``epc_hex`` (case
      and surrounding whitespace insensitive). Extra columns are
      silently ignored — operators frequently paste reel
      manifests with vendor-specific columns and we don't want to
      force a strip step.
    - Per-row validation runs the same regex as the DB CHECK on
      the *normalised* (upper-cased, stripped) value. Blank
      ``epc_hex`` cells are errors.
    - Duplicate ``epc_hex`` values *within the CSV* are reported
      as errors (catches the operator who pastes the same range
      twice). Cross-CSV / cross-tenant uniqueness is enforced by
      the DB constraint at flush time.

    Returns ``(valid_rows, errors)``. Per ADR 028 OQ 4's
    all-or-nothing rule, the route layer rejects the whole import
    if ``errors`` is non-empty; the partial ``valid_rows`` list is
    still returned for completeness but the route must not write it.
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return [], [TagImportRowError(row=0, epc_hex=None, error=f"file is not valid UTF-8: {exc}")]

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return [], [TagImportRowError(row=0, error="CSV is empty")]
    headers_norm = {(h or "").strip().lower(): h for h in reader.fieldnames}
    if "epc_hex" not in headers_norm:
        return [], [
            TagImportRowError(
                row=0,
                error="CSV is missing required column 'epc_hex'",
            )
        ]
    epc_col = headers_norm["epc_hex"]

    valid: list[TagImportRow] = []
    errors: list[TagImportRowError] = []
    seen: dict[str, int] = {}
    for idx, raw_row in enumerate(reader, start=1):
        raw_value = (raw_row.get(epc_col) or "").strip()
        if not raw_value:
            errors.append(TagImportRowError(row=idx, error="epc_hex is required"))
            continue
        normalised = normalize_epc_hex(raw_value)
        if not _EPC_HEX_RE.match(normalised):
            errors.append(
                TagImportRowError(
                    row=idx,
                    epc_hex=raw_value,
                    error="epc_hex must be 16-128 hex chars (case-insensitive)",
                )
            )
            continue
        prior = seen.get(normalised)
        if prior is not None:
            errors.append(
                TagImportRowError(
                    row=idx,
                    epc_hex=raw_value,
                    error=f"duplicate epc_hex in CSV (first seen on row {prior})",
                )
            )
            continue
        seen[normalised] = idx
        valid.append(TagImportRow(epc_hex=normalised))
    return valid, errors
