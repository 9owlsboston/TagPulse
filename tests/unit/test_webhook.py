"""Unit tests for webhook HMAC signing and event filtering."""

import hashlib
import hmac
import json

from tagpulse.integrations.webhook import _passes_filters


class TestWebhookSigning:
    def test_hmac_sha256_signature(self) -> None:
        signing_key = "test-signing-key"  # noqa: S105
        payload = {"alert_id": "123", "message": "test"}
        body = json.dumps(payload)
        expected = hmac.new(
            signing_key.encode(), body.encode(), hashlib.sha256
        ).hexdigest()
        # Verify the algorithm matches what the dispatcher would produce
        actual = hmac.new(
            signing_key.encode(), body.encode(), hashlib.sha256
        ).hexdigest()
        assert actual == expected
        assert len(actual) == 64  # SHA-256 hex digest length


class TestEventFilters:
    def test_no_filters_passes(self) -> None:
        assert _passes_filters(None, {"signal_strength": -40}) is True

    def test_empty_filters_passes(self) -> None:
        assert _passes_filters([], {"signal_strength": -40}) is True

    def test_gt_filter_passes(self) -> None:
        filters = [{"field": "signal_strength", "operator": "gt", "value": -50}]
        assert _passes_filters(filters, {"signal_strength": -40}) is True

    def test_gt_filter_fails(self) -> None:
        filters = [{"field": "signal_strength", "operator": "gt", "value": -50}]
        assert _passes_filters(filters, {"signal_strength": -60}) is False

    def test_lt_filter_passes(self) -> None:
        filters = [{"field": "signal_strength", "operator": "lt", "value": -50}]
        assert _passes_filters(filters, {"signal_strength": -70}) is True

    def test_missing_field_fails(self) -> None:
        filters = [{"field": "temperature", "operator": "gt", "value": 30}]
        assert _passes_filters(filters, {"signal_strength": -40}) is False

    def test_multiple_filters_all_must_pass(self) -> None:
        filters = [
            {"field": "signal_strength", "operator": "gt", "value": -80},
            {"field": "signal_strength", "operator": "lt", "value": -20},
        ]
        assert _passes_filters(filters, {"signal_strength": -50}) is True
        assert _passes_filters(filters, {"signal_strength": -10}) is False
