"""Floor-position estimator worker (Sprint 66, Phase 2 — Option C tick).

Drives :class:`tagpulse.services.floor_position_estimator.FloorPositionEstimatorService`
on a fixed cadence, mirroring the established worker shape (``start`` / ``stop`` /
``_loop`` / ``run_once``). The per-tenant recompute interval ``D``
(``position_strategy.recompute_interval_s``) is a future refinement; this worker
ticks at one base cadence and each pass recomputes every opted-in tenant.

Not registered in the app by default — activation lands with the concrete DB
adapters in the next slice.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from tagpulse.services.floor_position_estimator import FloorPositionEstimatorService

logger = logging.getLogger(__name__)


class FloorPositionWorker:
    """Periodic Option-C recompute of computed floor positions."""

    def __init__(
        self,
        service: FloorPositionEstimatorService,
        *,
        interval_s: float = 3.0,
    ) -> None:
        self._service = service
        self._interval = interval_s
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("FloorPositionWorker started (interval=%.1fs)", self._interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("FloorPositionWorker stopped")

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:  # pragma: no cover - defensive
                logger.exception("FloorPositionWorker pass failed")
            await asyncio.sleep(self._interval)

    async def run_once(self, now: datetime | None = None) -> int:
        written = await self._service.run_once(now if now is not None else datetime.now(UTC))
        if written:
            logger.debug("FloorPositionWorker wrote %d computed fixes", written)
        return written


__all__ = ["FloorPositionWorker"]
