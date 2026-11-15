"""The alert Cloud Function's INFO logging never reached Cloud Logging.

THE DEFECT. `cloud_functions/alert_processor/main.py` configured logging with

    logging.basicConfig(level=logging.INFO)

and **`basicConfig` is documented to do nothing when the root logger already
has a handler.** Under `functions_framework` it always does — the framework
installs one before user code is imported — so the level was never set and
every INFO record was dropped against a root level of WARNING.

MEASURED 2026-11-15, not inferred: 60 days of Cloud Logging for the gen-2
service `process-trading-alert` contain ZERO "Processing alert" lines. That
record fires on EVERY invocation, and alerts were delivering daily throughout.

SCOPE OF THE BLINDNESS, stated precisely because the obvious reading is too
strong: ERROR and WARNING were NEVER suppressed — they clear a WARNING root
level. This was blindness to every SUCCESSFUL invocation, not to failures. It
is why the 2026-11-11 duplicate-email investigation could not confirm from the
function's own side that it had run twice and had to be settled from the
request log.

WHY THE TEST EXECUTES THE SOURCE. `functions_framework` is not installed in
this environment, so the module cannot be imported. Asserting on the source
TEXT would be a grep, and a grep cannot tell a working configuration from a
broken one — the broken version contained the string "logging.INFO" too. So
these tests extract the real configuration block from the real file and RUN
it, against a root logger pre-seeded exactly as the framework leaves it.
"""
from __future__ import annotations

import logging
import os
import re
import sys

import pytest

HERE = os.path.dirname(__file__)
MAIN = os.path.abspath(os.path.join(
    HERE, "..", "cloud_functions", "alert_processor", "main.py"))


def _logging_setup_source() -> str:
    """The real file's logging-configuration statements, as executable text."""
    src = open(MAIN).read().split("\n")
    start = next(i for i, line in enumerate(src)
                 if line.startswith("# Configure logging"))
    out = []
    for line in src[start:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            # Stop at the first blank line AFTER we have collected something,
            # so the comment block above the statements does not end the scan.
            if out and not stripped:
                break
            continue
        if not (stripped.startswith("logging") or stripped.startswith("logger")):
            break
        out.append(line)
    assert out, "could not locate the logging configuration in main.py"
    return "\n".join(out)


@pytest.fixture
def framework_like_root():
    """Root logger as functions_framework leaves it: a handler, level WARNING."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    victim = logging.getLogger("cloud_functions.alert_processor.main")
    saved_victim = victim.level
    root.handlers = [logging.StreamHandler()]
    root.setLevel(logging.WARNING)
    victim.setLevel(logging.NOTSET)
    try:
        yield victim
    finally:
        root.handlers, victim.level = saved_handlers, saved_victim
        root.setLevel(saved_level)


class TestTheMechanism:
    def test_basicConfig_alone_does_NOT_set_the_level(self, framework_like_root):
        """The negative control: the OLD line, reproduced, fails to take effect.

        Without this the suite could not distinguish the fix from the defect.
        """
        logging.basicConfig(level=logging.INFO)
        assert logging.getLogger().level == logging.WARNING, (
            "basicConfig unexpectedly took effect — if this ever passes, the "
            "premise of the fix is wrong, not the fix")
        assert not framework_like_root.isEnabledFor(logging.INFO)

    def test_errors_were_never_suppressed(self, framework_like_root):
        """Bounds the claim: failures were always visible. Only INFO was lost."""
        logging.basicConfig(level=logging.INFO)
        assert framework_like_root.isEnabledFor(logging.ERROR)
        assert framework_like_root.isEnabledFor(logging.WARNING)


class TestTheRealSourceFixesIt:
    def test_executing_the_real_config_enables_INFO(self, framework_like_root):
        """Run the actual file's configuration block. INFO must survive."""
        exec(compile(_logging_setup_source(), MAIN, "exec"),
             {"logging": logging, "__name__":
              "cloud_functions.alert_processor.main"})
        assert logging.getLogger().isEnabledFor(logging.INFO)
        assert framework_like_root.isEnabledFor(logging.INFO), (
            "the deployed function would still drop every INFO record")

    def test_it_also_works_when_root_has_NO_handler(self):
        """Local runs and unit tests take the other branch — basicConfig's."""
        root = logging.getLogger()
        saved_handlers, saved_level = root.handlers[:], root.level
        root.handlers = []
        root.setLevel(logging.WARNING)
        try:
            exec(compile(_logging_setup_source(), MAIN, "exec"),
                 {"logging": logging, "__name__": "x"})
            assert root.isEnabledFor(logging.INFO)
        finally:
            root.handlers, _ = saved_handlers, None
            root.setLevel(saved_level)

    def test_force_true_is_not_used(self):
        """`force=True` would tear out the platform's structured handler."""
        assert "force=True" not in _logging_setup_source()

    def test_the_root_level_is_set_explicitly(self):
        """The whole point: an explicit setLevel, not a hopeful basicConfig."""
        block = _logging_setup_source()
        assert re.search(r"logging\.getLogger\(\)\.setLevel\(", block), block


class TestTheInvocationRecordStillExists:
    """The fix is worthless if the record it unblocks was also removed."""

    def test_processing_alert_info_is_still_emitted_per_invocation(self):
        src = open(MAIN).read()
        assert 'logger.info(f"Processing alert:' in src
        assert 'logger.info(f"Alert processed:' in src

    def test_module_still_parses(self):
        import ast
        ast.parse(open(MAIN).read())
