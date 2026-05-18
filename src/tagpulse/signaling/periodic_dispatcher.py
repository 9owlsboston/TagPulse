"""Periodic signaling-rule dispatcher (Sprint 41 Phase B3 / ADR-021 v2).

Background worker that wakes on a short loop tick and evaluates
``signaling.<event_type>.periodic`` rules whose configured
``cadence_minutes`` has elapsed since their last evaluation. This Phase B
implementation is a *shell*: it owns the cadence accounting and
rule-discovery loop, but delegates the actual processor logic to
:meth:`PeriodicSignalingDispatcher._evaluate_periodic_rule`, which is a
no-op in Phase B. Phase D will replace the hook with the IsolatedZones /
OverlappingZones / temperature processors per ADR-021.

Design mirrors :class:`tagpulse.workers.inventory_rule_worker.InventoryRuleWorker`:

* ``start()`` creates one asyncio task that runs ``_loop()``.
* ``_loop()`` calls :meth:`run_once` then ``asyncio.sleep(tick_interval_s)``.
* ``run_once()`` is public so tests can drive ticks deterministically
  without spinning up the loop.

Cadence accounting lives in-memory (``_last_fired``). Restarting the
worker resets the cadence clock for all rules \u2014 acceptable for Phase B
since the rules-engine output path still writes a real alert row
(Phase D), giving operators a persistent record of evaluations. If
post-restart cadence drift becomes a problem in Phase E, we can promote
``last_fired_at`` to a column on ``rules`` or a sibling table.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.core.usage_meter import UsageMeter
from tagpulse.events.protocol import EventBus
from tagpulse.rules import RulesService
from tagpulse.signaling.overlapping_zones import OverlappingZonesProcessor

if TYPE_CHECKING:
    from tagpulse.models.rule_schemas import RuleResponse

logger = logging.getLogger(__name__)


# All signaling periodic condition_types currently defined in ADR-021 v2.
# Kept as a module constant so the test suite can assert exact coverage
# and the dispatcher's worker registration in ``api/main.py`` is
# obviously the full set.
SIGNALING_PERIODIC_CONDITION_TYPES: tuple[str, ...] = (
    "signaling.location.periodic",
    "signaling.geolocation.periodic",
    "signaling.temperature.periodic",
)


class PeriodicSignalingDispatcher:
    """Periodic evaluator for ``signaling.*.periodic`` rules."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        usage_meter: UsageMeter,
        *,
        tick_interval_s: float = 60.0,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._meter = usage_meter
        self._tick_interval = tick_interval_s
        self._task: asyncio.Task[None] | None = None
        # Per-rule last-fired timestamps. Keyed by rule id so a paused
        # then re-enabled rule resumes against its prior cadence rather
        # than firing immediately on re-enable. Cleared at process
        # restart (see module docstring trade-off).
        self._last_fired: dict[UUID, datetime] = {}
        # Sprint 41 Phase D2: per-process OverlappingZones processor.
        # The processor is stateless across rules and re-used on each
        # tick to avoid the per-call import + class-construction cost.
        # ``signaling.<event_type>.periodic`` rules with
        # ``processor='overlapping_zones'`` dispatch through it; rules
        # with ``processor IS NULL`` or ``'isolated_zones'`` are handled
        # by the existing ingestion-path emission of
        # ``subject.zone_changed`` and do not need a periodic processor.
        self._overlapping_processor = OverlappingZonesProcessor(event_bus)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "PeriodicSignalingDispatcher started (tick=%.0fs)",
            self._tick_interval,
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("PeriodicSignalingDispatcher stopped")

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:  # pragma: no cover - defensive
                logger.exception("PeriodicSignalingDispatcher tick failed")
            await asyncio.sleep(self._tick_interval)

    async def run_once(self) -> None:
        """One dispatcher tick \u2014 public for tests.

        Opens a session, lists all enabled rules with a periodic
        condition_type across all tenants, then per rule checks the
        in-memory ``_last_fired`` map against the rule's configured
        ``cadence_minutes``. Rules whose cadence has elapsed get the
        evaluator hook invoked.
        """

        async with self._session_factory() as session:
            service = RulesService(session)
            rules = await service.get_active_rules_by_condition_types_all_tenants(
                list(SIGNALING_PERIODIC_CONDITION_TYPES)
            )
            now = datetime.now(UTC)
            for rule in rules:
                if not self._is_due(rule, now):
                    continue
                try:
                    await self._evaluate_periodic_rule(session, service, rule)
                except Exception:  # pragma: no cover - defensive
                    logger.exception(
                        "PeriodicSignalingDispatcher rule evaluation failed: rule=%s tenant=%s",
                        rule.id,
                        rule.tenant_id,
                    )
                    continue
                # Stamp last_fired even if the evaluator decided not to
                # raise an alert \u2014 the cadence is the *evaluation*
                # cadence, not the firing cadence.
                self._last_fired[rule.id] = now
            await session.commit()

    def _is_due(self, rule: RuleResponse, now: datetime) -> bool:
        """Return ``True`` when the rule's cadence window has elapsed.

        A rule is due either when we've never evaluated it (no entry in
        ``_last_fired``) or when ``now - last_fired >= cadence_minutes``.
        Rules with an invalid or missing ``cadence_minutes`` are
        skipped \u2014 the Pydantic schema enforces validity on writes, so
        a missing value here points to a row predating the migration or
        a manual DB tweak.
        """

        cadence = rule.condition_config.get("cadence_minutes")
        if not isinstance(cadence, int) or cadence < 1:
            logger.warning(
                "Skipping signaling rule %s: invalid cadence_minutes=%r",
                rule.id,
                cadence,
            )
            return False
        last = self._last_fired.get(rule.id)
        if last is None:
            return True
        return (now - last) >= timedelta(minutes=cadence)

    async def _evaluate_periodic_rule(
        self,
        session: AsyncSession,
        service: RulesService,
        rule: RuleResponse,
    ) -> None:
        """Dispatch one periodic rule to its configured processor.

        Phase D2 wiring: rules with ``processor='overlapping_zones'``
        run the :class:`OverlappingZonesProcessor` aggregation cycle
        and emit ``signaling.attribution_settled`` events. Rules with
        ``processor='isolated_zones'`` or ``processor IS NULL`` are
        already served by the existing ingestion-path emission of
        ``subject.zone_changed`` on every tag-read; the periodic
        dispatcher logs the tick for observability but does no extra
        work (the alerts are produced by the existing zone-changed
        rules pipeline).

        A meter tick is recorded once per dispatched rule regardless of
        processor so per-tenant evaluation counts remain comparable
        across processor choices.
        """

        self._meter.record(rule.tenant_id, "rule_evaluations", "evaluations")
        processor = rule.processor or "isolated_zones"
        if processor == "overlapping_zones":
            try:
                emitted = await self._overlapping_processor.run_once_for_rule(session, rule)
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "OverlappingZones run failed: rule=%s tenant=%s",
                    rule.id,
                    rule.tenant_id,
                )
                return
            logger.info(
                "PeriodicSignalingDispatcher OverlappingZones rule=%s "
                "tenant=%s event_type=%s emitted=%d attribution_settled "
                "events",
                rule.id,
                rule.tenant_id,
                rule.event_type,
                emitted,
            )
            return
        logger.info(
            "PeriodicSignalingDispatcher IsolatedZones tick rule=%s "
            "tenant=%s event_type=%s trigger=%s (no-op \u2014 served by "
            "ingestion-path zone-changed events)",
            rule.id,
            rule.tenant_id,
            rule.event_type,
            rule.trigger,
        )


__all__ = [
    "PeriodicSignalingDispatcher",
    "SIGNALING_PERIODIC_CONDITION_TYPES",
]
