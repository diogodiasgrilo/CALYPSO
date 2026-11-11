"""A publish WAIT timeout re-published and sent duplicate emails (2026-11-11).

`_publish` waited on the publish future with `future.result(timeout=5)`. That
raises `concurrent.futures.TimeoutError` — the builtin `TimeoutError` on 3.11 —
purely because OUR WAIT expired; the publish is still in flight and the future
cannot be cancelled (the library hard-overrides `cancel()` to return False,
established in the 2026-08-18 shutdown-hang review and still noted in the code).

So the retry called `publisher.publish()` a SECOND time, which does not replace
the first message — it adds one. Pub/Sub delivered both, and the alert Cloud
Function has no idempotency (and no shared state to build it on: the duplicates
landed on different instances), so two identical emails went out.

MEASURED, 2026-11-09 10:45 ET. The fleet logged TWO alert sends; the alert
function recorded THREE invocations at 15:45:11.712, 15:45:11.726 and
15:45:13.023 UTC with three DISTINCT `cloud_event_id`s — which RULES OUT a
Pub/Sub redelivery, since a redelivery reuses the id. `bl`'s own log named the
third:

    Pub/Sub publish attempt 1/2 failed (TimeoutError) — retrying
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shared.alert_service import AlertService, AlertPriority, AlertType


class _Future:
    """A publish future whose WAIT times out `n_timeouts` times, then succeeds."""
    def __init__(self, n_timeouts=0, mid="mid-1", fail_with=None):
        self.remaining = n_timeouts
        self.mid = mid
        self.fail_with = fail_with
        self.waits = 0

    def result(self, timeout=None):
        self.waits += 1
        if self.fail_with:
            raise self.fail_with
        if self.remaining > 0:
            self.remaining -= 1
            raise TimeoutError("our wait expired; the publish is still in flight")
        return self.mid

    def add_done_callback(self, fn):   # the library's Future has one
        pass


class _Publisher:
    """Counts publishes. A SECOND publish is the duplicate-email bug."""
    def __init__(self, futures):
        self._futures = list(futures)
        self.publish_calls = 0

    def publish(self, topic, data):
        self.publish_calls += 1
        i = min(self.publish_calls - 1, len(self._futures) - 1)
        return self._futures[i]


def _send(publisher, priority=AlertPriority.CRITICAL,
          alert_type=AlertType.CRITICAL_INTERVENTION):
    """Drive the REAL send_alert with a stubbed publisher."""
    svc = AlertService({"dry_run": False, "alerts": {"enabled": True}}, "TEST")
    svc._publisher = publisher
    svc._topic_path = "projects/p/topics/t"
    # send_alert gates on `_initialized` before it will use the publisher
    # (alert_service.py ~749), so a rig that only sets _publisher never reaches
    # the publish at all — which is how the first version of these tests
    # "failed" for the wrong reason.
    svc._initialized = True
    with patch.object(AlertService, "_write_dead_letter", lambda *a, **k: None), \
         patch.object(AlertService, "_attempt_telegram_bypass", lambda *a, **k: None), \
         patch("shared.alert_service.time.sleep", lambda *_: None):
        return svc.send_alert(alert_type, "T", "M", priority=priority)


# -------------------------------------------------------------- the defect

def test_a_wait_timeout_does_NOT_publish_a_second_message():
    """THE DEFECT, and the whole point of the fix."""
    fut = _Future(n_timeouts=1)
    pub = _Publisher([fut, _Future(0, "mid-DUPLICATE")])
    assert _send(pub) is True
    assert pub.publish_calls == 1, (
        f"published {pub.publish_calls}x — the 2nd message is the duplicate email")
    assert fut.waits == 2, "should have RE-WAITED on the same future"


def test_a_persistent_wait_timeout_still_only_publishes_once():
    fut = _Future(n_timeouts=99)
    pub = _Publisher([fut, _Future(0, "mid-DUPLICATE")])
    assert _send(pub) is False          # gives up, correctly
    assert pub.publish_calls == 1
    assert fut.waits == 2               # both attempts spent waiting, not publishing


def test_a_GENUINE_publish_error_DOES_start_a_fresh_publish():
    """The deliberately-unchanged half: nothing is in flight, so re-publish."""
    bad = _Future(0, fail_with=ValueError("topic gone"))
    good = _Future(0, "mid-2")
    pub = _Publisher([bad, good])
    assert _send(pub) is True
    assert pub.publish_calls == 2, "a real error must be retried with a NEW publish"


def test_the_happy_path_publishes_once_and_waits_once():
    fut = _Future(0, "mid-ok")
    pub = _Publisher([fut])
    assert _send(pub) is True
    assert (pub.publish_calls, fut.waits) == (1, 1)


def test_timeout_is_handled_BEFORE_the_generic_branch():
    """Ordering is load-bearing: TimeoutError must not fall into `except Exception`."""
    import inspect
    src = inspect.getsource(AlertService.send_alert)
    pub_at = src.index("future = publisher.publish")
    t = src.index("except TimeoutError", pub_at)
    g = src.index("except Exception as e:", pub_at)
    assert t < g, "except TimeoutError must precede except Exception"


def test_the_genuine_error_branch_resets_the_future():
    """Without the reset, a real error would re-wait on a dead future forever."""
    import inspect
    src = inspect.getsource(AlertService.send_alert)
    g = src.index("except Exception as e:", src.index("future = publisher.publish"))
    assert "future = None" in src[g:g + 400], (
        "the generic-error branch must clear `future` so a new publish happens")
