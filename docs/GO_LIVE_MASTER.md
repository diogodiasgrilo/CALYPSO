# GO-LIVE MASTER — CALYPSO / HYDRA on IBKR

> **Start here for anything about taking a strategy live.** This is the single umbrella that maps the whole
> go-live path across **all strategies** and **both go-live levels**, and links out to the detailed runbooks,
> scripts, and gates (it does **not** duplicate them). Last updated: **2026-07-14**.
>
> **Reality check:** this branch (`hydra-ibkr-standalone`) trades the **IBKR paper account only**. There is
> **no live-money path wired** on this branch — real money is a deliberate, approval-gated build (see Level II).
> Today, "live" means **live-PAPER** (real orders against the paper account), and only **variant C** is there.

---

## 1. The two go-live levels (and the boundary between them)

There are **two distinct** go-live transitions. Do not conflate them.

| | **Level I — dry-run → live-PAPER** | **Level II — live-paper → REAL MONEY** |
|---|---|---|
| What changes | `dry_run: true → false` — the bot places **real orders on the IBKR *paper* account** | New **live** IBKR account/credentials — real capital at risk |
| Risk | Execution/reconciliation realism; **no capital at risk** | Real money |
| How | Manual operator flip, gated on broker health + a same-ET-day paper-smoke PASS | Full readiness checklist + **new live-OAuth keypair** + **explicit written approval** |
| Docs | **RB-8** + the flip scripts + `broker_paper_smoke.py` (this doc §4) | **`LIVE_READINESS_CHECKLIST.md`** + `IBKR_CREDENTIALS_SETUP.md` (this doc §5) |
| Status on this branch | **Available** (C is here) | **Not wired** — deliberate. Needs the Level-II build below. |

**The boundary:** everything on this branch is Level I. Level II (real money) is intentionally unbuilt — the
credential model, approval record, and cutover would all be new work (§5). Don't cross the boundary without it.

---

## 2. Per-strategy status matrix

Current mode → next gate → how it flips → its smoke test → edge/readiness verdict. (Source of truth for *mode*
is each variant's `config_variant_*.json` `dry_run` on the VM; this table is the human-readable rollup.)

| Var | Strategy (group) | Current mode | Next gate | Flip mechanism | Smoke test | Edge / readiness verdict |
|---|---|---|---|---|---|---|
| **A** | HYDRA baseline — 0DTE IC (`ic_0dte`) | dry-run shadow | Level I paper-flip (available) | [`flip_a_live.sh`](../scripts/flip_a_live.sh) (auto via smoke `ExecStartPost`) or [`flip_ac_live.sh`](../scripts/flip_ac_live.sh) | [`broker_paper_smoke.py`](../scripts/broker_paper_smoke.py) | dry-run baseline; not a live candidate today |
| **B** | Brandon Narrow 7-slot — 0DTE IC | dry-run shadow | **None — intentionally not flipped** | none by design | — | Edge is **simulated-only**; its extra slots collect ~31% more credit than real fills give (`golive_readiness_and_fill_lever` memory). Needs its **own paper account** to test live (can't co-run with C — position-merge). **Do not flip.** |
| **C** | Brandon Narrow **live** — 0DTE IC | **LIVE-PAPER** (`dry_run=false`) | **Level II** (real-money) — see §5 | already flipped via [`flip_ac_live.sh`](../scripts/flip_ac_live.sh) | [`broker_paper_smoke.py`](../scripts/broker_paper_smoke.py) | Proven live-paper; ~breakeven. **Entry-Schedule Lock ~mid-Aug** ([NEXT_STEPS §5](NEXT_STEPS.md)) is the last strategy-side gate before any real-money push. |
| **D** | DC Time Machine — multi-day SPX calendar (`calendar_multiday`) | dry-run-**LOCKED** | Level I = **a BUILD**, not a flip (DG-1..DG-11) | `flip_d_live.sh` **(NOT BUILT)** | `broker_dc_smoke.py` **(NOT BUILT)** | **NO-GO** ([`D_GOLIVE_SCOPE_AND_AUDIT.md`](migration/D_GOLIVE_SCOPE_AND_AUDIT.md)) — no real-order path; edge **INSUFFICIENT_DATA**. See [`D_GOLIVE_RUNBOOK.md`](migration/D_GOLIVE_RUNBOOK.md). |
| **E** | SPY Double Calendar — multi-day (`calendar_multiday`) | dry-run-**LOCKED** | Level I = a BUILD ([NEXT_STEPS §2b](NEXT_STEPS.md)) | `flip_e_live.sh` **(NOT BUILT)** | `broker_dc_smoke.py` **(NOT BUILT)** | Edge **INSUFFICIENT_DATA** (n=0); least-documented; SPY assignment/dividend + IV-rank gate unbuilt. E go-live runbook to be modeled on D's. |

**Key takeaways:** C is the only live variant; A/B are dry-run (B by design); **D and E are BUILDS, not flips**
(they have no real-order path yet) and are calendar-shaped, so the 0DTE readiness checklist (§5) does not
transfer to them — they need their own gate.

---

## 3. Level I — dry-run → live-PAPER (the flip that actually happens here)

**Canonical procedure: [`RUNBOOKS.md` RB-8](migration/RUNBOOKS.md)** ("Flip a variant from dry-run to LIVE paper").

**Two hard preconditions (both must be true):**
1. `calypso-broker` `/health` returns `connected:true` (the shared IBKR session is up).
2. A **fresh, same-ET-day** paper-smoke **PASS** sentinel exists at `/opt/calypso/data/smoke/last_pass.txt`
   (written by `broker_paper_smoke.py --place` — see §6).

**Execute (A + C; B stays dry-run by design):**
```bash
# 1. Same ET day: run the broker paper smoke → writes today's PASS sentinel (or aborts)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start broker-paper-smoke && sleep 20 && curl -s http://127.0.0.1:8788/health"
# 2. Flip A + C dry_run:true→false (idempotent; guarded on broker /health + the fresh sentinel)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso /opt/calypso/scripts/flip_ac_live.sh"
# 3. Verify: no "DRY RUN" lines in the logs; the flip script Telegram-alerts on success
```
- [`flip_ac_live.sh`](../scripts/flip_ac_live.sh) flips **A + C** (Guard 1 = broker `connected:true`; Guard 2 = fresh
  ET-day sentinel; idempotent; atomic JSON write; restarts the units; verifies + alerts). **There is intentionally
  NO auto-flip timer for A+C.**
- [`flip_a_live.sh`](../scripts/flip_a_live.sh) flips **A only** and is wired as `ExecStartPost=+` on
  `broker-paper-smoke.service` (auto-flip on a clean smoke).
- **Rollback:** revert the variant's config to `dry_run:true` and restart the unit (RB-8 §rollback).

**Operator rule (broker mode):** a session/auth fault is fixed by **`systemctl restart calypso-broker`**, NOT the
`hydra*` units (they proxy through the broker). See [`CLAUDE.md` calypso-broker section](../CLAUDE.md).

---

## 4. Level II — live-paper → REAL MONEY (not wired on this branch)

**Canonical gate: [`LIVE_READINESS_CHECKLIST.md`](migration/LIVE_READINESS_CHECKLIST.md)** (10 hard gates; every
item must be GREEN). **Credential mechanics: [`IBKR_CREDENTIALS_SETUP.md`](../deploy/IBKR_CREDENTIALS_SETUP.md).**

This transition is a **deliberate build**, not a flip. The essentials the checklist enforces:
- Deploy from **`main`** (not a feature branch); audit register 0-OPEN; **full test suite green** (~1918 pass);
  integration paper-smoke; `pip-audit` 0 High/Critical.
- **5 consecutive clean paper sessions**, P&L ≥ 0, no false stops, no null VIX, chaos test passed.
- A **NEW live-OAuth keypair** (not the paper keypair) encrypted to `/etc/calypso/ibkr-live/` — **in broker mode
  these live in `calypso-broker`**, not the strategy units (the checklist is being refreshed for this — see
  [NEXT_STEPS §10 B](NEXT_STEPS.md)).
- **1 contract week 1**; tightened halt criteria; **explicit written approval committed to the repo**; operator
  availability for the first session.

**Scope caveat:** the readiness checklist is written for the **0DTE IC group (A/B/C)**. The **calendar group
(D/E) needs its own real-money gate** — the 0DTE gates (paper-history shape, position sizing, flat-overnight
assumptions) don't transfer (per [`D_GOLIVE_SCOPE_AND_AUDIT.md`](migration/D_GOLIVE_SCOPE_AND_AUDIT.md) §5).

---

## 5. Strategy D / E — the calendar go-live (a BUILD, currently NO-GO)

The most thorough go-live *framework* in the repo is D's — reuse its structure for E.

- **[`D_GOLIVE_RUNBOOK.md`](migration/D_GOLIVE_RUNBOOK.md)** — §5 phased plan (PHASE 0 decision → 1A coexistence
  guards + arm-gate → 1B real-order exec path → 1C ops/flatten/rollback/smoke → 1D validate + flip → PHASE 2
  transform), each step with an ID / risk tag / exit criterion / verify command; §8 flip/rollback/flatten; §9 the
  **DG-1..DG-11** readiness gate.
- **[`D_GOLIVE_SCOPE_AND_AUDIT.md`](migration/D_GOLIVE_SCOPE_AND_AUDIT.md)** — the scope + adversarial audit.
  **Verdict: NO-GO** ("a build, not a flip") for three reasons: (1) no real-order path exists at all; (2) the edge
  was never validated and its signal is unobservable on IBKR; (3) the "risk-free" invariant is a mid-pricing
  artifact that breaks on real fills.
- **[`D_MVL_PHASE1_PLAN.md`](migration/D_MVL_PHASE1_PLAN.md)** — the reduced-scope first-live phase (drop the
  transformer). PLAN ONLY, not approved.
- **E** — only [NEXT_STEPS §2b](NEXT_STEPS.md) gates exist (SPY American-assignment + dividend handling, real IV-rank
  entry gate, coexistence MUST-FIXes, multi-contract ladder). **E's go-live runbook is not written** ("model on D's").

**Edge status (both):** `INSUFFICIENT_DATA` (D n=1, E n=0) per the edge reader (§6). MVL-D is gated on
`EDGE_POSITIVE`.

---

## 6. Tests + pass-criteria appendix (the one place)

| Test / analyzer | What it verifies | PASS criterion | How to run |
|---|---|---|---|
| **`broker_paper_smoke.py`** ([scripts](../scripts/broker_paper_smoke.py)) | The production order path end-to-end (BrokerClient→broker→IBClient) is healthy + real-time | HARD-refuses non-paper (`DU…`) accounts; requires `6509` first-char `='R'` on SPX **+** VIX **+** the SPXW leg; `--place` does a real 1c buy→fill→sell round trip that closes flat → writes ET-dated `data/smoke/last_pass.txt`; **exit 0** | `sudo systemctl start broker-paper-smoke` (or `python -m scripts.broker_paper_smoke --place`) |
| **Full pytest suite** | Code correctness / regressions | **~1918 passed, 15 skipped, 0 failed** (Level-II Gate 3) | `.venv/bin/python -m pytest tests/ -q` |
| **Integration paper-smoke** (`tests/integration/test_ib_paper_smoke.py`) | 15 IBKR paper-account integration tests | ≥15 passed, run against paper in last 7 days | `IBIND_INTEGRATION=paper .venv/bin/python -m pytest tests/integration/ -v` |
| **`pip-audit`** | No High/Critical CVEs in the IBKR stack | 0 High/Critical | `.venv/bin/pip-audit -r requirements.txt` |
| **`slot_edge.py`** ([bots/hydra](../bots/hydra/slot_edge.py)) | Per-slot edge (95% t-CI + sample floor) | verdicts firm up as sample grows (Entry-Schedule Lock, NEXT_STEPS §5) | `python -c "from bots.hydra.slot_edge import analyze_slots, format_slot_report; print(format_slot_report(analyze_slots('data/variant_c/backtesting.db')))"` |
| **`stop_shadow.py`** ([bots/hydra](../bots/hydra/stop_shadow.py)) | %-of-width stop replay vs acting stop | RESOLVED 2026-07-14: don't flip C's stop | `analyze()` / `format_report()` over the variant DB |
| **`dc_edge.py` / `analyze_calendar_edge.py`** | D/E calendar edge | `EDGE_POSITIVE` required to advance MVL-D; currently `INSUFFICIENT_DATA` | see NEXT_STEPS §3 |
| **D STATE-004 matrix** | D coexistence/recovery | per `D_GOLIVE_RUNBOOK.md` §6 / `D_MVL_PHASE1_PLAN.md` §3 | (D build) |

---

## 7. Go-live line items (carried here, tracked in NEXT_STEPS)

- **Entry-Schedule Lock (~mid-August 2026)** — the last strategy-side gate: with ~40+ live-paper days, re-run
  `slot_edge.py` + the real-fill economics filter and **lock C's go-live entry schedule** on economics + tail-risk
  + robustness (NOT p-values — per-slot significance takes months–years given ~$884/entry std). [NEXT_STEPS §5](NEXT_STEPS.md).
- **Split-spread / midpoint ENTRY-pricing fill-quality lever** — targets the measured ~31% sim-vs-live credit gap
  (C fills ~$0.025/leg below mid). HYDRA already prices entries at `mid ± buffer` (`base_strategy.py:~2815`); the
  lever is tightening toward pure mid / a pegged-midpoint order. **ENTRY-ONLY** (never stops — those use aggressive
  marketable limits, correctly); trades price for fill-rate; **validate on real money** (paper fills midpoint orders
  optimistically). Memory `golive_readiness_and_fill_lever`.
- **B viability** — B's edge is simulated-only and can't be A/B-tested against C on one account (position-merge);
  the only clean test is a **separate IBKR paper account for B**. Not warranted for a go-live-on-C decision.
- **Per-strategy edge validation** — C proven (breakeven, live-paper); D/E `INSUFFICIENT_DATA`.

---

## 8. Build-gated pending artifacts (do not exist yet)

Blocked on the D/E real-order **builds** (no real-order path exists for calendars), so a master doc can only
reference them as pending:

- `RUNBOOKS.md` **RB-9** (flip D) / **RB-10** (flatten D)
- **E's go-live runbook** (model on `D_GOLIVE_RUNBOOK.md`)
- `scripts/flip_d_live.sh`, `scripts/flip_e_live.sh`
- `scripts/broker_dc_smoke.py` (the calendar equivalent of `broker_paper_smoke.py`)

---

## 9. The reusable framework + related docs

- **Generic go-live template:** [`NEW_STRATEGY_PLAYBOOK.md`](NEW_STRATEGY_PLAYBOOK.md) **Step 10** — a
  strategy-agnostic go-live gate (arm-gate, flip/rollback/flatten, smoke, readiness gate, halt/kill). Use it +
  `D_GOLIVE_RUNBOOK.md` §5 as the template for any new go-live.
- **Incident runbooks:** [`RUNBOOKS.md`](migration/RUNBOOKS.md) RB-1..RB-7 (session lost, orders breaker, null
  SPX/VIX, stolen session, deploy rollback, naked short, backup restore).
- **State tracker:** [`docs/migration/PROJECT_STATUS.md`](migration/PROJECT_STATUS.md) (project state) +
  [`NEXT_STEPS.md`](NEXT_STEPS.md) (current what's-left, incl. **§10** — this doc's own build/refresh TODO).
- **Operator reference:** [`CLAUDE.md`](../CLAUDE.md) (deploy, troubleshooting, calypso-broker).

---

*Maintenance: keep §2 (matrix) and §6 (tests) current; this doc links out rather than duplicating, so most updates
happen in the linked sources. The open build/refresh tasks for this doc live in [NEXT_STEPS §10](NEXT_STEPS.md).*
