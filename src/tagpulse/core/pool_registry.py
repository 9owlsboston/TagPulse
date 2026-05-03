"""Pool registry — single source of truth for which DB pool a tenant talks to.

Built once at startup from ``config/database.yaml`` (or the path in
``settings.database_config_path``). v1 ships with a single ``shared_default``
entry that wraps the legacy ``settings.database_url`` so existing deployments
need zero configuration. Adding a sovereign / regional pool later is a config
edit + one ``UPDATE tenants SET db_pool_key = …`` — **no code change**.

Per [docs/design/storage-strategy.md §6 Q2](../../docs/design/storage-strategy.md).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tagpulse.core.config import settings


class PoolEntry:
    """A single named pool: engine + sessionmaker. Cheap, lazy on first use."""

    def __init__(self, key: str, dsn: str, **engine_kwargs: Any) -> None:
        self.key = key
        self.dsn = dsn
        self._engine_kwargs = engine_kwargs
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(self.dsn, **self._engine_kwargs)
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            self._sessionmaker = async_sessionmaker(
                self.engine, expire_on_commit=False
            )
        return self._sessionmaker

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None


class PoolRegistry:
    """In-memory map of ``db_pool_key`` → :class:`PoolEntry`.

    Strict by default: requesting an unknown key raises rather than silently
    falling back. ``shared_default`` is always present.
    """

    def __init__(self, entries: dict[str, PoolEntry]) -> None:
        if "shared_default" not in entries:
            raise ValueError(
                "PoolRegistry must define a 'shared_default' pool — every "
                "tenant resolves to it unless tenants.db_pool_key is set."
            )
        self._entries = entries

    def sessionmaker_for(self, key: str) -> async_sessionmaker[AsyncSession]:
        try:
            return self._entries[key].sessionmaker
        except KeyError as exc:
            raise KeyError(
                f"Unknown db_pool_key={key!r}. Add it to config/database.yaml "
                f"or update tenants.db_pool_key."
            ) from exc

    def keys(self) -> list[str]:
        return sorted(self._entries.keys())

    async def dispose_all(self) -> None:
        for entry in self._entries.values():
            await entry.dispose()


def _load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must define a top-level mapping")
    pools = loaded.get("pools")
    if not isinstance(pools, dict) or not pools:
        raise ValueError(f"{path} must define a non-empty 'pools' mapping")
    return loaded


def _build_default_registry() -> PoolRegistry:
    """Build the registry, falling back to ``settings.database_url`` if no
    config file is present. Keeps every existing deployment working unchanged.
    """
    cfg_path = Path(settings.database_config_path)
    entries: dict[str, PoolEntry] = {}
    if cfg_path.exists():
        cfg = _load_config(cfg_path)
        for key, raw in cfg["pools"].items():
            if not isinstance(raw, dict) or "dsn" not in raw:
                raise ValueError(
                    f"pools.{key} in {cfg_path} must be a mapping with a 'dsn' field"
                )
            kwargs = {k: v for k, v in raw.items() if k != "dsn"}
            entries[key] = PoolEntry(key=key, dsn=str(raw["dsn"]), **kwargs)
    if "shared_default" not in entries:
        entries["shared_default"] = PoolEntry(
            key="shared_default", dsn=settings.database_url
        )
    return PoolRegistry(entries)


_lock = threading.Lock()
_registry: PoolRegistry | None = None


def get_pool_registry() -> PoolRegistry:
    """Return the process-wide registry, building it lazily on first use."""
    global _registry
    if _registry is None:
        with _lock:
            if _registry is None:
                _registry = _build_default_registry()
    return _registry


def set_pool_registry(registry: PoolRegistry | None) -> None:
    """Test seam — replace or reset the global registry."""
    global _registry
    with _lock:
        _registry = registry
