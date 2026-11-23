"""An emergency-stop command must name the LIVE SEAT, derived — never a literal.

On 2026-11-22 `docs/migration/RUNBOOKS.md` named `hydra_variant_b` as "the live
seat" in EIGHT separate "stop the bot" instructions, including the top-level
*"Stop ALL trading, paper and real money, right now."* The live paper seat moved
to `bl` on 2026-10-09, so every one of those commands stopped the DRY-RUN control
and left the live seat trading — in the document you reach for precisely when you
are unsure what is wrong.

**That was the FOURTH recurrence of one defect**: a fleet list or a stop command
written against the seat that was live when the line was authored.

  1. 2026-09-19  `systemctl stop hydra` — `hydra` is variant A, a dry-run shadow
  2. `ab63407`   the GCS DB backup covered a shadow and not the live seat
  3. 2026-11-16  the GATING nightly audit omitted the live seat entirely
  4. 2026-11-22  these eight runbook stop commands

Fixing the fourth without a gate buys a fifth, so this file is the gate. It
asserts the property that actually matters — *the command stops what trades* —
and it derives the seat from `shared.strategy_taxonomy`, the single source of
truth, rather than hard-coding `"bl"`. A test pinned to the literal would itself
go stale at the next swap and is exactly the bug it is meant to catch.

Scope note: this checks **documentation**, because that is where the defect was
and where nothing else could catch it. The code paths already follow the seat
dynamically via `live_seat_id()`.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

RUNBOOKS = "docs/migration/RUNBOOKS.md"
CLAUDE_MD = "CLAUDE.md"

# A `systemctl stop ...` invocation and everything it stops, up to the closing quote.
_STOP_RE = re.compile(r"systemctl stop ([a-z0-9_ \-]+)")


def _live_seat() -> str:
    """The live PAPER seat id, derived from the taxonomy."""
    from shared.strategy_taxonomy import STRATEGIES

    live = [k for k, v in STRATEGIES.items() if getattr(v, "status", "") == "live"]
    assert len(live) == 1, f"expected exactly one live-status strategy, got {live}"
    return live[0]


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _stop_commands(text: str) -> list[str]:
    """Every `systemctl stop` unit-list in the document."""
    return [m.group(1).strip() for m in _STOP_RE.finditer(text)]


def _units(cmd: str) -> set[str]:
    return {u for u in cmd.split() if u.startswith("hydra") or u.startswith("calypso")}


class TestTheLiveSeatIsDerivedNotAssumed:
    def test_the_taxonomy_names_exactly_one_live_seat(self):
        """Everything here rests on this being unambiguous."""
        assert _live_seat()

    def test_the_live_seat_is_a_paper_strategy(self):
        """A `status="live"` that was real money would make every assertion below
        dangerous rather than merely wrong."""
        from shared.strategy_taxonomy import PAPER, STRATEGIES

        assert getattr(STRATEGIES[_live_seat()], "account_kind", None) == PAPER


class TestTheEmergencyStopStopsWhatTrades:
    def test_the_fleetwide_stop_includes_the_live_seat(self):
        """THE REGRESSION. The top-level "stop ALL trading" command must contain
        the live seat's unit, whichever variant currently holds it."""
        seat_unit = f"hydra_variant_{_live_seat()}"
        text = _read(RUNBOOKS)
        # A fleet-wide stop is one that takes down MORE THAN the real-money seat.
        # `systemctl stop hydra_variant_bm` alone is a deliberate narrow action
        # ("stop real money, leave paper as the control") and must not be caught
        # here — selecting on the bm substring alone flagged it, which is how this
        # test first failed.
        fleetwide = [c for c in _stop_commands(text)
                     if "hydra_variant_bm" in _units(c) and len(_units(c)) >= 2]
        assert fleetwide, "no fleet-wide stop command found in RUNBOOKS"
        for cmd in fleetwide:
            assert seat_unit in _units(cmd), (
                f"a 'stop ALL trading' command omits the live seat {seat_unit!r}: "
                f"{cmd!r} — it would stop a shadow and leave the live seat trading"
            )

    def test_no_stop_command_names_a_retired_seat_as_the_only_target(self):
        """A single-unit stop that names a NON-live variant is the defect: it
        reads as 'stop trading' and stops nothing that trades."""
        seat = _live_seat()
        seat_unit = f"hydra_variant_{seat}"
        offenders = []
        for cmd in _stop_commands(_read(RUNBOOKS)):
            units = _units(cmd)
            variant_units = {u for u in units if u.startswith("hydra_variant_")}
            if not variant_units:
                continue                      # e.g. `systemctl stop hydra` in a quoted warning
            if seat_unit in units:
                continue                      # stops the live seat — fine
            # bm is the real-money seat; a bm-only stop is a legitimate narrow action
            if variant_units <= {"hydra_variant_bm"}:
                continue
            offenders.append(cmd)
        assert not offenders, (
            f"stop command(s) target a non-live variant and not {seat_unit!r}: {offenders}"
        )

    def test_claude_md_emergency_stop_includes_the_live_seat(self):
        """CLAUDE.md carries its own emergency stop; it drifted the same way in
        2026-09-19 (D/E/F/G missing) and 2026-09-28 (H missing)."""
        seat_unit = f"hydra_variant_{_live_seat()}"
        text = _read(CLAUDE_MD)
        wide = [c for c in _stop_commands(text) if len(_units(c)) >= 4]
        assert wide, "no fleet-wide stop command found in CLAUDE.md"
        for cmd in wide:
            assert seat_unit in _units(cmd), (
                f"CLAUDE.md's fleet-wide stop omits the live seat {seat_unit!r}: {cmd!r}"
            )

    def test_every_paper_variant_appears_in_the_claude_md_emergency_stop(self):
        """The 2026-09-19 and 2026-09-28 defects exactly: a command labelled
        'stop everything' that silently left variants running. Derived from the
        taxonomy so a NEW variant fails this until it is added.

        ⚠️ Targets the EMERGENCY stop specifically — identified as the stop
        command that also drops `calypso-broker`, which is what makes it the
        "everything" one. An earlier version of this test took the WIDEST stop
        command in the file instead, and a mutation that removed variant H from
        the emergency stop passed, because the ordinary "stop HYDRA + variants"
        command elsewhere still listed H and covered for it. Checking "some
        command is complete" is not the property; "THE command you reach for in
        an emergency is complete" is.
        """
        from shared.strategy_taxonomy import PAPER, STRATEGIES

        paper = {k for k, v in STRATEGIES.items()
                 if getattr(v, "account_kind", None) == PAPER}
        # variant 'a' is the `hydra` unit, not `hydra_variant_a`
        expected = {("hydra" if v == "a" else f"hydra_variant_{v}") for v in paper}
        emergency = [c for c in _stop_commands(_read(CLAUDE_MD))
                     if "calypso-broker" in _units(c)]
        assert emergency, (
            "no emergency stop found in CLAUDE.md — it is the command that also "
            "stops calypso-broker"
        )
        for cmd in emergency:
            missing = expected - _units(cmd)
            assert not missing, (
                f"CLAUDE.md's emergency stop omits paper variant unit(s) "
                f"{sorted(missing)} — a command labelled 'stop everything' that "
                f"leaves variants running: {cmd!r}"
            )


class TestNoDocCallsARetiredVariantTheLiveSeat:
    DOCS = [RUNBOOKS, CLAUDE_MD, "docs/GO_LIVE_MASTER.md"]

    def test_no_doc_asserts_a_non_live_variant_is_the_live_seat(self):
        """Prose, not commands. "today that is hydra_variant_b (the live paper
        seat)" is how the runbook defect read — a confident, wrong claim.

        Only unqualified present-tense claims count: a line that also carries a
        correction marker is describing the history deliberately.
        """
        seat = _live_seat()
        retired = {k for k in __import__(
            "shared.strategy_taxonomy", fromlist=["STRATEGIES"]
        ).STRATEGIES if k != seat}
        offenders = []
        for rel in self.DOCS:
            for n, line in enumerate(_read(rel).splitlines(), 1):
                low = line.lower()
                if "the live paper seat" not in low and "the live seat" not in low:
                    continue
                # a line that corrects itself is documenting the past on purpose
                if any(w in line for w in ("until 2026-", "CORRECTED", "SUPERSEDED",
                                           "was the live", "said ", "~~", "historical")):
                    continue
                for r in retired:
                    for form in (f"hydra_variant_{r} (the live",
                                 f"variant {r.upper()} is the live",
                                 f"`{r}` is the live"):
                        if form.lower() in low:
                            offenders.append(f"{rel}:{n}: {line.strip()[:110]}")
        assert not offenders, "doc(s) claim a retired variant is the live seat:\n" + "\n".join(offenders)
