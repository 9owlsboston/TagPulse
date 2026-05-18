"""Outbound webhook envelope upgrade — ADR-021 v2 / Sprint 41 Phase C.

Adds five top-level fields to the dispatcher-layer payload that fire for
every rule (legacy + signaling). Per ADR-021 v2 §"Outbound envelope" and
the worked example in
``docs/adr/024-position-estimation.md``:

- ``confidence``           — rule confidence threshold as a float
                             (``1.0`` for legacy rules where the concept
                             does not apply)
- ``keySet``               — ordered list of identity-key *names* for the
                             event_type (e.g. ``["asset_id", "zone_id"]``
                             for ``location``). Empty for legacy rules.
- ``eventConfigurationId`` — ``rules.id`` as a string. Lets webhook
                             consumers correlate every fired event back
                             to the source rule without a side lookup.
- ``categoryId``           — matched entity's ``category_id`` as a
                             string (``None`` for legacy / when unknown).
- ``labels``               — matched entity's labels propagated as
                             ``[{"key", "value"}]``. Empty for legacy.

The builder is intentionally **pure** (no I/O): the dispatcher is
responsible for resolving the rule + matched entity and passing the
column values in. Keeps this module trivially testable and the DB
boundary explicit. Closes remediation gap 2.9 in
``docs/design/reference-design-remediation.md``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TypedDict
from uuid import UUID

# Per-event-type keySet definitions. Maps the ADR-021 v2 ``event_type``
# enum values to the ordered identity-key names that uniquely identify a
# fired event. Legacy rules (``event_type IS NULL``) get the empty list.
# Keep alphabetised inside the literal so the field-name list is stable
# in webhook payloads (consumers may index by position).
_KEY_SETS: dict[str, list[str]] = {
    "location": ["asset_id", "zone_id"],
    "geolocation": ["asset_id", "site_id"],
    "geofencing": ["asset_id", "zone_id"],
    "temperature": ["asset_id"],
}


class SignalingEnvelopeFields(TypedDict):
    """The five additive top-level envelope fields per ADR-021 v2."""

    confidence: float
    keySet: list[str]
    eventConfigurationId: str | None
    categoryId: str | None
    labels: list[dict[str, str]]


def derive_key_set(event_type: str | None) -> list[str]:
    """Return the identity-key names for an event_type.

    Returns an empty list for legacy rules (``event_type IS NULL``) or for
    unknown event types — the empty list is a safe default that webhook
    consumers can iterate over without special-casing.
    """
    if event_type is None:
        return []
    return list(_KEY_SETS.get(event_type, []))


def build_envelope(
    *,
    rule_id: UUID | None,
    event_type: str | None,
    confidence_threshold: Decimal | float | None,
    category_id: UUID | str | None = None,
    labels: list[dict[str, str]] | None = None,
) -> SignalingEnvelopeFields:
    """Build the five-field signaling envelope addition.

    Safe defaults match the ADR-021 v2 §"Outbound envelope" contract for
    legacy rules: ``confidence=1.0``, ``keySet=[]``, ``categoryId=None``,
    ``labels=[]``. ``eventConfigurationId`` is ``str(rule_id)`` when
    present and ``None`` otherwise (only true for raw-broadcast events
    that aren't rule-fired — Phase C wires this for
    ``Topic.ALERT_TRIGGERED`` only).
    """
    is_legacy = event_type is None
    confidence_value: float
    if is_legacy:
        # ADR-021 v2: legacy rules don't model confidence; fixed at 1.0
        # so downstream consumers can apply a uniform ``>= threshold``
        # comparison without a None-check.
        confidence_value = 1.0
    elif confidence_threshold is None:
        # Signaling rule without an explicit threshold — treat as "all".
        # Defensive: schema validation requires a value, so this branch
        # is only hit in tests / malformed rows.
        confidence_value = 0.0
    else:
        confidence_value = float(confidence_threshold)

    return SignalingEnvelopeFields(
        confidence=confidence_value,
        keySet=derive_key_set(event_type),
        eventConfigurationId=str(rule_id) if rule_id is not None else None,
        categoryId=str(category_id) if category_id is not None else None,
        labels=list(labels) if labels else [],
    )
