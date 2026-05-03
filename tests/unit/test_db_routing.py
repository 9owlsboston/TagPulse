"""Sprint 13b — tenant context helpers + pool registry routing."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from tagpulse.core import context
from tagpulse.core.pool_registry import (
    PoolEntry,
    PoolRegistry,
    _build_default_registry,
    _load_config,
    set_pool_registry,
)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Each test starts with a clean lazy slot."""
    set_pool_registry(None)
    yield
    set_pool_registry(None)


# ── PoolRegistry ────────────────────────────────────────────────────────────


def test_registry_requires_shared_default() -> None:
    with pytest.raises(ValueError, match="shared_default"):
        PoolRegistry({"other": PoolEntry(key="other", dsn="sqlite+aiosqlite://")})


def test_registry_unknown_key_raises() -> None:
    reg = PoolRegistry(
        {"shared_default": PoolEntry("shared_default", "sqlite+aiosqlite://")}
    )
    with pytest.raises(KeyError, match="db_pool_key"):
        reg.sessionmaker_for("does_not_exist")


def test_registry_keys_are_sorted() -> None:
    reg = PoolRegistry(
        {
            "shared_default": PoolEntry("shared_default", "sqlite+aiosqlite://"),
            "eu_west": PoolEntry("eu_west", "sqlite+aiosqlite://"),
        }
    )
    assert reg.keys() == ["eu_west", "shared_default"]


def test_load_config_parses_pools(tmp_path: Path) -> None:
    cfg = tmp_path / "database.yaml"
    cfg.write_text(
        "pools:\n"
        "  shared_default:\n"
        "    dsn: postgresql+asyncpg://x@y/z\n"
        "  eu_west:\n"
        "    dsn: postgresql+asyncpg://a@b/c\n"
    )
    loaded = _load_config(cfg)
    assert set(loaded["pools"].keys()) == {"shared_default", "eu_west"}


def test_load_config_rejects_missing_pools(tmp_path: Path) -> None:
    cfg = tmp_path / "database.yaml"
    cfg.write_text("other_key: 1\n")
    with pytest.raises(ValueError, match="pools"):
        _load_config(cfg)


def test_default_registry_falls_back_to_settings_when_no_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No yaml file ⇒ shared_default seeded from `settings.database_url`."""
    from tagpulse.core import config as cfg_module

    monkeypatch.setattr(
        cfg_module.settings,
        "database_config_path",
        str(tmp_path / "missing.yaml"),
    )
    reg = _build_default_registry()
    assert "shared_default" in reg.keys()  # noqa: SIM118 — using keys() listing API


# ── tenant_context + db_session_var ────────────────────────────────────────


async def test_tenant_context_binds_and_resets() -> None:
    """`tenant_context` binds both contextvars and resets them on exit."""

    # Stub the registry with a sessionmaker that yields a fake session.
    class FakeSession:
        def __init__(self) -> None:
            self.executed: list[tuple[str, dict[str, object]]] = []
            self.committed = False
            self.rolled_back = False

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def execute(self, stmt: object, params: dict[str, object]) -> None:
            self.executed.append((str(stmt), params))

        async def commit(self) -> None:
            self.committed = True

        async def rollback(self) -> None:
            self.rolled_back = True

    fake = FakeSession()

    def fake_sessionmaker() -> FakeSession:
        return fake

    class FakeRegistry:
        def sessionmaker_for(self, key: str) -> object:
            assert key == "shared_default"
            return fake_sessionmaker

    set_pool_registry(FakeRegistry())  # type: ignore[arg-type]

    tid = uuid.uuid4()
    assert context.db_session_var.get() is None
    assert context.current_tenant_var.get() is None

    async with context.tenant_context(tid) as session:
        assert session is fake
        assert context.db_session_var.get() is fake
        assert context.current_tenant_var.get() == tid

    # Bindings reset after exit + commit happened, no rollback.
    assert context.db_session_var.get() is None
    assert context.current_tenant_var.get() is None
    assert fake.committed is True
    assert fake.rolled_back is False
    # First call must set the RLS GUC for the new tenant.
    assert any(
        "set_config" in stmt and params["tid"] == str(tid)
        for stmt, params in fake.executed
    )


async def test_tenant_context_rolls_back_on_exception() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.committed = False
            self.rolled_back = False

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def execute(self, *_: object, **__: object) -> None:
            return None

        async def commit(self) -> None:
            self.committed = True

        async def rollback(self) -> None:
            self.rolled_back = True

    fake = FakeSession()

    class FakeRegistry:
        def sessionmaker_for(self, key: str) -> object:
            return lambda: fake

    set_pool_registry(FakeRegistry())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="boom"):
        async with context.tenant_context(uuid.uuid4()):
            raise RuntimeError("boom")

    assert fake.rolled_back is True
    assert fake.committed is False
    # Bindings still cleared.
    assert context.db_session_var.get() is None
    assert context.current_tenant_var.get() is None


def test_get_bound_session_raises_outside_scope() -> None:
    with pytest.raises(RuntimeError, match="No async session bound"):
        context.get_bound_session()
