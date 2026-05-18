"""Unit tests for ``tagpulse.integrations.signaling_envelope`` (Sprint 41 Phase C / C1).

Pure builder tests — no DB, no event bus. Asserts the five-field
envelope contract per ADR-021 v2 §"Outbound envelope":

- Legacy rules (``event_type IS NULL``) get safe defaults: confidence
  1.0, empty keySet, None categoryId, empty labels.
- Signaling rules populate ``confidence`` from the threshold,
  ``keySet`` from the event_type table, and ``eventConfigurationId``
  from the rule id.
- ``derive_key_set`` returns the documented per-event_type lists.

Companion to ``test_webhook_envelope.py`` (Phase C / C3 dispatcher path).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from tagpulse.integrations.signaling_envelope import (
    build_envelope,
    derive_key_set,
)


class TestDeriveKeySet:
    """``derive_key_set`` maps event_type to its identity-key list."""

    def test_location_returns_asset_and_zone(self) -> None:
        assert derive_key_set("location") == ["asset_id", "zone_id"]

    def test_geolocation_returns_asset_and_site(self) -> None:
        assert derive_key_set("geolocation") == ["asset_id", "site_id"]

    def test_geofencing_returns_asset_and_zone(self) -> None:
        assert derive_key_set("geofencing") == ["asset_id", "zone_id"]

    def test_temperature_returns_asset_only(self) -> None:
        # Temperature is a per-entity scalar; the only identity key is
        # the entity being measured.
        assert derive_key_set("temperature") == ["asset_id"]

    def test_none_returns_empty(self) -> None:
        # Legacy rules carry ``event_type IS NULL`` → empty keySet.
        assert derive_key_set(None) == []

    def test_unknown_returns_empty(self) -> None:
        # Defensive default: a future event_type added to the column
        # but not to ``_KEY_SETS`` still returns a safe empty list.
        assert derive_key_set("future_event_type") == []

    def test_returned_list_is_fresh_copy(self) -> None:
        # Mutating the result must not corrupt the module-level table
        # (callers like the webhook dispatcher reuse the table per tick).
        out = derive_key_set("location")
        out.append("extra")
        assert derive_key_set("location") == ["asset_id", "zone_id"]


class TestBuildEnvelopeLegacy:
    """Legacy rules (``event_type IS NULL``) get the safe-default envelope."""

    def test_legacy_returns_safe_defaults(self) -> None:
        rule_id = uuid4()
        env = build_envelope(
            rule_id=rule_id,
            event_type=None,
            confidence_threshold=None,
        )
        assert env["confidence"] == 1.0
        assert env["keySet"] == []
        assert env["eventConfigurationId"] == str(rule_id)
        assert env["categoryId"] is None
        assert env["labels"] == []

    def test_legacy_confidence_threshold_is_ignored(self) -> None:
        # Per ADR: legacy rules pin confidence at 1.0 regardless of the
        # column value (column is additive — present on every row but
        # the threshold concept doesn't apply to legacy paths).
        env = build_envelope(
            rule_id=uuid4(),
            event_type=None,
            confidence_threshold=Decimal("0.75"),
        )
        assert env["confidence"] == 1.0

    def test_legacy_keyset_always_empty(self) -> None:
        env = build_envelope(
            rule_id=uuid4(),
            event_type=None,
            confidence_threshold=None,
            category_id=uuid4(),  # Even with category supplied — keySet stays []
            labels=[{"key": "zone", "value": "shipping"}],
        )
        assert env["keySet"] == []

    def test_legacy_categoryid_passthrough_when_provided(self) -> None:
        # Phase D wires the matched-entity lookup; if a caller does pass
        # ``category_id`` it's propagated. Same for labels.
        cat = uuid4()
        env = build_envelope(
            rule_id=uuid4(),
            event_type=None,
            confidence_threshold=None,
            category_id=cat,
            labels=[{"key": "zone", "value": "shipping"}],
        )
        assert env["categoryId"] == str(cat)
        assert env["labels"] == [{"key": "zone", "value": "shipping"}]


class TestBuildEnvelopeSignaling:
    """Signaling rules populate the envelope from the rule + matched entity."""

    def test_location_signaling_populates_keyset(self) -> None:
        env = build_envelope(
            rule_id=uuid4(),
            event_type="location",
            confidence_threshold=Decimal("0.75"),
        )
        assert env["keySet"] == ["asset_id", "zone_id"]

    def test_temperature_signaling_populates_keyset(self) -> None:
        env = build_envelope(
            rule_id=uuid4(),
            event_type="temperature",
            confidence_threshold=Decimal("0.5"),
        )
        assert env["keySet"] == ["asset_id"]

    def test_signaling_confidence_from_decimal(self) -> None:
        env = build_envelope(
            rule_id=uuid4(),
            event_type="location",
            confidence_threshold=Decimal("0.75"),
        )
        assert env["confidence"] == pytest.approx(0.75)
        assert isinstance(env["confidence"], float)

    def test_signaling_confidence_from_float(self) -> None:
        env = build_envelope(
            rule_id=uuid4(),
            event_type="location",
            confidence_threshold=0.5,
        )
        assert env["confidence"] == pytest.approx(0.5)

    def test_signaling_none_threshold_falls_back_to_zero(self) -> None:
        # Defensive: schema requires a value, but if a malformed row
        # reaches the builder we don't blow up — 0.0 means "all".
        env = build_envelope(
            rule_id=uuid4(),
            event_type="location",
            confidence_threshold=None,
        )
        assert env["confidence"] == 0.0

    def test_signaling_event_configuration_id_is_rule_id(self) -> None:
        rule_id = uuid4()
        env = build_envelope(
            rule_id=rule_id,
            event_type="location",
            confidence_threshold=Decimal("0.5"),
        )
        assert env["eventConfigurationId"] == str(rule_id)

    def test_signaling_category_id_propagated_as_string(self) -> None:
        cat = uuid4()
        env = build_envelope(
            rule_id=uuid4(),
            event_type="location",
            confidence_threshold=Decimal("0.5"),
            category_id=cat,
        )
        assert env["categoryId"] == str(cat)

    def test_signaling_labels_propagated(self) -> None:
        labels = [
            {"key": "zone", "value": "shipping"},
            {"key": "shift", "value": "night"},
        ]
        env = build_envelope(
            rule_id=uuid4(),
            event_type="location",
            confidence_threshold=Decimal("0.5"),
            labels=labels,
        )
        assert env["labels"] == labels

    def test_signaling_empty_labels_becomes_empty_list(self) -> None:
        # ``None`` labels (matched entity has no labels) → ``[]``,
        # not ``None``. Consumers can ``len()`` / iterate without
        # null-guarding.
        env = build_envelope(
            rule_id=uuid4(),
            event_type="location",
            confidence_threshold=Decimal("0.5"),
            labels=None,
        )
        assert env["labels"] == []

    def test_signaling_labels_is_fresh_copy(self) -> None:
        # The builder must not alias the caller's labels list — mutating
        # the returned ``labels`` field would otherwise corrupt the
        # cached matched-entity row.
        labels = [{"key": "zone", "value": "shipping"}]
        env = build_envelope(
            rule_id=uuid4(),
            event_type="location",
            confidence_threshold=Decimal("0.5"),
            labels=labels,
        )
        env["labels"].append({"key": "shift", "value": "night"})
        assert labels == [{"key": "zone", "value": "shipping"}]


class TestBuildEnvelopeEdgeCases:
    """Defensive paths — rule_id missing, non-UUID, etc."""

    def test_no_rule_id_event_configuration_id_is_none(self) -> None:
        # Raw-broadcast events (not rule-fired) — the dispatcher uses
        # this branch when the payload has no ``rule_id``.
        env = build_envelope(
            rule_id=None,
            event_type=None,
            confidence_threshold=None,
        )
        assert env["eventConfigurationId"] is None

    def test_category_id_string_accepted(self) -> None:
        # Some callers may pass the category id as a string already.
        cat_str = str(uuid4())
        env = build_envelope(
            rule_id=uuid4(),
            event_type="location",
            confidence_threshold=Decimal("0.5"),
            category_id=cat_str,
        )
        assert env["categoryId"] == cat_str

    def test_uuid_rule_id_serialized_as_string(self) -> None:
        # JSON-safe: ``rule_id`` is a UUID at the DB layer but the
        # webhook payload must serialize it as a string.
        rule_id = UUID("12345678-1234-5678-1234-567812345678")
        env = build_envelope(
            rule_id=rule_id,
            event_type=None,
            confidence_threshold=None,
        )
        assert env["eventConfigurationId"] == "12345678-1234-5678-1234-567812345678"
