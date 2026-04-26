"""Unit tests for the rule evaluation engine."""

from tagpulse.rules.evaluator import _eval_absence, _eval_rate_change, _eval_threshold


class TestThresholdEvaluation:
    def test_gt_true(self) -> None:
        config = {"field": "signal_strength", "operator": "gt", "value": -50}
        payload = {"signal_strength": -30}
        assert _eval_threshold(config, payload) is True

    def test_gt_false(self) -> None:
        config = {"field": "signal_strength", "operator": "gt", "value": -50}
        payload = {"signal_strength": -70}
        assert _eval_threshold(config, payload) is False

    def test_lt_true(self) -> None:
        config = {"field": "signal_strength", "operator": "lt", "value": -50}
        payload = {"signal_strength": -70}
        assert _eval_threshold(config, payload) is True

    def test_gte_boundary(self) -> None:
        config = {"field": "signal_strength", "operator": "gte", "value": -50}
        payload = {"signal_strength": -50}
        assert _eval_threshold(config, payload) is True

    def test_lte_boundary(self) -> None:
        config = {"field": "signal_strength", "operator": "lte", "value": -50}
        payload = {"signal_strength": -50}
        assert _eval_threshold(config, payload) is True

    def test_eq_match(self) -> None:
        config = {"field": "signal_strength", "operator": "eq", "value": 0}
        payload = {"signal_strength": 0}
        assert _eval_threshold(config, payload) is True

    def test_missing_field_returns_false(self) -> None:
        config = {"field": "temperature", "operator": "gt", "value": 30}
        payload = {"signal_strength": -40}
        assert _eval_threshold(config, payload) is False

    def test_missing_threshold_returns_false(self) -> None:
        config = {"field": "signal_strength", "operator": "gt"}
        payload = {"signal_strength": -40}
        assert _eval_threshold(config, payload) is False

    def test_invalid_operator_returns_false(self) -> None:
        config = {"field": "signal_strength", "operator": "ne", "value": -50}
        payload = {"signal_strength": -40}
        assert _eval_threshold(config, payload) is False

    def test_non_numeric_value_returns_false(self) -> None:
        config = {"field": "tag_id", "operator": "gt", "value": 0}
        payload = {"tag_id": "ABC123"}
        assert _eval_threshold(config, payload) is False


class TestAbsenceEvaluation:
    def test_absence_triggered_different_tag(self) -> None:
        config = {"tag_id": "TAG001", "minutes": 10}
        payload = {"tag_id": "TAG002"}
        assert _eval_absence(config, payload) is True

    def test_absence_not_triggered_same_tag(self) -> None:
        config = {"tag_id": "TAG001", "minutes": 10}
        payload = {"tag_id": "TAG001"}
        assert _eval_absence(config, payload) is False

    def test_absence_no_monitored_tag(self) -> None:
        config = {"minutes": 10}
        payload = {"tag_id": "TAG001"}
        assert _eval_absence(config, payload) is False


class TestRateChangeEvaluation:
    def test_rate_change_triggered(self) -> None:
        config = {"change_percent": 20, "baseline": -50.0, "window_minutes": 5}
        payload = {"signal_strength": -70}  # 40% deviation
        assert _eval_rate_change(config, payload) is True

    def test_rate_change_not_triggered(self) -> None:
        config = {"change_percent": 20, "baseline": -50.0, "window_minutes": 5}
        payload = {"signal_strength": -48}  # 4% deviation
        assert _eval_rate_change(config, payload) is False

    def test_rate_change_no_signal(self) -> None:
        config = {"change_percent": 20, "baseline": -50.0}
        payload = {"tag_id": "X"}
        assert _eval_rate_change(config, payload) is False

    def test_rate_change_no_config(self) -> None:
        config: dict[str, object] = {}
        payload = {"signal_strength": -70}
        assert _eval_rate_change(config, payload) is False

    def test_rate_change_default_baseline(self) -> None:
        config = {"change_percent": 50, "window_minutes": 5}
        payload = {"signal_strength": -80}  # 60% deviation from -50 default
        assert _eval_rate_change(config, payload) is True
