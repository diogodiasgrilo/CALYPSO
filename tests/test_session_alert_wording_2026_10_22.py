"""The session alert told the operator the bot was restarting. It was not.

On 2026-10-21 09:30 ET the LIVE seat sent:

    "HYDRA — IBKR session re-probe failed (auto-restarting)"
    "Strategy ensure_connected() health-probe failed — exiting for an
     automatic systemd restart (back in ~30s)."

while the code on the very next line logged:

    "Broker session unavailable (morning re-auth gate) — pausing trading and
     waiting for the broker to self-heal (NOT exiting)."

It had not exited. PID 3589980 was unchanged, `systemctl show -p NRestarts`
read 0, and the session recovered 29 seconds later.

CAUSE: `on_ensure_connected_failed` had exactly TWO framings — strategy-exits
(`will_restart=True`, the DEFAULT) and broker-re-auth-failing
(`will_restart=False`) — and the broker-mode call sites passed neither, so they
inherited the wrong default. There was no wording for what the deployed
topology actually does: PAUSE and wait.

⚠️ Flipping those sites to `will_restart=False` would have been WORSE. That is
the broker's framing: HIGH priority, it EMAILS, and it asserts the shared
broker's re-auth has failed for 2+ consecutive cycles — a different and far
more alarming claim than "a probe blipped and we paused".

Third instance in this project of a message ASSERTING behaviour instead of
deriving it (see the 2026-10-07 stop-wording fix and the anti-spam gate's
"can never be flooded again"). The pattern: the message was written for the
behaviour of the day, the behaviour changed, the message did not.
"""

from __future__ import annotations

import os
import re
import sys
from unittest.mock import MagicMock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.alert_hooks import IBKRAlertHooks  # noqa: E402


def _hooks():
    alerts = MagicMock()
    return IBKRAlertHooks(MagicMock(), alerts), alerts


def _sent(alerts):
    assert alerts.send_alert.called, "no alert was sent"
    kw = alerts.send_alert.call_args.kwargs
    return f"{kw.get('title','')}\n{kw.get('message','')}", kw.get("priority")


class TestTheThreeFramings:
    def test_paused_says_it_PAUSED_and_never_claims_a_restart(self):
        h, alerts = _hooks()
        h.on_ensure_connected_failed(reason="morning daily re-auth gate", paused=True)
        text, prio = _sent(alerts)
        assert "paused" in text.lower()
        assert "not exit" in text.lower().replace("does not exit", "not exit")
        # The exact words that were wrong on 2026-10-21.
        assert "auto-restarting" not in text.lower()
        assert "systemd restart" not in text.lower()
        assert prio.name == "LOW", "a transient pause must not email"

    def test_legacy_exit_path_still_says_restarting(self):
        """Unchanged for the direct-IBClient fallback, which really does exit."""
        h, alerts = _hooks()
        h.on_ensure_connected_failed(reason="x")          # default will_restart=True
        text, prio = _sent(alerts)
        assert "restart" in text.lower() and prio.name == "LOW"

    def test_broker_framing_is_still_the_HIGH_emailing_one(self):
        h, alerts = _hooks()
        h.on_ensure_connected_failed(reason="x", will_restart=False)
        text, prio = _sent(alerts)
        assert "calypso-broker" in text.lower()
        assert prio.name == "HIGH", "broker re-auth failure must stay actionable"

    def test_the_three_framings_are_distinct(self):
        texts = []
        for kw in ({"paused": True}, {}, {"will_restart": False}):
            h, alerts = _hooks()
            h.on_ensure_connected_failed(reason="r", **kw)
            texts.append(_sent(alerts)[0])
        assert len(set(texts)) == 3, "two situations share wording"


class TestTheCallSitesUseIt:
    """The fix is only real if the BROKER-MODE sites pass `paused=True`.

    Source-level, deliberately: the alternative is driving main.py's monitoring
    loop, and this asserts the one thing that was wrong — which flag the
    pause-path call sites pass.
    """

    def test_broker_mode_sites_pass_paused(self):
        src = open(os.path.join(ROOT, "bots/hydra/main.py")).read()
        # Each broker-mode branch logs "NOT exiting" shortly after alerting.
        for block in re.findall(r"on_ensure_connected_failed\((?:[^()]|\([^()]*\))*\)"
                                r".{0,400}?NOT exiting", src, re.S):
            assert "paused=True" in block, (
                "a call site that pauses (logs 'NOT exiting') does not pass "
                f"paused=True:\n{block[:200]}")

    def test_at_least_two_such_sites_exist(self):
        """Guard: if the regex stops matching, the test above is vacuous."""
        src = open(os.path.join(ROOT, "bots/hydra/main.py")).read()
        blocks = re.findall(r"on_ensure_connected_failed\((?:[^()]|\([^()]*\))*\)"
                            r".{0,400}?NOT exiting", src, re.S)
        assert len(blocks) >= 2, f"expected >=2 pause call sites, found {len(blocks)}"
