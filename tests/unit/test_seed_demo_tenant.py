"""Sprint 58 R1 mitigation — contract tests for ``scripts/seed_demo_tenant.py``.

The composer ``seed_demo_tenant.py`` invokes seven sibling scripts via
``subprocess.run`` with explicit CLI flags pinned per-call. If any of
those sibling scripts renames or drops a flag, the next ``make
demo-tenant`` run fails with a generic non-zero exit and the seed
silently doesn't ship — exactly the kind of drift §R1 of the design doc
calls out (see [Sprint 58 design doc](../../docs/design/sprint-58-demo-and-simulation.md)).

These tests are **hermetic** (no DB, no HTTP, no subprocess execution).
They parse each target script's ``argparse`` configuration via Python
AST and assert every ``--flag`` the composer passes is still registered
on the target's parser. That's the CLI-flag axis of the R1 risk. The
endpoint-URL axis (e.g. a script's hard-coded ``/tag-reads`` path
drifting to ``/v2/tag-reads``) is **not** covered here — that remains a
runtime risk only the live ``make demo-tenant`` exercise catches.

The two ``test_*_id_is_deterministic`` tests pin the uuid5 derivations
that gate idempotency (D2): if either changes, re-runs of the seed
would create duplicate tenants instead of converging onto the existing
row.

Located under ``tests/unit/`` (not ``tests/integration/`` as originally
sketched in R1) because the suite is pure-Python and ``make test`` only
picks up ``tests/unit``; co-locating with ``test_sim_loop.py`` keeps
the Sprint-58 composer tests together.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load_script_module(filename: str) -> ModuleType:
    """Load a top-level script under ``scripts/`` as an ad-hoc module.

    ``scripts/`` is not a package on the import path; this mirrors the
    pattern in ``tests/unit/test_sim_loop.py`` so we can read module-level
    constants without restructuring the script tree. Safe to call at
    test time because both target scripts gate all I/O behind functions
    and an ``if __name__ == '__main__'`` guard.
    """
    path = SCRIPTS_DIR / filename
    module_name = path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, (
        f"could not build importlib spec for {path}"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# (composer-step-function-name, target-script-filename, flags-the-composer-passes).
# Keep in lock-step with the ``_step_*`` helpers in ``scripts/seed_demo_tenant.py``.
# Flags listed here are the union the composer may pass; the test asserts each
# one is registered on the target's argparse parser. Conditional flags (e.g.
# ``--regenerate-key`` only when not DEMO_KEEP_KEY) are still listed because the
# target must accept them on the path that uses them.
_COMPOSER_INVOCATIONS: list[tuple[str, str, frozenset[str]]] = [
    (
        "_step_smoke_setup",
        "smoke_setup.py",
        frozenset(
            {
                "--full",
                "--regenerate-key",
                "--print-full-key",
                "--tenant-id",
                "--tenant-slug",
                "--tenant-name",
                "--admin-email",
                "--admin-name",
                # Added in chore/demo-tenant-as-job: composer forwards
                # $TAGPULSE_SMOKE_KEY_VAULT_NAME through to smoke_setup
                # when running inside the tools-job so the rotated key
                # lands in KV instead of Log Analytics.
                "--key-vault-name",
            }
        ),
    ),
    (
        "_step_simulate_devices",
        "simulate_devices.py",
        frozenset({"--tenant-id", "--api-key", "--devices", "--tags", "--seed-only"}),
    ),
    (
        "_step_simulate_inventory",
        "simulate_inventory.py",
        frozenset({"--tenant-id", "--api-key", "--scenario", "--units", "--seed-only"}),
    ),
    (
        "_step_simulate_assets",
        "simulate_assets.py",
        frozenset(
            {
                "--tenant-id",
                "--api-key",
                "--scenario",
                "--assets",
                "--readers",
                "--iterations",
                "--interval",
            }
        ),
    ),
    (
        "_step_backfill_history",
        "backfill_history.py",
        frozenset({"--tenant-id", "--api-key", "--days", "--reads", "--batch-size"}),
    ),
    (
        "_step_seed_alerts",
        "seed_alerts.py",
        frozenset({"--tenant-id", "--api-key", "--natural-count", "--resolved-count"}),
    ),
    (
        "_step_seed_transfer",
        "seed_transfer.py",
        frozenset({"--tenant-id", "--api-key", "--epc-count"}),
    ),
    (
        "_step_seed_ui_config",
        "seed_ui_config.py",
        frozenset({"--tenant-id", "--api-key"}),
    ),
    (
        "_step_seed_branding",
        "seed_branding.py",
        frozenset({"--tenant-id", "--api-key"}),
    ),
    (
        "_step_seed_register_tags",
        "seed_register_tags.py",
        frozenset({"--tenant-id", "--api-key", "--scenario"}),
    ),
]


def _extract_add_argument_flags(source: str) -> set[str]:
    """Return every ``"--flag"`` string passed as the first positional arg
    to a ``parser.add_argument(...)`` call in ``source``.

    Uses AST (no execution) so importing scripts with module-level side
    effects (HTTP base-URL probes, environment defaulting) is avoided.
    Short options (``-x``) are filtered out — the composer only uses the
    long form.
    """
    tree = ast.parse(source)
    flags: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match ``<anything>.add_argument(...)`` — the receiver is usually
        # ``parser`` but some scripts use sub-parsers or alternate names.
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            value = first.value
            if value.startswith("--"):
                flags.add(value)
    return flags


def test_demo_tenant_id_is_deterministic() -> None:
    """``DEMO_TENANT_ID`` is the uuid5 of the documented slug.

    Pinning this protects D2's idempotency contract: re-running
    ``make demo-tenant`` must converge to the same tenant row, never
    create a duplicate. A drift here means migrations / fixtures that
    hard-code the UUID also break.
    """
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")

    expected = uuid.uuid5(uuid.NAMESPACE_DNS, "demo-wm-dc.tagpulse.local")
    assert expected == seed_demo_tenant.DEMO_TENANT_ID
    assert seed_demo_tenant.DEMO_TENANT_SLUG == "demo-wm-dc"


def test_recipient_tenant_id_is_deterministic() -> None:
    """``DEFAULT_RECIPIENT_ID`` is the uuid5 of the documented slug.

    Same rationale as the source tenant — the transfer seed is the only
    other deterministic tenant the demo bundle materialises.
    """
    seed_transfer = _load_script_module("seed_transfer.py")

    expected = uuid.uuid5(uuid.NAMESPACE_DNS, "demo-wm-recipient.tagpulse.local")
    assert expected == seed_transfer.DEFAULT_RECIPIENT_ID
    assert seed_transfer.DEFAULT_RECIPIENT_SLUG == "demo-wm-recipient"


@pytest.mark.parametrize(
    ("step_name", "target_filename", "_expected_flags"),
    _COMPOSER_INVOCATIONS,
    ids=[step for step, _, _ in _COMPOSER_INVOCATIONS],
)
def test_composer_script_targets_exist(
    step_name: str,  # noqa: ARG001 — used as test id
    target_filename: str,
    _expected_flags: frozenset[str],  # noqa: ARG001 — unused in this test
) -> None:
    """Each subprocess target the composer invokes is a real file on disk."""
    target = SCRIPTS_DIR / target_filename
    assert target.is_file(), f"composer step targets missing script: {target}"


@pytest.mark.parametrize(
    ("step_name", "target_filename", "expected_flags"),
    _COMPOSER_INVOCATIONS,
    ids=[step for step, _, _ in _COMPOSER_INVOCATIONS],
)
def test_composer_subprocess_args_match_target_cli(
    step_name: str,  # noqa: ARG001 — used as test id
    target_filename: str,
    expected_flags: frozenset[str],
) -> None:
    """Every ``--flag`` the composer passes is still registered by the target.

    R1 of the design doc: if a sibling script drops or renames a flag,
    ``make demo-tenant`` should fail fast in CI rather than at first
    operator run. AST-based extraction so this stays hermetic.
    """
    target = SCRIPTS_DIR / target_filename
    source = target.read_text()
    declared = _extract_add_argument_flags(source)
    missing = expected_flags - declared
    assert not missing, (
        f"{target_filename} no longer declares argparse flags the composer "
        f"passes: {sorted(missing)} — either restore them on the target or "
        f"update _COMPOSER_INVOCATIONS in {Path(__file__).name}."
    )


# ---------------------------------------------------------------------------
# chore/demo-tenant-as-job — in-cluster execution guards & key handoff.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_value", ["prod", "production", "PROD", "Production"])
def test_assert_environment_safe_refuses_prod(
    env_value: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The composer must never run against a prod-shaped ENVIRONMENT.

    Case-insensitive, both 'prod' and 'production' variants. The demo
    seed rotates an admin API key and mutates a deterministic tenant
    slug, neither of which is safe in prod under any circumstance.
    """
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")
    monkeypatch.setenv("ENVIRONMENT", env_value)

    with pytest.raises(SystemExit) as excinfo:
        seed_demo_tenant._assert_environment_safe()

    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "refusing to run" in err
    assert env_value.lower() in err


@pytest.mark.parametrize(
    ("env_value", "expected_mode"),
    [
        ("dev", "dev"),
        ("staging", "staging"),
        ("DEV", "dev"),  # case-insensitive
        ("", "local"),  # empty
    ],
)
def test_assert_environment_safe_accepts_non_prod(
    env_value: str,
    expected_mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-prod ENVIRONMENT values are accepted and returned normalized."""
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")
    monkeypatch.setenv("ENVIRONMENT", env_value)

    assert seed_demo_tenant._assert_environment_safe() == expected_mode


def test_assert_environment_safe_treats_unset_as_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset $ENVIRONMENT (laptop dev path) resolves to the 'local' sentinel."""
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    assert seed_demo_tenant._assert_environment_safe() == "local"


def test_demo_admin_kv_secret_name_matches_smoke_setup_format() -> None:
    """Composer's hard-coded KV secret name must equal what smoke_setup writes.

    The composer reads the rotated admin key back from KV by deriving
    the name as ``tagpulse-<slug>-admin-key`` (per
    ``smoke_setup._kv_secret_name``). If smoke_setup ever changes that
    format, this test catches the drift before the in-cluster
    composer runs and silently can't find the secret.
    """
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")
    smoke_setup = _load_script_module("smoke_setup.py")

    derived = smoke_setup._kv_secret_name(seed_demo_tenant.DEMO_TENANT_SLUG, "admin")
    assert derived == seed_demo_tenant.DEMO_ADMIN_KV_SECRET_NAME
    assert derived == f"tagpulse-{seed_demo_tenant.DEMO_TENANT_SLUG}-admin-key"


def test_in_cluster_default_days_is_one_local_default_is_three() -> None:
    """Backfill --days default is mode-dependent.

    Local: 3 days because INGEST_CLOCK_ENFORCE is typically false. The
    dashboard's history view looks empty with less.
    In-cluster: 1 day because the deployed API enforces a 24 h
    MAX_PAST window (see src/tagpulse/ingestion/clock.py); a wider
    replay silently dead-letters most of the writes.
    """
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")

    assert seed_demo_tenant._DEFAULT_DAYS_LOCAL == 3.0
    assert seed_demo_tenant._DEFAULT_DAYS_INCLUSTER == 1.0


# ---------------------------------------------------------------------------
# Sprint 59 Phase B — per-domain demo profiles.
# The composer now drives three tenants from one script via ``--profile``.
# ``combined`` MUST stay byte-for-byte identical to the Sprint 58 build; the
# two new profiles each toggle off the steps that belong to the other domain.
# ---------------------------------------------------------------------------


def test_combined_profile_matches_legacy_constants() -> None:
    """The ``combined`` profile reproduces the frozen Sprint 58 identity.

    Its slug/uuid/admin-email/KV-secret-name must equal the module-level
    ``DEMO_*`` constants verbatim — that's the contract that guarantees
    ``make demo-tenant`` (which defaults to ``--profile combined``) keeps
    converging onto the existing ``demo-wm-dc`` tenant rather than spawning
    a new one.
    """
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")
    combined = seed_demo_tenant.PROFILES["combined"]

    assert seed_demo_tenant.DEFAULT_PROFILE == "combined"
    assert combined.slug == seed_demo_tenant.DEMO_TENANT_SLUG == "demo-wm-dc"
    assert combined.name == seed_demo_tenant.DEMO_TENANT_NAME
    assert combined.tenant_id == seed_demo_tenant.DEMO_TENANT_ID
    assert combined.admin_email == seed_demo_tenant.DEMO_ADMIN_EMAIL
    assert combined.admin_name == seed_demo_tenant.DEMO_ADMIN_NAME
    assert combined.admin_kv_secret_name == seed_demo_tenant.DEMO_ADMIN_KV_SECRET_NAME
    # All six seed steps run for the combined build.
    assert combined.seed_devices
    assert combined.seed_inventory
    assert combined.seed_assets
    assert combined.seed_backfill
    assert combined.seed_alerts
    assert combined.seed_transfer
    # Sprint 60: the WM-facing tenant applies the Device -> Reader label skin.
    assert combined.seed_ui_config


def test_profile_ids_are_deterministic_and_distinct() -> None:
    """Each profile's tenant_id is uuid5 of its slug, and all three differ.

    Same idempotency contract as Sprint 58 D2: re-running a profile must
    converge onto its own tenant row, and the three demo tenants must never
    collide.
    """
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")
    profiles = seed_demo_tenant.PROFILES

    assert set(profiles) == {"combined", "inventory", "asset"}

    expected_slugs = {
        "combined": "demo-wm-dc",
        "inventory": "demo-inv-coldchain",
        "asset": "demo-asset-fleet",
    }
    ids = set()
    for key, profile in profiles.items():
        assert profile.key == key
        assert profile.slug == expected_slugs[key]
        assert profile.tenant_id == uuid.uuid5(uuid.NAMESPACE_DNS, f"{profile.slug}.tagpulse.local")
        assert profile.admin_kv_secret_name == f"tagpulse-{profile.slug}-admin-key"
        ids.add(profile.tenant_id)

    assert len(ids) == 3, "demo profiles must have distinct tenant UUIDs"


def test_domain_profiles_toggle_off_the_other_domain() -> None:
    """Inventory drops assets+transfer; asset drops inventory.

    The toggles are what make each domain tenant tell *one* complete story
    instead of the combined generalist. Devices/backfill/alerts stay on for
    both so the dashboards animate.
    """
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")
    inventory = seed_demo_tenant.PROFILES["inventory"]
    asset = seed_demo_tenant.PROFILES["asset"]

    # Inventory story: no asset roster, no cross-tenant transfer.
    assert inventory.seed_inventory
    assert not inventory.seed_assets
    assert not inventory.seed_transfer
    assert inventory.seed_devices
    assert inventory.seed_backfill
    assert inventory.seed_alerts

    # Asset story: no inventory catalog.
    assert asset.seed_assets
    assert asset.seed_transfer
    assert not asset.seed_inventory
    assert asset.seed_devices
    assert asset.seed_backfill
    assert asset.seed_alerts


def test_only_combined_profile_applies_wm_ui_config() -> None:
    """The WM label skin (Sprint 60, ADR-032) is applied to the WM-facing
    ``combined`` tenant only; the neutral domain demos stay on system defaults.

    Pins the per-tenant scoping decision: ``Device`` -> ``Reader`` is a
    WM-specific simplification, not a platform-wide default, so it must not
    leak onto the cold-chain / asset-fleet demo tenants.
    """
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")
    profiles = seed_demo_tenant.PROFILES

    assert profiles["combined"].seed_ui_config
    assert not profiles["inventory"].seed_ui_config
    assert not profiles["asset"].seed_ui_config


def test_only_combined_profile_applies_branding() -> None:
    """The SuperMart logo kit (branding-logo-upload chore) is applied to the
    WM-facing ``combined`` tenant only; the neutral domain demos keep the bare
    display-name fallback so they tell their own unbranded story.
    """
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")
    profiles = seed_demo_tenant.PROFILES

    assert profiles["combined"].seed_branding
    assert not profiles["inventory"].seed_branding
    assert not profiles["asset"].seed_branding


def test_register_tags_runs_for_inventory_seeding_profiles() -> None:
    """The tags-registry registration step runs wherever inventory is seeded.

    The demo Tags KPI counts ``tags`` rows; the inventory simulator streams
    SGTIN reads but never registers their EPCs, so the step must run for every
    inventory-seeding profile (``combined`` + ``inventory``) and be skipped for
    the asset-only profile (which seeds no inventory units).
    """
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")
    profiles = seed_demo_tenant.PROFILES

    assert profiles["combined"].seed_inventory
    assert profiles["inventory"].seed_inventory
    assert not profiles["asset"].seed_inventory


def test_seed_register_tags_matches_inventory_serial_scheme() -> None:
    """The shim derives the exact EPC set the inventory simulator streams.

    The registered set must equal ``_build_units``' serial scheme
    (``(product_idx+1) * 100_000 + unit_idx``) so the Tags KPI reflects the
    real fleet and no EPC is over- or under-registered. EPCs must be uppercase
    hex (``TagCreate.epc_hex`` pattern).
    """
    seed_register_tags = _load_script_module("seed_register_tags.py")
    simulate_inventory = _load_script_module("simulate_inventory.py")

    for scenario_name, scenario in simulate_inventory.SCENARIOS.items():
        expected = {
            simulate_inventory._sgtin96_hex(
                simulate_inventory.COMPANY_PREFIX,
                item.item_ref,
                (product_idx + 1) * 100_000 + unit_idx,
            ).upper()
            for product_idx, item in enumerate(scenario.catalog)
            for unit_idx in range(item.units)
        }
        actual = set(seed_register_tags.scenario_epc_hexes(scenario_name))
        assert actual == expected, scenario_name
        assert all(e == e.upper() for e in actual)
        # Count parity: one tag per seeded unit.
        total_units = sum(item.units for item in scenario.catalog)
        assert len(actual) == total_units


def test_seed_register_tags_posts_each_epc() -> None:
    """The shim POSTs every EPC to ``/tags`` with ``source='backfill'`` and the
    right auth headers, tolerating 409 (already-registered) idempotently.
    """
    seed_register_tags = _load_script_module("seed_register_tags.py")

    posted: list[dict[str, object]] = []

    class _Resp:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.text = ""

    class _Client:
        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict) -> _Resp:
            posted.append({"url": url, "headers": headers, "json": json})
            # First half new (201), second half already-exist (409).
            return _Resp(201 if len(posted) % 2 else 409)

    seed_register_tags.httpx.Client = lambda *a, **k: _Client()  # type: ignore[assignment]
    seed_register_tags.time.sleep = lambda _s: None  # type: ignore[assignment]

    created, existing, failed = seed_register_tags.register_tags(
        "tid-abc", "tp_demo_key", "baseline"
    )

    expected = seed_register_tags.scenario_epc_hexes("baseline")
    assert len(posted) == len(expected)
    assert {p["json"]["epc_hex"] for p in posted} == set(expected)
    assert all(p["json"]["source"] == "backfill" for p in posted)
    assert all(p["url"].endswith("/tags") for p in posted)
    assert all(p["headers"]["Authorization"] == "Bearer tp_demo_key" for p in posted)
    assert all(p["headers"]["X-Tenant-ID"] == "tid-abc" for p in posted)
    assert failed == 0
    assert created + existing == len(expected)


def test_seed_ui_config_applies_canonical_wm_presentation() -> None:
    """The shim PUTs the *canonical* ``WM_DEMO_PRESENTATION`` (imported, not
    hardcoded) so the demo can never drift from the backend registry, and
    verifies the headline label skin resolved in the response before returning.
    """
    seed_ui_config = _load_script_module("seed_ui_config.py")
    from tagpulse.services.ui_config import WM_DEMO_PRESENTATION, WM_LABEL_SKIN

    assert seed_ui_config.WM_LABEL_SKIN == WM_LABEL_SKIN == {"device": "Reader"}
    # The presentation carries every consumed leaf. The Sprint 61 `nav` leaf
    # hides Inventory (presentation hide) and orders the entity-first sections;
    # Data Management stays visible and the Tags card stays shown.
    assert seed_ui_config.WM_DEMO_PRESENTATION == WM_DEMO_PRESENTATION
    assert set(WM_DEMO_PRESENTATION) == {"labels", "nav", "cards", "theme", "columns", "tables"}
    assert WM_DEMO_PRESENTATION["labels"] == {"device": "Reader"}
    assert WM_DEMO_PRESENTATION["nav"]["hidden"] == ["sec-inventory"]
    assert "sec-data-management" not in WM_DEMO_PRESENTATION["nav"]["hidden"]
    assert WM_DEMO_PRESENTATION["nav"]["order"][0] == "sec-assets"
    assert "tags" not in WM_DEMO_PRESENTATION["cards"]["dashboard"]["hidden"]

    captured: dict[str, object] = {}

    def _fake_put(url: str, *, headers: dict[str, str], json: dict, timeout: float):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers

        class _Resp:
            status_code = 200

            @staticmethod
            def json() -> dict:
                # The resolved document echoes the pushed leaves (label skin +
                # the hidden-card list).
                return {
                    "labels": {"device": "Reader", "asset": "Asset"},
                    "cards": {"dashboard": {"hidden": ["reads-per-hour"]}},
                }

        return _Resp()

    seed_ui_config.httpx.put = _fake_put  # type: ignore[assignment]
    resolved = seed_ui_config.apply_wm_presentation("tid-123", "tp_demo_key")

    assert captured["url"].endswith("/ui-config/tenant")
    # The full presentation is PUT, not just labels.
    assert captured["json"] == WM_DEMO_PRESENTATION
    assert captured["headers"]["Authorization"] == "Bearer tp_demo_key"
    assert captured["headers"]["X-Tenant-ID"] == "tid-123"
    assert resolved["labels"]["device"] == "Reader"


def test_seed_branding_assets_are_bundled() -> None:
    """The SuperMart logo kit ships in-repo so the demo travels self-contained.

    Both default asset files must exist and be non-trivial PNGs; the data-URL
    encoding must stay under the backend's 96 KiB ``data:`` cap so the PATCH is
    accepted (see ``tenant_branding._MAX_LOGO_DATA_URL_LEN``).
    """
    seed_branding = _load_script_module("seed_branding.py")

    for path in (seed_branding.DEFAULT_FULL_LOGO, seed_branding.DEFAULT_COLLAPSED_LOGO):
        assert path.is_file(), f"bundled logo missing: {path}"
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"not a PNG: {path}"
        data_url = seed_branding._data_url(path)
        assert data_url.startswith("data:image/png;base64,")
        assert len(data_url) < 96 * 1024, f"{path} data-URL exceeds 96 KiB cap"


def test_seed_branding_patches_logo_kit() -> None:
    """The shim PATCHes both logos + the teal accent and verifies both logos
    resolved in the response before returning, failing fast otherwise.
    """
    seed_branding = _load_script_module("seed_branding.py")

    captured: dict[str, object] = {}

    def _fake_patch(url: str, *, headers: dict[str, str], json: dict, timeout: float):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers

        class _Resp:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {
                    "logo_url": json["logo_url"],
                    "logo_collapsed_url": json["logo_collapsed_url"],
                    "brand_color": json["brand_color"],
                }

        return _Resp()

    seed_branding.httpx.patch = _fake_patch  # type: ignore[assignment]
    resolved = seed_branding.apply_branding(
        "tid-xyz",
        "tp_demo_key",
        full_logo=seed_branding.DEFAULT_FULL_LOGO,
        collapsed_logo=seed_branding.DEFAULT_COLLAPSED_LOGO,
        brand_color=seed_branding.DEMO_BRAND_COLOR,
    )

    assert captured["url"].endswith("/tenant/branding")
    body = captured["json"]
    assert body["logo_url"].startswith("data:image/png;base64,")
    assert body["logo_collapsed_url"].startswith("data:image/png;base64,")
    assert body["brand_color"] == "#14B8A6"
    assert captured["headers"]["Authorization"] == "Bearer tp_demo_key"
    assert captured["headers"]["X-Tenant-ID"] == "tid-xyz"
    assert resolved["logo_url"] and resolved["logo_collapsed_url"]


def test_reset_known_slugs_cover_all_profiles() -> None:
    """``reset_demo_tenant`` can target every profile slug the composer seeds.

    Guards the per-tenant reset targets (``make demo-inventory-reset`` /
    ``demo-asset-reset``): if a profile slug is added to the composer but not
    to the reset script's ``--slug`` choices, the operator can seed a tenant
    they can't tear down.
    """
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")
    reset_demo_tenant = _load_script_module("reset_demo_tenant.py")

    composer_slugs = {p.slug for p in seed_demo_tenant.PROFILES.values()}
    reset_slugs = set(reset_demo_tenant.KNOWN_DEMO_SLUGS)

    assert composer_slugs <= reset_slugs, (
        "reset_demo_tenant.KNOWN_DEMO_SLUGS is missing composer profile slugs: "
        f"{sorted(composer_slugs - reset_slugs)}"
    )


# ---------------------------------------------------------------------------
# Sprint 59 Phase C — scenario depth.
# Each domain profile now selects a richer simulator preset via ``--scenario``.
# The ``baseline`` preset in each simulator MUST reproduce the legacy
# (Sprint 58) module constants so the combined tenant stays byte-for-byte.
# ---------------------------------------------------------------------------


def test_profile_scenarios_are_registered_in_their_simulators() -> None:
    """Every profile's scenario name resolves to a real preset.

    Catches drift where a profile points ``inventory_scenario`` /
    ``asset_scenario`` at a preset key that the target simulator's
    ``SCENARIOS`` dict doesn't define — which would crash the composer
    mid-run with an argparse ``choices`` error.
    """
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")
    simulate_inventory = _load_script_module("simulate_inventory.py")
    simulate_assets = _load_script_module("simulate_assets.py")

    for profile in seed_demo_tenant.PROFILES.values():
        assert profile.inventory_scenario in simulate_inventory.SCENARIOS, (
            f"profile {profile.key!r} inventory_scenario "
            f"{profile.inventory_scenario!r} not in simulate_inventory.SCENARIOS"
        )
        assert profile.asset_scenario in simulate_assets.SCENARIOS, (
            f"profile {profile.key!r} asset_scenario "
            f"{profile.asset_scenario!r} not in simulate_assets.SCENARIOS"
        )


def test_combined_profile_uses_baseline_scenarios() -> None:
    """The combined tenant must run both simulators in their baseline preset.

    This is the scenario-depth half of the byte-for-byte contract: the
    domain profiles get the richer presets, but ``combined`` stays on
    ``baseline`` so ``make demo-tenant`` reproduces the Sprint 58 build.
    """
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")
    combined = seed_demo_tenant.PROFILES["combined"]

    assert combined.inventory_scenario == "baseline"
    assert combined.asset_scenario == "baseline"


def test_domain_profiles_select_deep_scenarios() -> None:
    """Inventory selects coldchain catalog depth; asset selects the fleet roster."""
    seed_demo_tenant = _load_script_module("seed_demo_tenant.py")
    profiles = seed_demo_tenant.PROFILES

    assert profiles["inventory"].inventory_scenario == "coldchain"
    assert profiles["asset"].asset_scenario == "fleet"


def test_inventory_baseline_scenario_matches_legacy_constants() -> None:
    """``simulate_inventory`` baseline preset reuses the legacy module objects.

    The combined tenant seeds inventory with ``--scenario baseline``; if that
    preset ever diverged from the original ``CATALOG`` / ``ZONE_PIPELINE`` /
    ``SITE_NAME`` constants, the combined build would silently change shape.
    """
    simulate_inventory = _load_script_module("simulate_inventory.py")
    baseline = simulate_inventory.SCENARIOS["baseline"]

    assert baseline.catalog is simulate_inventory.CATALOG
    assert baseline.zone_pipeline is simulate_inventory.ZONE_PIPELINE
    assert baseline.site_name == simulate_inventory.SITE_NAME
    # Baseline has no quarantine divert (that's a coldchain-only feature).
    assert baseline.quarantine_zone is None


def test_assets_baseline_scenario_is_not_topology() -> None:
    """``simulate_assets`` baseline preset keeps the legacy Sim-Pallet flow.

    ``is_topology`` False routes ``main()`` down the unchanged Sprint 15
    fetch_devices/ensure_assets/ensure_bindings/emit path the combined tenant
    relies on; the ``fleet`` preset is the topology-driven one.
    """
    simulate_assets = _load_script_module("simulate_assets.py")

    assert simulate_assets.SCENARIOS["baseline"].is_topology is False
    assert simulate_assets.SCENARIOS["fleet"].is_topology is True
