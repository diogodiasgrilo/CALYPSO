# CALYPSO — Next Steps (living doc)

> **This is the single, always-current "what's left" tracker.** Update it whenever work lands or a new
> item appears. It complements (does not replace) [`docs/migration/PROJECT_STATUS.md`](migration/PROJECT_STATUS.md)
> (project-wide state) and the per-effort design docs. When an item is done, check it off here and move the
> detail into the relevant doc/commit. Last updated: **2026-06-16**.

---

## 0. Current snapshot

- **Branch:** **MERGED** into `hydra-ibkr-standalone` (2026-06-16, at `e5688f0`; latest `741fc66`). The merge
  also reconciled a HOMER auto-commit that had regressed the mainline (see [[homer-vm-autocommit-gotcha]] / §6).
- **Built + tested (full suite ~1686 passed / 15 skipped; frontend `tsc -b` + `vite build` clean):**
  Strategy taxonomy → bot wiring → `CalendarStrategyBase` (D byte-identical) → **Strategy E (SPY double
  calendar, dry-run-locked)** → dashboard backend `/api/strategies` → dashboard frontend (picker + group
  tabs + **full-parity non-primary 0DTE view** + EOD auto-update) → comms (group-scoped `/compare` + display names).
- **Deployed to the VM:** the full dashboard (picker + group tabs + full-parity 0DTE view) AND **Strategy E
  running dry-run** (`hydra_variant_e` active; SPY resolves @~750.72; recorder DB created; available in the
  picker). `calypso-broker` was restarted for the new `shared/ib_client`; session healthy. VM tree clean.
- **Still dry-run-LOCKED / NOT live:** E and D place **no real orders**. A/B/C unchanged (A=dry-run per its
  config, C=live-paper). Real-money/live-paper go-live for E/D remains gated (§2b/§3).

---

## 1. Finish this effort (Strategy-E + grouping)

- [ ] **Documentation consolidation** (from the 2026-06-16 doc audit — 3 agents). Fix staleness/contradictions:
  - [ ] **CLAUDE.md**: rewrite the *Variant Comparison* section around the 5-variant / 2-group taxonomy; add D
        + E to the structure/deploy/service-name/state-file lists; update the *Adding a new variant* recipe to
        start with the taxonomy + registry rows; document group-scoped `/compare`, the strategy picker, group
        tabs, EOD auto-update, and the `/api/strategies` + `/api/dc` endpoints; add doc-index rows for
        STRATEGY_GROUPING_REDESIGN + the D go-live docs.
  - [ ] **PROJECT_STATUS.md**: add a state section for the Strategy-D full build + the Strategy-E/grouping work
        (it currently stops at 2026-06-02 and is silent on both); refresh header + pointer index.
  - [ ] **Code comments/docstrings**: genericize `calendar_strategy_base.py` (its docstrings + `[DCTM-*]` log
        tags say "D" but it's shared by D **and** E); fix `double_calendar_strategy.py.__init__` (says "NOT
        implemented / stubbed" while the rest of the file says "IS implemented"); fix `registry.py` "SCAFFOLD /
        stubbed" comments for D/E; generalize `dc_status.py` ("D-native" → calendar-group); add `/compare` +
        `/calendars` to the `telegram_commands.py` header command list.
  - [ ] **STRATEGY_GROUPING_REDESIGN.md**: flip the header off "Status: DESIGN / no code written yet" →
        IMPLEMENTED (commits f9e7d2a…); resolve the §6 OPEN DECISIONS the code already answered.
  - [ ] **NEW_STRATEGY_PLAYBOOK.md**: the audit-log row records the *wrong* E identity (Theta-Profits/Ahuja);
        the shipped E is the **SPY/OptionsKit** double calendar — correct it; note `CalendarStrategyBase` is no
        longer hypothetical (it exists).
  - [ ] **D go-live docs** (`D_GOLIVE_*`): note that D's shared `_dc_*` methods now live in
        `bots/hydra/calendar_strategy_base.py` (their `double_calendar_strategy.py:NNN` line refs are stale
        post-lift).
- [ ] **Frontend visual verification** — eyeball the deployed dashboard (§4 checklist).
- [ ] **Code refinements** (safe, not go-live-gated):
  - [ ] Multi-contract ladder: `_spy_dc_partial_close` must scale `entry.net_debit` down on a partial close
        (irrelevant at the 1-contract default; required before multi-contract sizing). Verify `CalendarEntry`
        contract-scaling semantics first.
  - [ ] `__init__.py` version-history entry — **DONE** (capstone of the comms commit).
- [ ] **Merge** the feature branch into `hydra-ibkr-standalone` via PR (full suite + frontend build green).
      → reconciles the VM dashboard overlay (§4) on the next `git pull`.

---

## 2. Deploy Strategy E (SPY double calendar)

E is **dry-run-LOCKED** (un-flippable constructor lock). Two stages:

### 2a. Run E as a dry-run variant on the VM (safe — no real orders)
- [ ] On the VM (as `calypso`): create `bots/hydra/config/config_variant_e.json` (the committed file is the
      template; configs are gitignored/`skip-worktree` on the VM). **Verify `underlying_symbol: "SPY"`,
      `trading_class: "SPY"`, `exchange`, `strike_increment: 1`** — a stale value silently breaks chain/quote
      resolution.
- [ ] Probe SPY data on the VM (read-only) **before** relying on it: confirm `qualify_contract("SPY",
      sec_type="STK")` + the SPY option chain + quotes/greeks resolve through `calypso-broker`. SPY is the
      probe control instrument, so this should work — but verify the exact `exchange` (NYSE vs ARCA vs SMART).
- [ ] Install `deploy/hydra_variant_e.service` (`HYDRA_VARIANT_ID=e`, broker-proxy mode), `daemon-reload`.
- [ ] ⚠️ If `shared/` changed since the broker last restarted, **restart `calypso-broker` FIRST** (it holds
      the SPY conid pin in `ib_client.py`) — see CLAUDE.md deploy workflow.
- [ ] `systemctl start hydra_variant_e`; watch logs; confirm the dry-run lifecycle fires (entry sim →
      laddered TP → time-exit) with no real orders.
- [ ] Add `variant_e_*` paths to the VM's `dashboard/backend/config.py` (committed on the branch) so the
      dashboard picker shows E with data.

### 2b. Take E LIVE (real paper orders) — GO-LIVE GATES (multi-week, deliberate)
- [ ] **SPY American assignment + dividend handling** — SPY options are physically settled; the short near
      leg can be early-assigned if ITM (esp. around ex-div for calls). The time-exit ("never hold to expiry")
      is the first defense; a real-order path needs explicit assignment detection + cover-with-long, and
      `_read_open_positions` must include equity (STK) positions (today it filters to OPT).
- [ ] **True IV-rank entry gate** — replace the VIX-threshold proxy with a real IV-history source (the video
      enters when IV < ~1yr median).
- [ ] **Coexistence MUST-FIXes** (shared with D — land once in `CalendarStrategyBase`): scope C's STATE-004
      overnight guard + orphan sweep to per-variant conids; per-variant buying-power budget.
- [ ] **Multi-contract ladder fix** (§1) before sizing > 1 contract.
- [ ] Remove the dry-run constructor lock only via a documented manual flip + a fresh same-ET-day paper
      smoke PASS — never an auto-flip. Write E's go-live runbook (model on `D_GOLIVE_RUNBOOK.md`).

---

## 3. Deploy Strategy D (DC Time Machine)

D is **dry-run-LOCKED** and the go-live audit verdict is **NO-GO** (see
[`D_GOLIVE_SCOPE_AND_AUDIT.md`](migration/D_GOLIVE_SCOPE_AND_AUDIT.md)). D already runs as a dry-run variant on
the VM (per its memory/journal). To take D live:
- [ ] Follow [`D_GOLIVE_RUNBOOK.md`](migration/D_GOLIVE_RUNBOOK.md) in full (DG-1..DG-11 gate).
- [ ] Build the real-order execution path (D is fully simulated today).
- [ ] Ship the **coexistence MUST-FIXes** (shared with E — STATE-004 / orphan sweep scoping + per-variant BP).
- [ ] Validate the edge (backtest/soak) and confirm the risk-free transform invariant survives real fills.
- [ ] Consider the **MVL-D** first phase (drop the transformer; defined-risk debit calendar + stop) per
      [`D_MVL_PHASE1_PLAN.md`](migration/D_MVL_PHASE1_PLAN.md).
- [ ] NOTE: D's go-live docs predate the `CalendarStrategyBase` extraction — their file:line refs to
      `double_calendar_strategy.py` are stale for the shared `_dc_*` methods (now in the base). Update them (§1).

---

## 4. Dashboard deploy (done — reconcile on merge)

- **State:** the VM dashboard runs the feature-branch backend (`dashboard/backend` + `shared/strategy_taxonomy.py`
  overlaid via `git checkout FETCH_HEAD -- …`) + the new frontend `dist`. Bot code is untouched on
  `hydra-ibkr-standalone` HEAD. A `dist.bak.predeploy.*` backup exists.
- [ ] **Visual verification checklist:** picker switches the main view + the whole header follows it; selecting
      D shows the debit calendar layout (no credit/buffer, no SPX/VIX chips); group tabs (`ic_0dte` A/B/C panels,
      `calendar_multiday` debit comparison); legacy `/comparison` + `/dc` redirect; no cross-strategy stop-toasts;
      cumulative + "Last Trading Day"/"Week in Review" cards refresh at ~4 PM ET without reload.
- [ ] **Reconcile on merge:** after the branch merges to `hydra-ibkr-standalone`, on the VM
      `git checkout -- dashboard/backend shared/strategy_taxonomy.py` (discard the overlay) then `git pull`
      (brings the same code via the merge), clear cache, restart dashboard. The overlay and the merged code are
      identical, so this is a no-op refresh that restores a clean git tree.

---

## 5. Backlog / nice-to-haves (from the audit + design)

- [x] **Per-strategy History/Analytics (item 3, 2026-06-22).** Both tabs now follow the strategy picker:
      `/api/metrics/{daily,entries,stops,comparisons,performance}` take an optional `strategy_id` and read that
      variant's own DB (today-from-live-state gated to the canonical id); the picker shows on /history + /analytics.
- [x] **WebSocket per-strategy subscribe — DECIDED 2026-06-22: keep polling.** The non-primary main view already
      works via 2s polling; a true WS multiplex (per-connection strategy channels) was judged not worth the
      live-dashboard risk (cross-strategy message bleed) for the marginal latency gain. Polling is the accepted
      non-primary path. Revisit only if real-time non-primary becomes a felt need.
- [x] **IC `ConfigDelta` control column (item 5, 2026-06-22).** No longer hardcodes `variants["A"]` — derives the
      baseline from `variantIds[0]` (the group's first member, matching the backend's `members[0]`).
- [x] **Per-command Telegram name selectors (item 6, 2026-06-22).** `/status [variant]`, `/snapshot [variant]`,
      `/stops [variant]` render a named NON-primary variant from its own state file via one unified view (reuses
      `_load_variant_state` + `_build_variant_summary`). Pragmatic scope: D/E point at `/calendars`; A falls
      through to the full view.
- [x] **Genericize the `[DCTM-*]` log tags (item 2, 2026-06-22).** The shared `CalendarStrategyBase` now logs a
      neutral `[CAL-*]` (D's own file keeps its legitimate `[DCTM-*]`), so E's calendar plumbing is no longer
      mis-attributed to D.

## 6. Brandon viability + fill-quality (from the 2026-06-22 review)

Two fixes shipped 2026-06-22 (live on B/C): **MKT-048** (entry fillability gate — don't open a side whose
`short_bid − long_mid` can't clear the net-credit floor) and **MKT-049** (exit net-of-cost TP gate — don't
take profit on a mid mark when the real `short_ask − long_bid` + commission gives the gain back; defer to
expiry instead). See `bots/hydra/__init__.py` version history.

- [ ] **CONFIRM LIVE (P2.5):** over the next few live days, verify MKT-048 eliminates the leg-3 entry bleed and
      MKT-049 cuts the close drag on C. Look for `MKT-048: … vetoing` and `MKT-049 … DEFERRED` in C's logs.
- [ ] **Commission ratio (P2):** commission/gross is **invariant to contract count** (both scale with size);
      it's driven by **credit-per-spread**. Over the live period ~45% of gross credit is lost to commission +
      close slippage. Biggest recovered chunk = holding thin spreads to expiry (MKT-049). Keep measuring.
- [ ] **FUTURE TEST (parked — Lever 2, do NOT start yet):** test a **thicker-credit-per-spread** strike config
      (wider / closer-to-money shorts → more credit per leg → lower commission ratio) on **B (dry-run shadow)**
      for ~2 weeks, compare commission ratio + win rate vs C, THEN decide whether to bring to C. This is a
      risk/reward change to Brandon's 8δ delta-target — parked until explicitly requested.

## 7. MVL-D vs Strategy E (2026-06-22 — IMPORTANT realization)

**Strategy E already IS the "transformer-less managed double calendar" that an MVL-D would be** — just on SPY
(not SPX) and with *better* management (laddered partial profit-take + time-exit before short expiry, no hard
stop) than D's crude `−20% stop + EOD-close`. D's transformer is the only thing E lacks, and the transformer
is exactly the unproven/illusory part (see `docs/migration/D_GOLIVE_SCOPE_AND_AUDIT.md`). So:

- [ ] **DECIDE before building MVL-D:** do we even need an SPX plain-calendar test when E already runs the SPY
      one? E answers "is a plain managed double calendar +EV?" today. MVL-D's only *unique* value is (a) SPX
      underlying (cash-settled, no assignment, bigger notional, different liquidity/tax) and (b) hard-stop vs
      E's ladder as a management comparison.
- [ ] **If we do build it:** don't re-derive "D minus transform with a crude stop." Instead scope MVL-D as
      **"E-style management ported onto an SPX double calendar"** (reuse E's proven ladder + time-exit; swap
      underlying SPX↔SPY in `CalendarStrategyBase`). Cheaper and strictly better-managed than gutting D.
- [ ] Either way: **gate behind E proving +EV first** in dry-run. Building a second unproven calendar before
      the first one shows an edge is premature.

## 8. Dashboard — make take-profits visible on the Intraday P&L line

The Intraday P&L line plots `realized − commission + Σ(live mark-to-market of open positions)` (a mark-to-market
equity curve), so a take-profit is a near-no-op on the curve (the gain was already "in" as unrealized before
the close). It's NOT a bug — B's "smooth" line and C's "stepped" look are the same code at different P&L scales.
To actually *show* TPs:

- [ ] **Preferred — TP/close event markers:** backend exposes each entry's close events (timestamp + reason +
      realized); frontend overlays dots/annotations on `PnLCurve.tsx` at those timestamps. Keeps the live
      mark-to-market line AND shows where TPs fired (works at any scale). Read-only dashboard change.
- [ ] **Alternative — realized-only line:** plot only `realized − commission` (no open-position mark), so the
      line is flat between closes and steps up at each TP. Crisp TP steps, but you lose the intraday
      mark-to-market "how are open positions doing right now" view. Design choice; would apply to all variants.

## 9. Repo hygiene (2026-06-22)

- [x] **`.gitignore` hardened against stray `git add -A`.** The VM accumulated ~28 untracked artifacts not
      covered by ignore rules; now `*.bak*` (was `*.bak`), `dist_old/`, `intel/homer/`, and the `.cache/`
      `.config/` `.gsutil/` tool-cache dirs are ignored. Verified the VM `git status` is now clean. HOMER is
      already path-scoped, so this defends mainly against a human/script `add -A`.
- [ ] **Optional — untrack the legacy `intel/homer/2026-05-*.md`.** Those dated reports are TRACKED (swept in
      by the pre-2026-06-16 `git add -A` HOMER bug); the dir is now ignored so new ones won't be added, but the
      old tracked ones remain (cosmetic "tracked-but-ignored"). `git rm --cached intel/homer/2026-05-*.md`
      cleans it up (keeps the files on disk). Not done automatically — it removes them from the repo.
- [ ] **Optional — delete stale disk clutter on the VM** (old `dist.bak.*` from April, ancient config `.bak`s).
      Pure disk cleanup; left alone since some config backups may be intentional safety copies.
