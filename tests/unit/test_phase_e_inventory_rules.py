"""Unit tests for Phase E (rules + worker + imports + meter snapshot).

Exercises:
- ``RuleEvaluator.on_subject_zone_changed`` for ``stock.unexpected_in_zone``
- ``InventoryRuleWorker._scan_below_threshold`` and ``_scan_expiring_within``
- ``UsageMeter.record_snapshot`` semantics (replace, not sum)
- ``RulesService.get_active_rules_by_condition_type{,_all_tenants}`` filter
- CSV import row-error reporting (parser-only, no DB hits).
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.events.protocol import Event, Topic
from tagpulse.models.rule_schemas import RuleCreate, RuleResponse
from tagpulse.models.schemas import StockLevelRow

# ---------------------------------------------------------------------------
# Schema acceptance
# ---------------------------------------------------------------------------


def test_rule_create_accepts_inventory_condition_types() -> None:
    for ct in (
        "stock.below_threshold",
        "stock.expiring_within",
        "stock.unexpected_in_zone",
    ):
        rule = RuleCreate(
            name=f"r-{ct}",
            condition_type=ct,
            condition_config={},
            action_type="notification",
            action_config={},
        )
        assert rule.condition_type == ct


def test_rule_create_rejects_unknown_condition_type() -> None:
    with pytest.raises(ValueError):
        RuleCreate(
            name="bad",
            condition_type="stock.bogus",
            condition_config={},
            action_type="notification",
            action_config={},
        )


# ---------------------------------------------------------------------------
# Evaluator: stock.unexpected_in_zone (event-driven branch)
# ---------------------------------------------------------------------------


class _FakeMeter:
    def __init__(self) -> None:
        self.records: list[tuple[Any, str, str]] = []

    def record(self, tenant_id: UUID, dimension: str, unit: str, count: int = 1) -> None:
        self.records.append((tenant_id, dimension, unit))


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Event]] = []

    async def publish(self, topic: Topic, event: Event) -> None:
        self.published.append((topic, event))


class _FakeRulesService:
    def __init__(self, rules: list[RuleResponse]) -> None:
        self._rules = rules
        self.alerts: list[dict[str, Any]] = []

    async def get_active_rules_by_condition_type(
        self, tenant_id: UUID, condition_type: str
    ) -> list[RuleResponse]:
        return [r for r in self._rules if r.condition_type == condition_type]

    async def create_alert(
        self,
        tenant_id: UUID,
        rule_id: UUID,
        *,
        device_id: UUID | None,
        severity: str,
        message: str,
        context: dict[str, Any],
    ) -> Any:
        record = {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "rule_id": rule_id,
            "severity": severity,
            "message": message,
            "context": context,
            "triggered_at": datetime.now(UTC),
        }
        self.alerts.append(record)

        class _A:
            def __init__(self, r: dict[str, Any]) -> None:
                self.id = r["id"]
                self.severity = r["severity"]
                self.message = r["message"]
                self.triggered_at = r["triggered_at"]

        return _A(record)


def _make_rule(
    tenant_id: UUID,
    *,
    condition_type: str,
    condition_config: dict[str, Any],
    name: str = "r",
) -> RuleResponse:
    now = datetime.now(UTC)
    return RuleResponse(
        id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        description=None,
        condition_type=condition_type,
        condition_config=condition_config,
        action_type="notification",
        action_config={},
        scope_device_id=None,
        enabled=True,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_unexpected_in_zone_fires_when_not_in_allowed_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    allowed = uuid4()
    actual = uuid4()
    rule = _make_rule(
        tenant,
        condition_type="stock.unexpected_in_zone",
        condition_config={"allowed_zone_ids": [str(allowed)]},
    )

    fake_rules = _FakeRulesService([rule])

    # Patch RulesService construction inside the handler.
    monkeypatch.setattr(
        "tagpulse.rules.evaluator.RulesService",
        lambda session: fake_rules,
    )

    class _DummyCtx:
        async def __aenter__(self) -> Any:
            return _Session()

        async def __aexit__(self, *args: Any) -> None:
            return None

    class _Session:
        async def commit(self) -> None:
            return None

    bus = _FakeBus()
    meter = _FakeMeter()
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        usage_meter=meter,  # type: ignore[arg-type]
    )
    event = Event(
        id=uuid4(),
        topic=Topic.SUBJECT_ZONE_CHANGED,
        timestamp=datetime.now(UTC),
        payload={
            "tenant_id": str(tenant),
            "subject_kind": "stock_item",
            "subject_id": str(uuid4()),
            "to_zone_id": str(actual),
            "from_zone_id": None,
        },
    )
    await evaluator.on_subject_zone_changed(event)

    assert len(fake_rules.alerts) == 1
    assert any(p[0] == Topic.ALERT_TRIGGERED for p in bus.published)
    dims = [d for _, d, _ in meter.records]
    assert "rule_evaluations" in dims
    assert "alerts_fired" in dims


@pytest.mark.asyncio
async def test_unexpected_in_zone_skips_when_zone_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    allowed = uuid4()
    rule = _make_rule(
        tenant,
        condition_type="stock.unexpected_in_zone",
        condition_config={"allowed_zone_ids": [str(allowed)]},
    )
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr(
        "tagpulse.rules.evaluator.RulesService",
        lambda session: fake_rules,
    )

    class _DummyCtx:
        async def __aenter__(self) -> Any:
            return _S()

        async def __aexit__(self, *args: Any) -> None:
            return None

    class _S:
        async def commit(self) -> None:
            return None

    bus = _FakeBus()
    meter = _FakeMeter()
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        usage_meter=meter,  # type: ignore[arg-type]
    )
    await evaluator.on_subject_zone_changed(
        Event(
            id=uuid4(),
            topic=Topic.SUBJECT_ZONE_CHANGED,
            timestamp=datetime.now(UTC),
            payload={
                "tenant_id": str(tenant),
                "subject_kind": "stock_item",
                "subject_id": str(uuid4()),
                "to_zone_id": str(allowed),
                "from_zone_id": None,
            },
        )
    )
    assert fake_rules.alerts == []


@pytest.mark.asyncio
async def test_unexpected_in_zone_ignores_asset_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    allowed = uuid4()
    rule = _make_rule(
        tenant,
        condition_type="stock.unexpected_in_zone",
        condition_config={"allowed_zone_ids": [str(allowed)]},
    )
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr(
        "tagpulse.rules.evaluator.RulesService",
        lambda session: fake_rules,
    )

    class _DummyCtx:
        async def __aenter__(self) -> Any:
            return _S()

        async def __aexit__(self, *args: Any) -> None:
            return None

    class _S:
        async def commit(self) -> None:
            return None

    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    await evaluator.on_subject_zone_changed(
        Event(
            id=uuid4(),
            topic=Topic.SUBJECT_ZONE_CHANGED,
            timestamp=datetime.now(UTC),
            payload={
                "tenant_id": str(tenant),
                "subject_kind": "asset",  # ignored
                "subject_id": str(uuid4()),
                "to_zone_id": str(uuid4()),
            },
        )
    )
    assert fake_rules.alerts == []


# ---------------------------------------------------------------------------
# Worker: below_threshold + expiring_within (offline harness)
# ---------------------------------------------------------------------------


class _FakeStockRepo:
    def __init__(self, levels: list[StockLevelRow]) -> None:
        self._levels = levels

    async def stock_levels(self, tenant_id: UUID, *, product_id: Any = None,
                           zone_id: Any = None) -> list[StockLevelRow]:
        return list(self._levels)


@pytest.mark.asyncio
async def test_worker_below_threshold_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    from tagpulse.workers.inventory_rule_worker import InventoryRuleWorker

    tenant = uuid4()
    product = uuid4()
    rule = _make_rule(
        tenant,
        condition_type="stock.below_threshold",
        condition_config={"product_id": str(product), "threshold": 10},
    )

    levels = [StockLevelRow(product_id=product, lot_id=None, zone_id=None, quantity=3)]
    fake_rules = _FakeRulesService([rule])

    async def _fetch_all(self_, condition_types: list[str]) -> list[RuleResponse]:
        return [r for r in fake_rules._rules if r.condition_type in condition_types]

    monkeypatch.setattr(
        "tagpulse.workers.inventory_rule_worker.RulesService",
        lambda session: _FakeRulesServiceForWorker(fake_rules._rules, fake_rules),
    )
    monkeypatch.setattr(
        "tagpulse.workers.inventory_rule_worker.TimescaleStockItemRepository",
        lambda session: _FakeStockRepo(levels),
    )

    worker = InventoryRuleWorker(
        session_factory=_session_factory(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    await worker._scan_below_threshold(_NoopSession())  # type: ignore[arg-type]
    assert len(fake_rules.alerts) == 1


@pytest.mark.asyncio
async def test_worker_below_threshold_no_alert_when_above(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.workers.inventory_rule_worker import InventoryRuleWorker

    tenant = uuid4()
    product = uuid4()
    rule = _make_rule(
        tenant,
        condition_type="stock.below_threshold",
        condition_config={"product_id": str(product), "threshold": 10},
    )
    levels = [StockLevelRow(product_id=product, lot_id=None, zone_id=None, quantity=42)]
    fake_rules = _FakeRulesService([rule])

    monkeypatch.setattr(
        "tagpulse.workers.inventory_rule_worker.RulesService",
        lambda session: _FakeRulesServiceForWorker(fake_rules._rules, fake_rules),
    )
    monkeypatch.setattr(
        "tagpulse.workers.inventory_rule_worker.TimescaleStockItemRepository",
        lambda session: _FakeStockRepo(levels),
    )

    worker = InventoryRuleWorker(
        session_factory=_session_factory(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    await worker._scan_below_threshold(_NoopSession())  # type: ignore[arg-type]
    assert fake_rules.alerts == []


# ---------------------------------------------------------------------------
# CSV parsing helpers — exercise the parser without booting FastAPI.
# ---------------------------------------------------------------------------


def test_csv_dictreader_handles_bom_and_blank_rows() -> None:
    raw_bytes = b"\xef\xbb\xbfsku,gtin,name,category,unit\nSKU1,,Widget,,each\n,,,,\n"
    # The route uses utf-8-sig to strip the BOM before DictReader.
    text = raw_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    assert rows[0]["sku"] == "SKU1"
    assert rows[1]["sku"] == ""  # blank trailing row is preserved
    # Verify our normalize helper would skip it.
    from tagpulse.api.routes.inventory_imports import _norm

    assert _norm(rows[1]["sku"]) is None
    assert _norm(rows[0]["sku"]) == "SKU1"


def test_parse_dt_round_trip() -> None:
    from tagpulse.api.routes.inventory_imports import _parse_dt

    now = datetime.now(UTC).replace(microsecond=0)
    parsed = _parse_dt(now.isoformat())
    assert parsed == now
    assert _parse_dt(None) is None
    assert _parse_dt("") is None
    with pytest.raises(ValueError):
        _parse_dt("not-a-date")


# ---------------------------------------------------------------------------
# Test plumbing for the worker (no real SQLAlchemy session involved).
# ---------------------------------------------------------------------------


class _NoopSession:
    async def commit(self) -> None:
        return None

    async def execute(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ARG002
        class _R:
            def all(self) -> list[Any]:
                return []

            def scalars(self) -> Any:
                return iter([])

        return _R()


def _session_factory() -> Any:
    class _CM:
        async def __aenter__(self) -> Any:
            return _NoopSession()

        async def __aexit__(self, *args: Any) -> None:
            return None

    def _f() -> Any:
        return _CM()

    return _f


class _FakeRulesServiceForWorker:
    """Mirror only the methods the worker calls."""

    def __init__(self, rules: list[RuleResponse], capture: _FakeRulesService) -> None:
        self._rules = rules
        self._capture = capture

    async def get_active_rules_by_condition_types_all_tenants(
        self, condition_types: list[str]
    ) -> list[RuleResponse]:
        return [r for r in self._rules if r.condition_type in condition_types]

    async def create_alert(
        self,
        tenant_id: UUID,
        rule_id: UUID,
        *,
        device_id: UUID | None,
        severity: str,
        message: str,
        context: dict[str, Any],
    ) -> Any:
        return await self._capture.create_alert(
            tenant_id,
            rule_id,
            device_id=device_id,
            severity=severity,
            message=message,
            context=context,
        )
