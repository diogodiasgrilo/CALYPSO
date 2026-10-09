# PRE-REGISTRATION — does the GEX gate earn its keep?

**Registered 2026-09-29, before any out-of-sample data exists. Nothing is disabled today.**

Supersedes the framing in [`PR1_PR2_DEEP_DIVE_2026_09_28.md`](PR1_PR2_DEEP_DIVE_2026_09_28.md).
Two narrower hypotheses were tested and both failed; what replaced them is bigger.

---

## What was ruled out first

**PR-1a — "the adjuster should SHIFT where it SKIPs."** The source only ever describes moving
the band, and SHIFT has never once executed in 166 live decisions. The shadow deployed
2026-09-29 got five chances that same day and **agreed with the live SKIP all five times**: the
acceleration zone ran 7695–8060, so clearing it needed 330–350pt against a 25pt cap. There was
nowhere to shift to. **Not refuted by argument — refuted by its own shadow, on day one.**

**PR-1b — "the zones are nonsense because the sign convention is inverted."** Scored against
what actually happened (`scripts/gex_sign_convention_verdict.py`): flipping would have blocked
18 entries that were placed (worth **+$295**, 14 of them winners) and taken 11 that were skipped
(worth **−$487.90** with the A2 stop). **Net −$782.90 — and n = 29 changed entries, mean −$27,
t = −0.26, 95% CI [−$233, +$179]. Indistinguishable from zero.** The corrected gate does not
block fewer trades; it blocks a *different* set, disagreeing on **44%** of decisions and buying
nothing.

So the gate is not mis-signed and not mis-actioned. The question left is whether it carries
information at all.

---

## The finding that generated this hypothesis

Breach rate of the strike the gate judged, split by its verdict, over 166 decisions:

| verdict | n | mean OTM distance | median | **breached** |
|---|---|---|---|---|
| **SKIP** (vetoed) | 28 | **42.9pt** | 43.5pt | **0 / 23 = 0%** |
| **KEEP** (allowed) | 138 | **42.0pt** | 41.1pt | **26 / 131 = 20%** |

**The distances are the same.** The gate is not vetoing further-out strikes — median 43.5pt
vetoed against 41.1pt allowed — so the 0% cannot be explained away as "it only blocks safe ones
because they were already far away."

Under the null that a vetoed strike breaches at the same rate as an allowed one (19.8%):

> **P(0 breaches in 23) = 0.0062**

**The gate is not noise. At matched distance its vetoes are significantly SAFER than its
approvals** — it is selecting against the trades it should be taking.

⚠️ **This is in-sample.** It was found by looking, so it cannot also test itself.

---

## The registered test

**Cut-off: 2026-09-29.** Every decision on or before today formed the hypothesis and is
**excluded**. Only decisions from **2026-09-30** onward count.

| | |
|---|---|
| **Primary statistic** | breach rate of GEX-vetoed strikes, out-of-sample |
| **Benchmark** | breach rate of KEPT strikes over the same window, restricted to vetoed-distance ±10pt |
| **Required n** | **25 out-of-sample vetoes** — the sample at which a continued 0% reaches p < 0.01 against a 20% base (21 needed at 20%, 29 at 15%; 25 is the midpoint and is fixed now, not chosen later) |
| **DISABLE the SKIP action if** | vetoed breach rate ≤ benchmark **and** the binomial p < 0.01 |
| **KEEP the gate if** | vetoed breach rate > benchmark, **or** p ≥ 0.01 at n = 25 |
| **On "keep"** | the hypothesis is recorded **FAILED**. Not retried with a longer window, a different arm, or a P&L statistic. |

**Breach rate, not P&L, is the statistic** — deliberately. A veto's stated claim is *"this strike
is in danger."* That claim is directly checkable and cannot be rescued by a lucky run, whereas a
P&L test on 29 changed entries has a 95% CI over $400 wide and would authorise almost anything.

**Not permitted:** switching to the `flipped_sign` or `all_fixes` arm after seeing results
(both are already scored above and neither helped) · scoring on profit · counting credit-gate
skips as GEX vetoes · stopping early on a favourable count.

---

## If it disables, what actually changes

Only the **SKIP** action. `KEEP` and `SHIFT` are untouched, the GEX **breach exit** is a separate
mechanism and is untouched, and `one_sided_entries_enabled=false` stays as it is — that rule is
aligned with the source and is not what this is about.

Expected effect, for calibration rather than as a target: the gate drove roughly 23 of B's
require-both-sides skips. At the measured value of a placed entry this is a small positive, and
**it is not the reason to do it** — the reason is that a filter whose vetoes are safer than its
approvals is subtracting information.

---

## Status

| | |
|---|---|
| Disabled today | **nothing** |
| Live gate | unchanged — still SKIPs |
| Shadows running | `shift_first` (per-decision) · `flipped_sign` / `windowed` / `all_fixes` (per-decision) |
| First evaluable date | **2026-09-30** |
| Config freeze | holds — [`GO_LIVE_MASTER.md`](GO_LIVE_MASTER.md) §2-quater |

---

## Contamination assessment — recorded 2026-10-09, BEFORE the result is known

**Progress at the time of writing: 19 of 25 out-of-sample vetoes (~3 trading
days).** The result is NOT yet readable, which is the only reason this
assessment is worth anything — made after seeing the answer it would be a
rationalisation.

`scripts/registered_test_eta` flags every economic commit since the cut-off that
touched this test's dependency paths. It **flags; it does not rule.** Each is
assessed below against what the test actually measures — *veto decisions and
delta-target strike selection*.

### Direct hits (paths specific to this test)

| commit | what it changed | verdict |
|---|---|---|
| `1ab9222` 10-01 | Polygon chain page cap 4 → 20 (`gex_provider.py`) | ✅ **cleared against DATA**: the chain hit the 1,000 cap on exactly two days ever (09-18, 09-30), which produced **2 GEX decisions and 0 vetoes** between them, and **0 of 54** out-of-sample decisions came from truncated days |
| `8d4102c` 10-07 | advisory dedupe store + logging helpers | ✅ logging only — adds `_brandon_advisory_log`, `ADVISORY_REPEAT_SECONDS`, `_advisory_log_store`; no order or selection behaviour |
| `85031dc` 10-01 | a `logger.error` for a failed price refresh | ✅ logging only |
| `735c2df` 10-06 | `_swallow.note(...)` exception counters | ✅ telemetry only |

### Shared-file hits — including all three of 2026-10-09

`CRITICAL #7b` (hold the hedge when the short close fails), **A3** (clamp the
close quantity to the broker's position) and **A4** (report refused-vs-unfilled)
all land in `base_strategy.py` / `strategy.py`, so they flag.

**All three are EXIT-path changes.** This test's subject is **entry-time** strike
selection and veto decisions. A veto is decided before any position exists, and
a "breach" is spot crossing a strike — a market fact independent of how, or
whether, we later closed. No mechanism is apparent by which a close-path change
could alter which strikes were vetoed or how often a vetoed strike was breached.

### Verdict

**The out-of-sample sample is assessed UNCONTAMINATED.** Every direct hit is
logging/telemetry, the one substantive direct hit was cleared against data, and
the 10-09 changes act only after entry.

⚠️ Recorded so the decision in ~3 days is **not** made under time pressure, and
so this reasoning can be challenged rather than merely asserted. If someone
disagrees with the exit-path argument, the place to say so is **now**, not after
reading the number.

