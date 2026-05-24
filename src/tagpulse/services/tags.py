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
"""

from __future__ import annotations

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
