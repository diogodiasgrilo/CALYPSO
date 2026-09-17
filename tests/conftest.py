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
