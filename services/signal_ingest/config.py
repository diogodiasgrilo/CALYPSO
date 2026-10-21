"""Config loading for the signal-ingest service. Fails closed on every default."""

from __future__ import annotations

import json
import os
from typing import Any, Dict

DEFAULTS: Dict[str, Any] = {
    # ⚠️ Every default here is the SAFE one. A missing or malformed config must
    # produce a service that refuses everything, never one that trades.
    "dry_run": True,
    "host": "127.0.0.1",
    "port": 8002,
    "shared_secret": "",        # blank => refuse everything (see gates.check_secret)
    "ledger_path": "data/signal_ingest/signals.db",
    "max_orders_per_day": 10,
    "symbols": {},              # empty => refuse everything (fail-closed)
}


def load(path: str = "services/signal_ingest/config.json") -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path) as f:
            cfg.update(json.load(f) or {})
    # The secret may also come from the environment so it need not sit in a file.
    env_secret = os.environ.get("SIGNAL_INGEST_SECRET", "").strip()
    if env_secret:
        cfg["shared_secret"] = env_secret
    return cfg


def assert_dry_run(cfg: Dict[str, Any]) -> None:
    """This build has no live path; refuse to pretend otherwise.

    `dry_run: false` does not enable trading here — nothing in this package can
    place an order. Starting with it set almost certainly means someone expected
    orders, so fail loudly at startup rather than let them believe it is armed.
    """
    if not cfg.get("dry_run", True):
        raise RuntimeError(
            "signal-ingest is DRY-RUN ONLY in this build: it imports no broker "
            "client and cannot place an order. Setting dry_run=false would be "
            "misleading, so it is refused. Going live is a deliberate code "
            "change (add a broker dependency + a reviewed mode), not a config flip."
        )
