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
        frozenset({"--tenant-id", "--api-key", "--units", "--seed-only"}),
    ),
    (
        "_step_simulate_assets",
        "simulate_assets.py",
        frozenset(
            {
                "--tenant-id",
                "--api-key",
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
