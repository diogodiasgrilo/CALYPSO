"""Session-wide test isolation.

Created 2026-09-17, after CI's first run failed a test that passes locally.

WHAT HAPPENED. ``gex_shared_cache._cache_dir()`` resolves to
``/opt/calypso/data/shared`` unless ``CALYPSO_GEX_CACHE_DIR`` overrides it, and
degrades to ``None`` (cache disabled) when that path cannot be created:

    macOS dev box   /opt/calypso absent -> mkdir fails -> None -> no cache
    Linux CI runner /opt/calypso creatable -> real cache directory exists
    the VM          /opt/calypso/data/shared IS THE LIVE CACHE

So on a Mac the suite silently ran with the cross-variant cache switched off,
while on Linux one test's saved profile leaked into another's — which is how
``test_failure_cooldown_60s`` came to assert ``is None`` and receive a real
``GEXProfile`` written moments earlier by a different test.

The operational half matters more than the flake: without this fixture, running
the suite ON THE VM would read and write the PRODUCTION GEX cache that variants
B and C share at entry time.

Redirecting the env var for every test fixes all three at once — and makes the
suite behave identically on every platform, which is the only way a green run on
one machine means anything on another.
"""

from __future__ import annotations

import pytest


#: Every module that bound ``is_running_on_gcp`` by name at import time. A
#: patch on the SOURCE does not reach these — the name was already copied into
#: each module's namespace — so all of them must be redirected individually.
_GCP_PROBE_SITES = (
    "shared.secret_manager",
    "shared",
    "shared.alert_service",
    "shared.claude_client",
    "shared.config_loader",
    "shared.logger_service",
    # Not a shared/ module, but it binds the name the same way. Found by
    # test_every_binding_site_is_listed, which scans the source rather than
    # trusting this list to be complete — it caught this omission immediately.
    "bots.hydra.main",
)


@pytest.fixture(autouse=True)
def _no_live_gcp_probe(monkeypatch):
    """Stop the test suite making a real network call to detect GCP.

    ``secret_manager.is_running_on_gcp()`` falls through to an actual HTTP GET
    against ``http://metadata.google.internal/...`` with a 1-second timeout.
    Three test files construct an ``AlertService`` without patching it, so the
    suite was issuing live network calls whose latency depends entirely on how
    the host resolves a name that does not exist — 7ms on this laptop, up to the
    full timeout elsewhere.

    That is a nondeterminism source in a suite that is supposed to be
    hermetic, and it is the leading suspect for two alert tests that failed on
    the CI runner and then passed on a re-run with no code change.

    Defaults to False (the honest answer for any machine running tests). A test
    that wants True still sets it explicitly; its ``monkeypatch.setattr`` runs
    after this fixture and wins.
    """
    import importlib

    for mod_name in _GCP_PROBE_SITES:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:      # noqa: BLE001 — a module absent on this branch
            continue
        if hasattr(mod, "is_running_on_gcp"):
            monkeypatch.setattr(mod, "is_running_on_gcp", lambda: False)


@pytest.fixture(autouse=True)
def _isolate_shared_gex_cache(tmp_path_factory, monkeypatch):
    """Point the cross-variant GEX cache at a per-test temp directory.

    Autouse and unconditional: a test that genuinely wants to exercise the cache
    sets its own ``CALYPSO_GEX_CACHE_DIR`` (as
    ``test_brandon_gex_shared_cache.py`` already does), and a later ``setenv``
    wins over this one.
    """
    monkeypatch.setenv(
        "CALYPSO_GEX_CACHE_DIR",
        str(tmp_path_factory.mktemp("gex_cache")),
    )
