"""A placeholder alert email silently bounces every CRITICAL from that variant.

On 2026-11-09 variant C emailed a CRITICAL "Trading HALTED" and it bounced
with "Address not found": its config still carried `your@email.com`. Six of
ten configs did — c, d, e, f, g, h.

The trap is that fixing the VM is not enough. `config_variant_*.json` are
TRACKED, and CLAUDE.md records (empirically verified) that skip-worktree does
NOT stop a `git pull` fast-forward from overwriting them. A VM-only fix would
have been reverted by the next deploy that touched these paths, silently.

So this guards the SOURCE. CRITICAL bypasses `alerts.enabled=false` by design
— that is what keeps a naked short unsilenceable — which means every variant
can email regardless of its enabled flag, and therefore every variant needs a
deliverable address.
"""

from __future__ import annotations

import json
import pathlib

import pytest

CONFIGS = sorted((pathlib.Path(__file__).resolve().parent.parent
                  / "bots" / "hydra" / "config").glob("config_variant_*.json"))

PLACEHOLDERS = ("your@", "example.com", "changeme", "foo@", "test@test")


def test_there_are_configs_to_check():
    """Guards against the glob silently matching nothing."""
    assert len(CONFIGS) >= 5, f"only found {len(CONFIGS)} variant configs"


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.name)
def test_no_placeholder_alert_email(path):
    email = (json.loads(path.read_text()).get("alerts") or {}).get("email")
    if email is None:
        return                      # absent is fine; a WRONG one is not
    low = email.lower()
    assert not any(p in low for p in PLACEHOLDERS), (
        f"{path.name} carries a placeholder alert email ({email!r}). Every "
        f"CRITICAL from this variant will bounce — CRITICAL bypasses "
        f"alerts.enabled=false by design, so a disabled variant still emails.")
    assert "@" in low and "." in low.split("@")[-1], f"{email!r} is not an address"
