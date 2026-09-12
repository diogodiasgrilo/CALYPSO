# AUD5 — Post-Cutover Go-Live Audit (since AUD4)

**Scope:** every change since the last comprehensive audit (AUD4, the 30-agent migration audit) — commit range **`38ac9d6..d83d50b`** (2026-05-31 16:48 → 2026-06-02 15:18 ET). 18 commits, ~49 files, **+2179/−299**. This is the Saxo→IBKR go-live hardening: market-data gating, 429 penalty box, conid pin + `/secdef` priming, LST refresh, 410/tick handling, ITM settlement, real entry slippage + fill prices, margin NULL, Brandon close recording, Sheets 429/grid, claude_client pacing, ARGUS fixes, and the **dry-run→live-paper auto-flip scripts**.

**Method:** 20 parallel domain audit agents (read + command access, no edit) → 3 meta-auditors that independently brainstormed the full audit surface and checked the 20's coverage item-by-item → 16 backfill agents that audited only the gaps the meta-auditors flagged. 39 agents total, 2.71M subagent tokens, ~1832 tool calls.

**Raw output:** 155 findings (89 audit + 66 backfill). Many are duplicates across the audit/backfill passes — this register **deduplicates into clusters** and **re-adjudicates severity** (agent-assigned "critical" was frequently inflated; the orchestrator verified each cluster below against the actual code). The complete un-deduped raw register is in the Appendix.

**Meta-auditor verdict:**
- **Code correctness lens — HIGH confidence, 0 gaps.** The 20 covered the changed code paths well.
- **Dead-code / infra lens — 15 gaps.** The 20 under-covered `deploy/` + `scripts/`; backfill surfaced the broken go-live flip mechanism in detail.
- **Documentation lens — MEDIUM confidence, 4 gaps.** Backfill surfaced PROJECT_STATUS / spec / version-history staleness.

---

## ⚠️ Go-live blockers (relevant to the A+C live-paper flip after today's 2026-06-02 close)

### GL-1 — The `flip_ac_live.sh` mechanism cannot fire as planned (CONFIRMED, operational-HIGH)
Clusters raw C6, C7, C12, C13, C14, H6, H8, H24, C18.

The operator plan (commit `b9e79a4`) is "flip A+C to real paper after the 06-02 close." The mechanism is broken three ways:

1. **Orphaned — no scheduler.** `scripts/flip_ac_live.sh:7-8` claims it is "Scheduled via a one-shot systemd timer at 21:30 UTC," but **`deploy/` contains no `flip_ac_live.timer`/`.service`**. Only `flip_a_live.sh` is wired (as `ExecStartPost=+` on `broker-paper-smoke.service`). `flip_ac_live.sh` is manual-only and will never auto-run. *(Verified: `ls deploy/ | grep flip` → none; only `broker-paper-smoke.{service,timer}`.)*
2. **Guard 2 aborts on a stale sentinel.** `flip_ac_live.sh:24-29` requires a `${today} PASS` line in `/opt/calypso/data/smoke/last_pass.txt`. The smoke is a **one-shot timer for `2026-06-01 09:35 ET`** (`broker-paper-smoke.timer`), so the only sentinel is `2026-06-01 PASS`. Run today, the guard greps for `^2026-06-02 PASS`, finds nothing, and exits 0 ("not flipping") → **A+C stay dry-run**. No mechanism is scheduled to write a fresh 06-02 sentinel.
3. **`date +%F` uses local TZ, not ET** (`flip_ac_live.sh:25`, `flip_a_live.sh:32`). On a UTC VM, after 20:00 ET (00:00 UTC) the local date rolls a day ahead of the ET sentinel → guard mismatch. Secondary to #2 but compounds it.

**Net effect:** the intended A+C go-live for the 2026-06-03 session will **not happen** via this script as-is. To execute the plan you must either (a) re-run the smoke today to write a fresh `2026-06-02 PASS` sentinel, then manually run `flip_ac_live.sh`; or (b) relax Guard 2 / fix the date handling and wire a timer. *(Note: variant **A may already be live** from the 06-01 `flip_a_live.sh` auto-flip if Monday's smoke passed — that's VM state, not visible from the repo. Confirm A's actual `dry_run` on the VM before assuming.)*

### GL-2 — ORDER-004 buying-power gate silently fails open on IBKR + logs an error on every entry (CONFIRMED, HIGH)
Clusters raw C5, C15, C16, C17, H23.

`base_strategy.py:5543` (the **BP-OK success path**, hit on every entry where `available >= required`) does `f"BP OK: ... (margin {margin_pct:.1f}% used ...)"`. On the IBKR path `margin_pct = balance.get("MarginUtilizationPct")` is **`None`** (intentional — line 5489). `None:.1f` raises `TypeError`. The broad `except` at 5545 catches it and returns `(True, "Balance check skipped (error: ...)")`.

**Net effect in LIVE mode (A+C about to flip):** ORDER-004 is effectively **disabled** — every entry hits the TypeError, the gate returns "skipped," and an `ERROR` is logged each time. Not a bot crash (caught), but the buying-power protection you're trying to validate during the live-capture observation is a no-op, with per-entry error spam. Lines 5536/5540 have the same bug on the insufficient-BP path. **Trivial fix:** reuse the existing safe pattern from line 5491 (`_util_str = f"{margin_pct:.1f}%" if margin_pct is not None else "n/a"`) at 5536/5540/5543. The agents rated this "critical/crash"; the broad `except` downgrades it to HIGH (gate-disabled + log spam), but it should be fixed before relying on ORDER-004 in live paper.

---

## High-priority correctness findings

### C-1 — Real-time market-data gating is incomplete (CONFIRMED, HIGH)
Clusters raw C1, C2, C3, C4, C8, C9, C10, H1, H2, H16, H17, H18, H19, H20, H29, H30.

Commit `651a5cc` ("market-data availability now actually gates trading") surfaces IBKR field 6509 but leaves three gaps:

1. **Index quotes — `_update_market_data()` (`base_strategy.py:4111`, `:4118`)** sets `current_price`/`current_vix` whenever `avail != "Z"`, so **`D` (delayed), `Y` (frozen-delayed), `N` (not-subscribed)** flow into strike calc / VIX regime. This is **inconsistent with `update_spx()`/`update_vix()`**, which refuse to advance the freshness clock on `Z/Y/N`. Delayed (`D`) data is never blocked from `current_price` at all.
2. **Market-halt — `_check_market_halt()` (`base_strategy.py:5584`)** triggers only on `Z`, missing `Y`/`N`.
3. **Option quotes — no gate exists.** `strategy.py:1590` references `_option_quote_is_realtime()` as the gating mechanism, but **that function is never defined** (verified by repo-wide grep). `_read_option_quotes_batch()` doesn't even surface the `availability` field, and the five pricing call sites (MKT-020/022 tightening `:4054/:4287`, entry-window capture `:4697`, dry-run sim `:7045`, entry-price update `:7089`) plus `_read_option_quote()` callers (`:2699` Fix #81, `:6538` MKT-033 profit gate) consume bid/ask/mid with **no real-time check**.

**Practical risk:** during normal RTH with entitlement, quotes are `R`, so this is dormant. The exposure is an **entitlement lapse or delayed feed mid-session**, where the bot would keep pricing real paper orders off stale/delayed option quotes. The commit claimed to add this gate; it is half-built. Recommend implementing `_option_quote_is_realtime()` (`availability[:1].upper() == "R"`) and wiring it into the pricing paths, plus extending the index/halt checks to `Y`/`N`. Add tests for `Y`/`N`/`D` (none exist).

### C-2 — Empty positions list leaves stale rows on the Positions sheet (CONFIRMED, MEDIUM)
Clusters raw C11, H5, H21.

`logger_service.py:1569` guards the resize+update behind `if all_rows:`. When all positions have expired (empty list), the `resize(1, 17)` that would shrink the sheet to header-only never runs, so **stale position rows persist visually**. The adjacent comment (`:1563-1568`) claims "the resize already grows AND shrinks to exact size removing stale rows" — false for the empty case. Display-only (no trading impact). Fix: run the shrink-to-header path when `all_rows` is empty.

### C-3 — Settlement metrics write is throttled (CONFIRMED, MEDIUM)
Clusters raw C22, H4.

`strategy.py:9392` calls `log_performance_metrics(period="Intraday")` during **post-settlement** reconciliation (`main.py:464`). The throttle in `logger_service.py:~2615` exempts only periods containing `End`/`All`/`Weekly`/`Monthly`/`Final`. `"Intraday"` matches none, so the authoritative end-of-day metrics write can be throttled/dropped. Contradicts the audit intent that settlement writes bypass the 60s throttle. Fix: pass an exempt period label (e.g. `"End of Day"`) on the settlement path.

---

## Documentation findings (doc-only; no trading impact, but the project's "single source of truth" discipline relies on these)

Confirmed clusters (raw IDs in parens):

- **PROJECT_STATUS.md is stale** — header metadata wrong (Last-updated `2026-05-31`, Last-commit `3d90d15`→ actual `d83d50b`, Commits-ahead `141`→ ~153) (C19/H11); describes the superseded 06-01 auto-flip plan instead of the 06-02 A+C manual flip (C20/H9/H12/H26); "today is Sunday 2026-05-31" context is 2 days stale (C21/H10); "Active work" omits the go-live decision + `flip_ac_live.sh` + the 06-03 observation gate (H12/H26).
- **Commission spec stale** — `HYDRA_STRATEGY_SPECIFICATION.md:995` still says **$2.50/leg**; IBKR configs have been **$1.15/leg since 2026-05-29** (`078049f`) (H13).
- **`CALYPSO_IBKR_MAX_RPS` comment drift** — comments say `=8`; actual default is **5** (`ib_client.py:746`, `strategy.py:377`, `:9291`) (H7/H32/H33/H34).
- **Version history stale** — `bots/hydra/__init__.py` omits the commission change (2026-05-29) and variant entry-time changes (H15).
- **Missing go-live docs** — no go-live flip section in CLAUDE.md (H27), no flip runbook in RUNBOOKS.md (H28), `broker-paper-smoke.timer` not listed in `deploy/README.md` (H25).
- **Misleading names/comments** — `_get_total_saxo_pnl` is broker-agnostic (H3); stale Fix #87 settlement comment (`strategy.py:10787`, H31); `data_recorder.py` module docstring missing v7/v8/v9 schema (H36); strategy spec doesn't document the Brandon stack / B-vs-C distinctly (H14).
- **Stale docstring** — `strategy.py:1590` documents the non-existent `_option_quote_is_realtime()` and falsely claims "callers gate on it" (H2/H29/H30) — same root as C-1.

---

## Dead code / config drift (CONFIRMED)

- **`broker-paper-smoke.timer`** armed for the past date `2026-06-01` (C18/H8). `Persistent=false` means it won't refire on a missed boot, but a clock rewind during a rollback could re-trigger the A auto-flip. Once today's go-live decision is made, this one-shot unit + `broker_paper_smoke.py` + `flip_a_live.sh` are spent — candidates for disable/removal.
- **`flip_ac_live.sh` orphaned** (GL-1) — present in `scripts/` with no scheduler.
- See the Appendix for the lower-severity dead-code sweep results across `shared/` + `bots/hydra/` and `deploy/` + `scripts/`.

---

## What the audit did NOT find (clean areas)

- **No new real-money risk** — branch is IBKR paper only; B stays dry-run. (Same caveat as every prior cycle.)
- **429 penalty box / circuit breakers** — the code lens found the penalty box live-safe (does not block safety-critical exits) and the breaker interaction sound. No high/critical findings.
- **conid pin + `/secdef` priming** — the Fix #14 regression restore (`ff95877`) verified correct; no reopened priming bug.
- **LST refresh / 410 / tiered tick** — no confirmed correctness bug.
- **claude_client pacing** — the 15s→35s pacing + backoff verified to keep the agent suite under Tier-1 ITPM (no concurrent-burst path) — important given the prior GCP-suspension incident.
- **ARGUS** — the false-FAIL fixes did not introduce false-negatives in the paths reviewed.
- **Known-deferred (NOT new):** POS-004 same-conid short+long settlement-merge (`strategy.py:~10586`) and `connection_timeout_seconds` not enforced as a hard cap remain open by prior decision.

---

## Appendix — complete raw register (155 findings, un-deduped)

The full per-agent findings (all severities incl. the 36 `info` and 21 `low` items, with evidence + recommendations) follow, grouped by severity. AUDIT = one of the original 20 domain agents; BACKFILL = one of the 16 gap-closing agents.


### CRITICAL

- **[AUDIT]** (code-bug/conf:high) **Market data availability gate incomplete: current_price set for Y/N availability** — `bots/hydra/base_strategy.py:4111`
  - _domain:_ Market-data-availability gating (commit 651a5cc + audit of subsequent commits through d83d50b)
  - _update_market_data() sets self.current_price = price when avail != 'Z', but should only set it when first character of 6509 flag is 'R'. When availability is 'Y' (Frozen-Delayed) or 'N' (Not-Subscribed), the code at line 4111 evaluates to True (since 'Y' != 'Z' and 'N' != 'Z'), allowing stale quotes to reach current_price which is then used in _calculate_strikes() for all entry strike calculations. The update_spx() function correctly refuses to update last_spx_update for these cases, but current_price is still updated in _update_market_data(), creating a logic mismatch.
  - _fix:_ Change line 4111 to: `first = avail[:1] if isinstance(avail, str) else None` followed by `if first == "R" or first is None: self.current_price = price` OR `if first not in ("Z", "Y", "N"): self.current_price = price`

- **[AUDIT]** (code-bug/conf:high) **Market data availability gate incomplete: current_vix set for Y/N availability** — `bots/hydra/base_strategy.py:4118`
  - _domain:_ Market-data-availability gating (commit 651a5cc + audit of subsequent commits through d83d50b)
  - Identical issue to line 4111 but for VIX. Sets self.current_vix = vix when avail != 'Z', allowing frozen/delayed/unentitled VIX quotes to reach current_vix which is used in strike OTM distance calculations (_calculate_strikes line 928-930), volatility regime detection, and other VIX-dependent logic.
  - _fix:_ Change line 4118 to apply the same first-character check as line 4111.

- **[AUDIT]** (code-bug/conf:high) **Market halt detection ignores Y/N availability flags** — `bots/hydra/base_strategy.py:5584`
  - _domain:_ Market-data-availability gating (commit 651a5cc + audit of subsequent commits through d83d50b)
  - _check_market_halt() only triggers the halt condition when avail.upper() == 'Z', but per commit 651a5cc spec, 'Y' (Frozen-Delayed) and 'N' (Not-Subscribed) should ALSO trigger halt detection. This allows the bot to continue trading when receiving frozen or unentitled data, falsely believing the market is operational.
  - _fix:_ Change line 5584 to: `first = avail[:1].upper() if isinstance(avail, str) else None` followed by `if first in ("Z", "Y", "N"):`

- **[AUDIT]** (code-bug/conf:high) **Option quote real-time gate mentioned but not implemented** — `bots/hydra/strategy.py:1590`
  - _domain:_ Market-data-availability gating (commit 651a5cc + audit of subsequent commits through d83d50b)
  - The commit message for 651a5cc states '_read_option_quote surfaces availability on the legs we trade + warns on a non-'R' option quote'. Line 1590 references '_option_quote_is_realtime()' as the mechanism for gating: 'Callers gate on it (see _option_quote_is_realtime)'. However, this function does NOT EXIST in the codebase (verified via grep). The code at lines 1594-1598 only WARNS about non-'R' quotes but places no actual gate to prevent using delayed/frozen/unentitled OPRA feeds for trade pricing. When _read_option_quotes_batch() results are consumed at lines 4054-4087 for strike scanning (MKT-020), there is NO check of the availability field to skip non-real-time quotes.
  - _fix:_ Implement _option_quote_is_realtime(quote) to return True only if quote.get('availability', '')[:1].upper() == 'R', and call it in all paths that consume option quotes for trade pricing. Add gates at lines 4065-4078 (MKT-020 candidate evaluation) and similar spots to skip quotes with non-real-time availability.

- **[AUDIT]** (code-bug/conf:high) **NULL margin_pct format crash in ORDER-004 buying-power gate (3 sites)** — `bots/hydra/base_strategy.py:5536, 5540, 5543`
  - _domain:_ Margin NULL handling and buying-power gate (df56441, 21c9ca0)
  - Commit df56441 correctly set margin_pct=None on the IBKR path (line 5489) and added a safe conditional format for the diagnostic log (line 5491: `_util_str = f"{margin_pct:.1f}%" if margin_pct is not None else "n/a"`). However, three subsequent f-string format statements still apply unconditional .1f% formatting to margin_pct. In live trading (not dry-run), when available margin is checked: (1) if available >= required, line 5543 crashes; (2) if available < required and not dry-run, line 5536 (warning log) and line 5540 (return message) both crash. The bug ALWAYS manifests on the first entry attempt in live mode because the margin gate is called unconditionally (strategy.py:5080).
  - _fix:_ Replace lines 5536, 5540, 5543 to use _util_str variable (already computed at line 5491) or recompute the conditional format at each site. Example for line 5543: `return True, f"BP OK: ${available:,.2f} (margin {_util_str} used, req ${required:,.0f} at {self.contracts_per_entry}c)"`.

- **[AUDIT]** (code-bug/conf:high) **flip_ac_live.sh date guard fails on 2026-06-02 — cannot flip unless smoke runs on same day** — `scripts/flip_ac_live.sh:26`
  - _domain:_ Dry-run→live-paper auto-flip guard (commits 578e4cf, ef6795a, b9e79a4)
  - The flip_ac_live.sh script is designed to flip A+C after the 2026-06-02 market close (21:30 UTC), but the date-sentinel guard checks for 'today's' dated PASS marker. The smoke test is one-shot scheduled for 2026-06-01 09:35 ET only (via broker-paper-smoke.timer), so it writes '2026-06-01 PASS'. When flip_ac_live.sh runs on 2026-06-02, it checks for '^2026-06-02 PASS', which does not exist in the sentinel file, causing the guard to abort. This is a fundamental date mismatch that prevents the intended go-live on 2026-06-02 unless a new smoke test runs on that date or the operator manually triggers one.
  - _fix:_ Either: (1) Create a 2026-06-02 one-shot timer for broker-paper-smoke (missing from deploy/), OR (2) Change the date guard logic to check for 'any PASS' not just 'today's PASS' (looser but acceptable since the ExecStartPost gate on 2026-06-01 already validated the path), OR (3) Document that operator must manually run `broker_paper_smoke.py --place` on 2026-06-02 morning to update sentinel before running flip_ac_live.sh. Option 1 is the cleanest: add deploy/broker-paper-smoke-20260602.timer (one-shot at 2026-06-02 09:35 ET) scheduling the same smoke test.

- **[AUDIT]** (code-bug/conf:high) **flip_ac_live.sh is orphaned — no systemd timer/service wiring** — `scripts/flip_ac_live.sh + deploy/`
  - _domain:_ DOC ACCURACY: docs/migration/*.md + CLAUDE.md coverage of go-live plan, gate status, and deployment state post-AUD4
  - Commit b9e79a4 (2026-06-02 09:39) added scripts/flip_ac_live.sh to flip variants A and C from dry_run:true to false after the 2026-06-02 close. The commit message states: 'Scheduled via a one-shot systemd timer at 21:30 UTC (17:30 ET — well past the 16:00 ET close + after-hours settlement).' However, NO timer file or service file for flip_ac_live exists in deploy/. The directory contains flip_a_live.sh (wired to broker-paper-smoke.service ExecStartPost=+) but flip_ac_live.sh is dead code — it will never execute unless invoked manually.
  - _fix:_ URGENT: Either (1) create deploy/flip_ac_live.timer for 21:30 UTC 2026-06-02 (or whatever the real scheduled time is) and document in PROJECT_STATUS.md / CLAUDE.md, OR (2) change the script to MANUAL and document operator how/when to invoke it. The current state leaves A+C in dry-run and the operator has no automated path to flip them live. Verify with user what the actual 06-02/06-03 plan is before 06-03 market open.

- **[BACKFILL]** (code-bug/conf:high) **Missing _option_quote_is_realtime() function blocks option quote gating** — `bots/hydra/strategy.py:1590`
  - _domain:_ Option quote real-time gate implementation verification
  - Line 1590 references a non-existent function _option_quote_is_realtime() in a comment: '(see _option_quote_is_realtime); a non-'R' option quote during RTH means a delayed/frozen/unentitled OPRA feed — do not price a trade off it.' The function is never implemented anywhere in the codebase, yet the comment documents it as the expected gating mechanism. Commit 651a5cc (2026-05-31 23:53:46) explicitly added this comment when surfacing the availability flag, but did not implement the gate.
  - _fix:_ Implement _option_quote_is_realtime(quote: Dict) -> bool function that checks if quote.get('availability', '')[:1].upper() == 'R'. Add it near _read_option_quote() definition (~line 1555). Then add calls in all quote-consuming paths to gate trading decisions.

- **[BACKFILL]** (code-bug/conf:high) **No callers of _read_option_quote() check availability before using quote for trade pricing** — `bots/hydra/strategy.py:2699, 6538`
  - _domain:_ Option quote real-time gate implementation verification
  - Two call sites of _read_option_quote() were identified: (1) Line 2699 (Fix #81, close long-leg worthless-option check) — fetches quote but only checks if bid <= 0, never checks availability. (2) Line 6538 (MKT-033, profit-gate for early close) — the most critical trading-decision path, fetches quote and computes profit but never checks if the quote is real-time. Both code paths proceed to trade logic regardless of availability='D'/'Z'/'Y'/'N'. This means a delayed/frozen/unentitled OPRA feed can drive pricing decisions on live trades.
  - _fix:_ Add availability gate at both call sites before using quote for trading: (1) At line 2700, after quote fetch, add: if quote and quote.get('availability', '')[:1].upper() != 'R': logger.warning(...); continue. (2) At line 6538, after quote fetch, add similar gate. For batch callers (MKT-020/MKT-022/etc at lines 4054, 4287, 4697, 7045, 7089), add availability checks after quote dict construction or skip non-realtime candidates.

- **[BACKFILL]** (code-bug/conf:high) **_read_option_quotes_batch() is used in critical trading paths without availability gating** — `bots/hydra/strategy.py:4054, 4287, 4697, 7045, 7089`
  - _domain:_ Option quote real-time gate implementation verification
  - Five critical call sites use _read_option_quotes_batch(): (1) Line 4054 in MKT-020 (call tightening) — iterates quotes at 4065-4090, uses short_bid/short_ask/long_bid/long_ask to compute credit without checking availability. (2) Line 4287 in MKT-022 (put tightening) — mirrors MKT-020, same issue. (3) Line 4697 (entry-window spread-width capture) — extracts bid/ask without checking. (4) Line 7045 (dry-run price simulation) — uses quotes without availability checks. (5) Line 7089 (batch entry-price update) — uses quotes for per-leg pricing. None of these check whether the batch quotes are real-time. A frozen/delayed option feed can silently influence credit thresholds, entry decisions, and position pricing.
  - _fix:_ After quotes = self._read_option_quotes_batch(...), add a filter to remove or flag non-realtime quotes. Option 1 (conservative): skip candidates where any leg has non-'R' availability: if short_quote.get('availability', '')[:1].upper() != 'R': continue. Option 2 (log + proceed): warn if availability is non-'R' and count as a data-quality degradation event. The gate should prevent credit calculations on frozen/delayed quotes.

- **[BACKFILL]** (code-bug/conf:high) **Empty positions list not cleared from Positions sheet (meic/hydra)** — `shared/logger_service.py:1569`
  - _domain:_ Google Sheets Position Snapshot Logging
  - When log_position_snapshot() is called with an empty positions list (all positions expired), the code at line 1569 checks `if all_rows:` and skips the resize and update calls entirely. This leaves stale position data visually on the sheet, violating the fix's stated intent from commit 21c9ca0 that 'the resize already grows AND shrinks to exact size removing stale rows'. The resize(1, 17) call would correctly shrink the sheet to header-only when end_row=1, but it is never executed when all_rows is empty.
  - _fix:_ Move the resize call outside the `if all_rows:` guard so it is always executed. The logic should be: (1) Always call resize(1 + len(all_rows), 17) to set the sheet to exactly header-row plus data rows (when all_rows is empty, this becomes resize(1, 17) to shrink to header-only). (2) Only call update(...) if all_rows is not empty, since there is no data to write. Alternative: Keep the if guard but add an else branch that calls resize(1, 17) explicitly when all_rows is empty.

- **[BACKFILL]** (config-drift/conf:high) **flip_ac_live.sh scheduled execution claims timer does not exist** — `scripts/flip_ac_live.sh:7-8`
  - _domain:_ flip_ac_live.sh systemd integration and date guard
  - The script header states 'Scheduled via a one-shot systemd timer at 21:30 UTC (17:30 ET — well past the 16:00 ET close + after-hours settlement)' but no corresponding systemd unit files (flip_ac_live.timer and flip_ac_live.service) exist in the deploy/ directory. The script is currently manual-only with no automation, contradicting the design intent documented in commit b9e79a4.
  - _fix:_ Either (a) create deploy/flip_ac_live.timer with OnCalendar=2026-06-02 21:30:00 America/New_York and a corresponding deploy/flip_ac_live.service unit, OR (b) update the script header to remove the timer claim and document that manual execution is required (e.g., 'Manual execution: /opt/calypso/scripts/flip_ac_live.sh after 2026-06-02 market close').

- **[BACKFILL]** (code-bug/conf:high) **flip_ac_live.sh line 25 uses local date, not ET — date guard will fail on 2026-06-03** — `scripts/flip_ac_live.sh:25`
  - _domain:_ flip_ac_live.sh systemd integration and date guard
  - Line 25 uses 'today=$(date +%F)' which returns the LOCAL system timezone date, not Eastern Time. The PASS sentinel written by broker_paper_smoke.py uses Python's date.today() (also depends on system TZ, no ET override in broker-paper-smoke.service). If flip_ac_live.sh is executed on 2026-06-03 (e.g., early morning UTC after midnight ET), the local date becomes 2026-06-03, but the sentinel file contains '2026-06-02 PASS', causing the grep check to ABORT: 'no fresh (2026-06-03) paper-smoke PASS sentinel'. The flip fails, A and C remain in dry-run, and 2026-06-03 morning entry is blocked.
  - _fix:_ Fix line 25 to use ET timezone: 'today=$(TZ=America/New_York date +%F)'. This matches the pattern established by bot_status.sh:14, argus/health_check.sh:L107,L110, and ensures the date guard checks the same timezone the sentinel was written in. Also add Environment='TZ=America/New_York' to broker-paper-smoke.service (line 14-15 region) so the Python date.today() in the smoke also uses ET consistently.

- **[BACKFILL]** (code-bug/conf:high) **flip_a_live.sh line 32 has same date guard timezone bug** — `scripts/flip_a_live.sh:32`
  - _domain:_ flip_ac_live.sh systemd integration and date guard
  - Identical to flip_ac_live.sh: line 32 uses 'today=$(date +%F)' without TZ override. This is called as ExecStartPost=+ on broker-paper-smoke.service (L23 of the service file), so it runs immediately after the smoke test. If the service runs late in the day or the system clock is in a non-ET timezone, the date mismatch bug will also block the A flip.
  - _fix:_ Apply same fix: change line 32 to 'today=$(TZ=America/New_York date +%F)'. Ensure both flip scripts use ET date consistently so the sentinel guard works across all timezones and times of day.

- **[BACKFILL]** (code-bug/conf:high) **Unconditional format of None margin_pct crashes on IBKR path — line 5536** — `bots/hydra/base_strategy.py:5536`
  - _domain:_ Margin NULL format crash in buying-power gate (ORDER-004)
  - The warning log on line 5536 unconditionally formats margin_pct with .1f% spec. On IBKR path, margin_pct is None (intentionally, since IBKR doesn't surface utilization). This causes TypeError: unsupported format string passed to NoneType.__format__. Crash occurs during insufficient BP warning log, before the False return on line 5537.
  - _fix:_ Create `_util_str = f'{margin_pct:.1f}%' if margin_pct is not None else 'n/a'` before the warning log (reusing the pattern from line 5491), then substitute {_util_str} in the format string on line 5536. This matches the safe pattern already applied at line 5491.

- **[BACKFILL]** (code-bug/conf:high) **Unconditional format of None margin_pct crashes on IBKR path — line 5540** — `bots/hydra/base_strategy.py:5540`
  - _domain:_ Margin NULL format crash in buying-power gate (ORDER-004)
  - The return tuple message on line 5540 unconditionally formats margin_pct with .1f% spec. Crashes with TypeError when margin_pct is None (IBKR path). This crash occurs when available < required, preventing the function from returning the error message and logging the rejection reason.
  - _fix:_ Use the pre-computed `_util_str` variable (see recommendation for line 5536) to safely format the margin percentage. Replace `{margin_pct:.1f}%` with `{_util_str}` in the format string on line 5540.

- **[BACKFILL]** (code-bug/conf:high) **Unconditional format of None margin_pct crashes on IBKR path — line 5543** — `bots/hydra/base_strategy.py:5543`
  - _domain:_ Margin NULL format crash in buying-power gate (ORDER-004)
  - The success-path return statement on line 5543 unconditionally formats margin_pct with .1f% spec. Crashes with TypeError when margin_pct is None (IBKR path). This affects the BP-OK message returned when available >= required.
  - _fix:_ Use the pre-computed `_util_str` variable to safely format the margin percentage. Replace `{margin_pct:.1f}%` with `{_util_str}` in the format string on line 5543.

- **[BACKFILL]** (config-drift/conf:high) **broker-paper-smoke.timer armed for past date (2026-06-01 09:35 ET, current date 2026-06-02)** — `deploy/broker-paper-smoke.timer:9`
  - _domain:_ systemd-timers | go-live-hardening
  - The one-shot systemd timer is configured with OnCalendar=2026-06-01 09:35:00 America/New_York. Since the current date is 2026-06-02, this timer is scheduled for a past date. While systemd will skip firing a past-date timer on the next systemd restart, if the VM system time is rewound during troubleshooting or rollback operations, the timer could unexpectedly fire again and trigger an unwanted conditional auto-flip of variant A to dry_run:false via the ExecStartPost=/opt/calypso/scripts/flip_a_live.sh hook.
  - _fix:_ IMMEDIATE ACTION REQUIRED: On the live VM, verify the timer's firing status via 'systemctl list-timers broker-paper-smoke.timer' or 'journalctl -u broker-paper-smoke.service'. If the smoke test has already passed and the flip decision has been made (evidenced by the flip_ac_live decision on 2026-06-02 09:39:44), either: (a) disable the timer with 'systemctl disable broker-paper-smoke.timer' and 'systemctl mask broker-paper-smoke.timer' to prevent accidental re-firing on time rewind, or (b) delete the timer file and restart systemd-timers. Document the action taken in the deployment runbook with timestamp evidence that the smoke passed on 2026-06-01 or 2026-06-02 and the decision was made to proceed with the flip_ac_live sequence. Add a post-deployment gate to verify no systemd-journal FAILED or warnings for the disabled timer.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **PROJECT_STATUS.md metadata stale: last-updated date, commit hash, and commits-ahead count all wrong** — `docs/migration/PROJECT_STATUS.md:5-7`
  - _domain:_ Documentation Staleness Audit (PROJECT_STATUS.md)
  - File header metadata no longer matches HEAD. Line 5 states 'Last updated: 2026-05-31' but current date is 2026-06-02. Line 6 states 'Last commit: 3d90d15' but current HEAD is d83d50b (12 commits ahead). Line 7 states 'Commits ahead: 141' but actual count is 153. These are easily-verified reference markers that any Claude session will check; their inaccuracy signals the entire file may be unreliable.
  - _fix:_ Update lines 5-7: **Last updated: 2026-06-02**, **Last commit: d83d50b** (go-live manual flip decision + pre-flip hardening), **Commits ahead of main: 153**. These should be updated in every commit that changes project state going forward.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **Line 17 describes stale auto-flip strategy; actual operator plan is manual flip_ac_live.sh for A+C on 2026-06-02 post-close** — `docs/migration/PROJECT_STATUS.md:17`
  - _domain:_ Documentation Staleness Audit (PROJECT_STATUS.md)
  - Current text claims: 'Variant A will auto-flip to dry_run:false (live paper) IF the one-shot Monday 2026-06-01 09:35 ET broker paper-smoke passes (flip_a_live.sh)'. This is now incorrect. Commits 578e4cf (2026-05-31) through b9e79a4 (2026-06-02 09:39) show the evolution: (1) the auto-flip strategy (flip_a_live.sh) was the original plan; (2) commit b9e79a4 introduced flip_ac_live.sh (lines 1-3: 'flip variant A (hydra) AND variant C'; lines 4: 'run AFTER the 2026-06-02 close'; lines 8: 'Scheduled via a one-shot systemd timer at 21:30 UTC'); (3) commit b9e79a4 message states 'Operator plan (chosen 2026-06-02): flip A and C...after today's close so they run LIVE paper from the 06-03 open'; (4) the paper-smoke PASSED on 2026-06-02 (message line: 'Today's paper smoke PASSED'). The file still references the OLD auto-flip plan as if it is current.
  - _fix:_ Replace line 17 auto-flip block with: '**Notes:** A/B/C run dry-run on 2026-06-02. B stays dry-run indefinitely. **Operator decided 2026-06-02 09:39 ET: manual go-live.** After today's 16:00 ET close + settlement, a one-shot systemd timer at 21:30 UTC (17:30 ET) will execute `/opt/calypso/scripts/flip_ac_live.sh` to flip BOTH A (hydra) and C (hydra_variant_c) from dry_run:true → dry_run:false. The script is guarded (broker /health connected + fresh 2026-06-02 smoke PASS sentinel), idempotent (only flips dry_run:true), and safe (any guard miss leaves A/C in dry-run). It restarts both units, verifies active + non-DRY-RUN, and Telegram-confirms. A and C will place REAL paper orders starting 2026-06-03 open across all entry windows (10:15/10:45/11:15/14:00 ET per regime cap).'

- **[BACKFILL]** (doc-inaccuracy/conf:high) **Line 23 'Now = Gate 2' section references stale date (Sunday 2026-05-31) and incomplete Monday observation window** — `docs/migration/PROJECT_STATUS.md:23`
  - _domain:_ Documentation Staleness Audit (PROJECT_STATUS.md)
  - Current line 23 states: 'today is Sunday 2026-05-31; the broker paper-smoke + windows are tomorrow, Monday 2026-06-01'. This is now 2 days out of date. The actual timeline: (1) paper-smoke was Monday 2026-06-01 as planned; (2) it PASSED on 2026-06-02 morning (per commit b9e79a4); (3) the Monday 2026-06-01 observation window is complete; (4) today is NOW 2026-06-02, the day of the manual flip decision and final pre-flip hardening commits (df56441, 21c9ca0, d83d50b); (5) the next gate is 2026-06-03 morning live observation (A+C in live paper, B dry-run). The file fails to document that the operator chose 2026-06-02 09:39 ET to flip at 21:30 UTC tonight, not defer to the Monday morning plan.
  - _fix:_ Replace line 23 with: '**Now = Gate 2 post-smoke, pending flip:** The paper-smoke PASSED on 2026-06-02 morning, validating the real order-path (1-contract round trip, full fills). Operator chose (2026-06-02 09:39 ET) to flip A + C to live paper tonight (21:30 UTC, post-close) via flip_ac_live.sh for live entry starting 2026-06-03 open (10:15/10:45/11:15 ET). B stays dry-run. Final pre-flip hardening completed: Sheets 429 throttle, B/C log spam, B catastrophic-max-loss display, entry slippage capture, Brandon close recording, margin NULL, ARGUS false-FAILs, /secdef priming, market-data gates, 429 penalty box, 410/tick handling, ITM settlement (commits df56441, 21c9ca0, d83d50b). Pending gate: 2026-06-03 morning live observation (A/C real paper, B dry-run, broker + watchdog + Telegram confirm).'

- **[BACKFILL]** (code-bug/conf:high) **Settlement metrics logging uses throttled 'Intraday' period instead of exempted 'End of Day'** — `bots/hydra/strategy.py:9392`
  - _domain:_ Settlement logging throttling during EOD reconciliation
  - The log_performance_metrics() method hardcodes period='Intraday' when calling trade_logger.log_performance_metrics(). This method is called from main.py:464 during post-settlement reconciliation (after market close, line 456 logs 'Settlement complete - sending daily summary...'). The throttle logic in logger_service.py:2615 exempts periods containing 'End', 'All', 'Weekly', 'Monthly', or 'Final' from the 60-second throttle, but 'Intraday' matches none of these, causing settlement writes to be throttled and delayed.
  - _fix:_ Add a `period` parameter to HydraStrategy.log_performance_metrics() method (default 'Intraday' for backward compatibility). When called from main.py:464 (post-settlement), pass period='End of Day' to bypass throttling. Also update the method call at main.py:696 (intraday heartbeat) to explicitly pass period='Intraday' for clarity. Add a comment at line 9392 explaining why settlement must not be throttled.


### HIGH

- **[AUDIT]** (test-gap/conf:high) **No test coverage for availability='Y' and 'N' edge cases** — `tests/test_hydra_init_broker_kwarg.py`
  - _domain:_ Market-data-availability gating (commit 651a5cc + audit of subsequent commits through d83d50b)
  - The test suite TestReadIndexPrice and TestReadOptionQuote only test the happy path with availability='R' or 'RpB'. There are no tests for the edge cases of availability='Y' (Frozen-Delayed), 'N' (Not-Subscribed), or 'Z' (Frozen) to verify that current_price/current_vix are NOT updated and that the staleness gate functions correctly for these cases. This test gap allowed bugs #1-3 to exist undetected.
  - _fix:_ Add test cases: test_update_market_data_skips_current_price_on_frozen, test_update_market_data_skips_current_price_on_not_subscribed, test_check_market_halt_detects_frozen_and_not_subscribed, test_read_option_quote_with_non_realtime_availability

- **[AUDIT]** (doc-inaccuracy/conf:high) **Docstring mentions non-existent _option_quote_is_realtime() function** — `bots/hydra/strategy.py:1590`
  - _domain:_ Market-data-availability gating (commit 651a5cc + audit of subsequent commits through d83d50b)
  - The docstring/comment at line 1590 says 'Callers gate on it (see _option_quote_is_realtime)' but this function does not exist and is not called anywhere in the codebase. This is misleading to future maintainers who may assume the gate exists.
  - _fix:_ Either: (a) implement _option_quote_is_realtime() and use it, or (b) update the comment to accurately describe the current (incorrect) behavior: 'Callers SHOULD gate on it but currently do NOT (TODO: implement _option_quote_is_realtime)'

- **[AUDIT]** (doc-inaccuracy/conf:high) **Misleading method name: _get_total_saxo_pnl is broker-agnostic** — `bots/hydra/base_strategy.py:4599`
  - _domain:_ ITM settlement reconciliation + fill-price sources (commits 1952c04, df56441)
  - Method _get_total_saxo_pnl() appears Saxo-specific but is broker-agnostic; it uses _read_open_positions() and _get_broker_pnl_for_entry() to fetch real P&L from any broker. Called in strategy.py at lines 1140, 2336, 4426, 4475 for both Saxo and IBKR paths. Misleading name will cause confusion during maintenance, especially when debugging MKT-018 early-close logic.
  - _fix:_ Rename to _get_total_broker_pnl or _get_total_unrealized_pnl. Non-breaking (internal method). Improves clarity for future maintainers.

- **[AUDIT]** (other/conf:high) **Settlement log_performance_metrics throttled, contradicts audit intent** — `bots/hydra/strategy.py:9392`
  - _domain:_ HYDRA-on-IBKR 0DTE SPX Bot: B/C CRITICAL Log Spam, B Max-Loss Display, Commission Rounding (Commits 21c9ca0, d83d50b)
  - Commit d83d50b added dashboard metrics throttling to reduce Sheets write quota stress. The implementation exempts certain period strings ('End', 'All', 'Weekly', 'Monthly', 'Final') from throttling, intending to never throttle settlement writes. However, log_performance_metrics() is hardcoded to period='Intraday' both at heartbeat and at settlement (main.py:464), meaning settlement metrics ARE throttled to 60s intervals. This contradicts the audit comment in d83d50b which states 'EOD / All-Time / Weekly / Monthly / Final periods are the important low-frequency settlement writes and are NEVER throttled.' If the last heartbeat occurred within 60s before 4 PM settlement, the final EOD performance metrics will be skipped.
  - _fix:_ Either: (1) add a period parameter to HydraStrategy.log_performance_metrics() to accept 'EndOfDay' or similar, defaulting to 'Intraday', then pass 'EndOfDay' from main.py:464 settlement call; OR (2) add a bypass flag to log_performance_metrics() to skip throttle checks on settlement calls. Verify settlement writes complete without skipping on a bot restart near 4 PM.

- **[AUDIT]** (code-bug/conf:high) **Empty positions list not cleared: stale data persists on Positions sheet** — `shared/logger_service.py:1569`
  - _domain:_ Google Sheets 429 retry + grid expansion (commit 21c9ca0, shared/logger_service.py)
  - When log_position_snapshot is called with an empty positions list for meic/hydra strategy, the code skips the resize+update operations because of the `if all_rows:` guard. This means the sheet is never resized back to just the header row (1 row). Old position data from the previous snapshot remains visible on Google Sheets until the next non-empty snapshot is written. The fix's commit message explicitly states 'resize both grows AND shrinks to end_row (removing stale rows)' but this only happens when all_rows is NOT empty.
  - _fix:_ Add an else clause to resize the sheet to 1 row (header only) when all_rows is empty: `else: self._sheets_call_with_timeout(worksheet.resize, 1, 17)`. This ensures stale position data is cleared immediately when all positions close. Alternatively, move the resize outside the `if all_rows:` block and make it conditional on whether sheet needs resizing (compare end_row to current row_count to avoid redundant API calls). The 10s timeout already prevents this from blocking, so performance impact is negligible.

- **[AUDIT]** (dead-code/conf:high) **flip_ac_live.sh orphaned: script created but NO corresponding systemd timer** — `scripts/flip_ac_live.sh, deploy/`
  - _domain:_ deploy/ + scripts/ dead code and redundancy audit (commits 38ac9d6..HEAD, HYDRA-on-IBKR go-live hardening)
  - Commit b9e79a4 (2026-06-02 09:39:44) added scripts/flip_ac_live.sh with an explicit header comment: 'Scheduled via a one-shot systemd timer at 21:30 UTC (17:30 ET — well past the 16:00 ET close + after-hours settlement)'. No .timer file was ever created for this script. The script is manually invoked only if someone runs it by hand. The commit message also states it's 'Scheduled' but the infrastructure (flip_ac_live.timer) does not exist, making the script unreachable as designed.
  - _fix:_ Either: (1) create deploy/flip_ac_live.timer (OnCalendar=2026-06-02 21:30:00 UTC, WantedBy=timers.target) if the automated flip after close is still desired for 2026-06-03, OR (2) delete scripts/flip_ac_live.sh and document the manual fallback command if the flip is operator-driven. If A+C were supposed to auto-flip after 2026-06-02 close, this is BROKEN infrastructure.

- **[AUDIT]** (config-drift/conf:high) **Stale hardcoded env var value in code comments (CALYPSO_IBKR_MAX_RPS=8 vs 5)** — `bots/hydra/strategy.py:377, bots/hydra/strategy.py:9291, shared/ib_client.py:746`
  - _domain:_ deploy/ + scripts/ dead code and redundancy audit (commits 38ac9d6..HEAD, HYDRA-on-IBKR go-live hardening)
  - Commit 56669ba (2026-06-01 10:32:24) changed CALYPSO_IBKR_MAX_RPS from 8 to 5 in deploy/calypso-broker.service due to empirical 429 failures at the 2026-06-01 10:45 ET entry window (ThreadPool+TLS+WAN jitter re-clusters requests so they arrive in bursts instead of evenly spaced). The commit updated the deploy file comments to say '5 (50% headroom)' but THREE code comments still reference the old '8 (20% headroom)' value. These are not active bugs but maintainability debt — developers reading the code will see conflicting statements about what the actual rate limit is.
  - _fix:_ Update the three stale code comments to reference 5 (50% headroom) to match the current deployment value and the commit 56669ba rationale. While not a functional bug, this drift causes future maintainers to be confused about which limit is actually in force.

- **[AUDIT]** (dead-code/conf:high) **broker-paper-smoke.timer one-shot armed for past date (2026-06-01 09:35 ET), now June 2** — `deploy/broker-paper-smoke.timer`
  - _domain:_ deploy/ + scripts/ dead code and redundancy audit (commits 38ac9d6..HEAD, HYDRA-on-IBKR go-live hardening)
  - The one-shot timer was armed to fire exactly once at 2026-06-01 09:35:00 America/New_York. The audit is running on 2026-06-02 23:59 UTC (June 2), so that date has passed. The service references flip_a_live.sh which flips A to live paper. With the armed time now in the past, the timer will not fire unless systemd's clock is rewound. The timer and service should either be (1) removed from deploy entirely if the 2026-06-01 smoke already fired successfully, or (2) kept for future reference/re-runs, but the 'armed for Mon 2026-06-01 09:35 ET' comment is now outdated.
  - _fix:_ Either: (1) delete deploy/broker-paper-smoke.timer and deploy/broker-paper-smoke.service (and scripts/broker_paper_smoke.py) if they were one-time test infrastructure for the 2026-06-01 smoke that already ran, OR (2) keep them for future reference/testing but update the Description and OnCalendar comment to clarify their status (e.g., 'ARCHIVED: one-shot smoke test armed for 2026-06-01, now past'). Leaving an orphaned past-date timer in production deploy files is confusing.

- **[AUDIT]** (doc-inaccuracy/conf:high) **PROJECT_STATUS.md describes outdated 06-01 auto-flip plan, not 06-02 manual flip plan** — `docs/migration/PROJECT_STATUS.md:17`
  - _domain:_ DOC ACCURACY: docs/migration/*.md + CLAUDE.md coverage of go-live plan, gate status, and deployment state post-AUD4
  - Line 17 states: 'Variant A will **auto-flip** to `dry_run:false` (live paper) IF the one-shot Monday 2026-06-01 09:35 ET broker paper-smoke passes (`broker-paper-smoke.timer` → `broker-paper-smoke.service`, whose `ExecStartPost=+/opt/calypso/scripts/flip_a_live.sh` flips ONLY A and restarts `hydra` on a clean PASS ... otherwise A stays dry-run.' This describes the old plan from commit ef6795a (2026-05-31 20:47). But commit b9e79a4 (2026-06-02 09:39) changed the plan: keep all three in dry-run on 06-02, then flip A+C to live after the close via flip_ac_live.sh. The doc was last edited at 6140b9f (2026-05-31 22:19), BEFORE the 06-02 decision, so it never captured the actual executed plan.
  - _fix:_ Update PROJECT_STATUS.md section 'CUTOVER EXECUTED' > Notes bullet to reflect the actual 06-02 decision: 'A/B/C run **dry-run** on 06-02. Operator decision (2026-06-02): after today's close, flip A and C to live paper for 06-03 session via flip_ac_live.sh (guarded on broker /health + fresh smoke-PASS sentinel). B stays dry-run. Scheduled/manual TBD.'

- **[AUDIT]** (doc-inaccuracy/conf:high) **PROJECT_STATUS.md has stale current-date context in CUTOVER note** — `docs/migration/PROJECT_STATUS.md:23`
  - _domain:_ DOC ACCURACY: docs/migration/*.md + CLAUDE.md coverage of go-live plan, gate status, and deployment state post-AUD4
  - Line 23 says: 'today is Sunday 2026-05-31; the broker paper-smoke + windows are tomorrow, Monday 2026-06-01'. This is now false — today is 2026-06-02 (as of HEAD commit d83d50b, 2026-06-02 15:18). The paper-smoke ran yesterday (06-01). This stale context could confuse an operator reading the doc on 06-02 or later.
  - _fix:_ Update line 23 to reflect current state: 'The broker paper-smoke ran on 2026-06-01 (PASSED). A/B/C remain dry-run through 06-02; A + C will flip to live paper after the 06-02 close for the 06-03 session.'

- **[AUDIT]** (doc-inaccuracy/conf:high) **PROJECT_STATUS.md metadata (Last updated, Last commit, Commits ahead) is stale** — `docs/migration/PROJECT_STATUS.md:5-7`
  - _domain:_ DOC ACCURACY: docs/migration/*.md + CLAUDE.md coverage of go-live plan, gate status, and deployment state post-AUD4
  - Line 5: 'Last updated: 2026-05-31' (true — commit 6140b9f). Line 6: 'Last commit on branch: `3d90d15`' (false — HEAD is d83d50b, 12 commits ahead). Line 7: 'Commits ahead of main: 141' (false — current is 153, per git log main..HEAD | wc -l). The 'Last updated' date is especially misleading — the doc was frozen at 2026-05-31 22:19 but 12 significant commits (including go-live decisions, audit fixes, pre-live capture fixes) landed after that. A reader will think they have current state when they don't.
  - _fix:_ Update lines 5-7: 'Last updated: 2026-06-02' / 'Last commit on branch: `d83d50b`' / 'Commits ahead of `main`: 153'. (Or rewrite to be dynamic, e.g., 'Run: git rev-parse HEAD; git log main..HEAD | wc -l; git log -1 --format=%ai -- docs/migration/PROJECT_STATUS.md')

- **[AUDIT]** (doc-inaccuracy/conf:high) **PROJECT_STATUS.md 'Active work' section doesn't mention the 06-02 go-live decision or flip_ac_live.sh** — `docs/migration/PROJECT_STATUS.md:189-191`
  - _domain:_ DOC ACCURACY: docs/migration/*.md + CLAUDE.md coverage of go-live plan, gate status, and deployment state post-AUD4
  - The 'Active work' section describes the state as 'Gate 2 paper-smoke watch' with no mention of the 06-02 decision to flip A+C after close. It says 'A Claude session should not start new code work unless the user explicitly requests it' which is good, but it fails to explain what IS happening: A+C are scheduled (or planned to be scheduled) to go live tomorrow (06-03). A reader will not know whether the 06-01 auto-flip succeeded, failed, or was overridden until they dig into commit logs.
  - _fix:_ Update 'Active work' section to: 'After the 06-01 paper-smoke PASSED, the operator decided (2026-06-02) to keep A/B/C simulating on 06-02, then flip A + C to live paper after the close for the 06-03 session (via flip_ac_live.sh, guarded on broker health + fresh smoke-PASS sentinel). B stays dry-run indefinitely. Execution plan: [manual / scheduled timer at 21:30 UTC] TBD — operator must confirm.'

- **[AUDIT]** (doc-inaccuracy/conf:high) **Commission spec stale: $2.50 documented but IBKR configs deployed at $1.15 since 2026-05-29** — `docs/HYDRA_STRATEGY_SPECIFICATION.md:995`
  - _domain:_ CONFIG + SPEC + VERSION-HISTORY consistency (HYDRA trading bot)
  - Spec states 'Commission = $2.50 per leg per transaction' (line 995), but post-IBKR cutover all live configs (template + B + C) set commission_per_leg to 1.15 since commit 078049f (2026-05-29). For variants B/C this is critical: at 10 contracts × 4 legs × 3-4 slots, the $2.50 vs $1.15 gap is ~$170-450/day, causing phantom fee bookings on dry-run evaluation.
  - _fix:_ Update docs/HYDRA_STRATEGY_SPECIFICATION.md line 995 to 'Commission = ~$1.15 per leg per transaction (IBKR post-2026-05-29; was Saxo $2.50). Cost: ~$0.65 IBKR Pro base + ~$0.45 CBOE index fee + ~$0.05 ORF/OCC/CAT.' Also add to MKT Rules section or commission explanation that this is display/P&L only and does NOT affect entry/stop logic.

- **[AUDIT]** (doc-inaccuracy/conf:high) **Strategy spec does not document Brandon stack or variant B/C behavior distinctly** — `docs/HYDRA_STRATEGY_SPECIFICATION.md:entire`
  - _domain:_ CONFIG + SPEC + VERSION-HISTORY consistency (HYDRA trading bot)
  - Spec treats HYDRA as a monolithic strategy but variants B/C run a fully different stack ('Brandon Trojan Horse' with GEX-aware strike adjustment, breach exits, defensive overlay, narrow 5/10pt spreads, delta-target strike selection, take-profit at 20% remaining credit) while variant A runs stock HYDRA. Spec mentions variants exist (line 9) but never documents their distinct rule sets, config parameters, or entry schedules. Config comments are detailed (strategy.brandon section, 30+ lines of explanation per variant), but the spec — the authoritative strategy document — is silent on Brandon entirely.
  - _fix:_ Add 'Brandon Trojan Horse Stack (Variants B/C)' section to spec after the base strategy overview (post 'Strategy Overview > Entry Schedule' section). Document: (a) Brandon-specific config keys and their runtime effect (strike_adjuster, breach_exit, defensive_overlay, narrow_spread, delta_target_strike_selection, take_profit), (b) how variant B/C entry times differ ([09:45,10:45,11:15,11:45] for B; [10:15,10:45,11:15] for C vs A's [10:15,10:45,11:15]), (c) narrow 5/10pt spread rule vs HYDRA's VIX×6.0 formula, (d) Polygon GEX integration, (e) that directional_pivot is disabled in B/C (Brandon GEX-breach plays that role). Cross-ref to docs/migration/HYDRA_STANDALONE_REWRITE_PLAN.md for Brandon history.

- **[AUDIT]** (doc-inaccuracy/conf:high) **Version history stale: variant entry-time changes (2026-05-13) and commission change (2026-05-29) not logged** — `bots/hydra/__init__.py:37-151 (Version History section)`
  - _domain:_ CONFIG + SPEC + VERSION-HISTORY consistency (HYDRA trading bot)
  - Last version entry is v1.27.2 (2026-05-05). Post-05-05 changes NOT in version history: (1) 2026-05-13: Variant B entry_times trimmed 7 slots → 4 ([09:31,10:15,10:45,11:15,11:45,12:15] → [09:45,10:45,11:15,11:45]), contracts_per_entry 15→10, per config comment (commit d085e77). (2) 2026-05-29: commission_per_leg set to 1.15 across all configs (commit 078049f). (3) 2026-05-31: doc-reconciliation pass (commit 6140b9f: 44 verified doc fixes). (4) 2026-06-01 onward: IBKR audit fixes A–F (market-data gating, 429 penalty box, LST refresh, conid pin, tiered tick, ITM settlement, entry-window burst mitigation, etc.). The __init__.py shows v2.0.0-rc.1 as 'current' but that version label is in the preamble (line 4), not in the Version History list. No new version entries have been created to track post-05-05 changes.
  - _fix:_ Extend Version History with new entries bridging v1.27.2 to v2.0.0-rc.1. Minimum: (1) v1.27.3+ entry for 2026-05-13 variant B trim + contracts cut (d085e77 data already in config comment); (2) note on 2026-05-29 commission_per_leg IBKR cutover (078049f); (3) consolidated v2.0.0-rc.1 entry (or intermediate v1.28.0) summarizing IBKR audit fixes A–F (651a5cc, 1952c04, 56669ba, 21c9ca0, df56441) with market-data gating, penalty-box safety, LST refresh, conid pinning, tick handling, ITM settlement fixes. Each entry should cite the relevant commit hashes.

- **[BACKFILL]** (code-bug/conf:high) **BUG-001: _update_market_data lines 4111/4118 only gate on 'Z', not 'Y'/'N'** — `bots/hydra/base_strategy.py:4111, 4118`
  - _domain:_ HYDRA-on-IBKR 0DTE SPX trading bot: Market-data availability Y/N codes test coverage
  - Lines 4111 and 4118 check `if avail != "Z"` before updating current_price and current_vix. However, per commit 651a5cc (which introduced this code) and per the ib_constants.py documentation, the 6509 availability flag has 5 meaningful first-character values: R=RealTime (proceed), D=Delayed (warn, proceed), Z=Frozen (reject), Y=Frozen-Delayed (reject), N=Not-Subscribed (reject). The code in MarketData.update_spx/update_vix at lines 722 and 775 correctly handles Z/Y/N as NOT real-time by returning early. But _update_market_data fails to mirror this logic: it will incorrectly update current_price/current_vix when availability='Y' or 'N', causing strike calculations and price-based stops to act on frozen or unentitled data.
  - _fix:_ Change lines 4111 and 4118 from `if avail != "Z":` to `if avail not in ("Z", "Y", "N"):`. This ensures current_price and current_vix are only updated when the broker confirms real-time (R) or delayed-but-usable (D) data, not frozen/unentitled (Z/Y/N).

- **[BACKFILL]** (code-bug/conf:high) **BUG-002: _check_market_halt line 5584 only checks 'Z', not 'Y'/'N'** — `bots/hydra/base_strategy.py:5584`
  - _domain:_ HYDRA-on-IBKR 0DTE SPX trading bot: Market-data availability Y/N codes test coverage
  - _check_market_halt is supposed to detect when the market is halted or the feed is degraded (GAP-E / F7.3). Line 5584 checks `if isinstance(avail, str) and avail.upper() == "Z"` to detect stale data as a halt signal. But per the 6509 spec, Y=Frozen-Delayed and N=Not-Subscribed are equally non-real-time and should also trigger the halt detection logic. This means if IBKR returns availability='Y' or 'N' during regular trading hours, _check_market_halt will NOT detect it as a potential halt/degradation condition, and the bot may incorrectly proceed to trade on a frozen/unentitled option feed.
  - _fix:_ Change line 5584 from `if isinstance(avail, str) and avail.upper() == "Z":` to `if isinstance(avail, str) and avail.upper() in ("Z", "Y", "N"):`. This ensures all three non-real-time codes are treated as halt/degradation indicators.

- **[BACKFILL]** (test-gap/conf:high) **BUG-003: No unit tests for _update_market_data with availability='Y' or 'N'** — `tests/test_hydra_init_broker_kwarg.py`
  - _domain:_ HYDRA-on-IBKR 0DTE SPX trading bot: Market-data availability Y/N codes test coverage
  - The test suite includes TestReadIndexPrice (4 tests) which verify that _read_index_price correctly returns availability flags. But there are ZERO tests for _update_market_data with availability='Y' or 'N', nor any tests for MarketData.update_spx/update_vix with Y/N codes. This means the regression from the two code bugs above is completely undetected by the test suite. When IBKR returns Y or N, the current_price/current_vix will be silently updated (due to bugs 001/002), but no test will catch it.
  - _fix:_ Add comprehensive unit tests to tests/test_hydra_init_broker_kwarg.py covering: (1) _update_market_data with availability='Y'/'N'/'Z' verify current_price/current_vix NOT updated (like 'Z'); (2) _update_market_data with availability='R'/'D' verify current_price/current_vix ARE updated; (3) _check_market_halt with all 5 codes (R/D/Z/Y/N) verify halt=False/False/True/True/True; (4) MarketData.update_spx/update_vix with Y/N directly verify last_spx_update is not advanced.

- **[BACKFILL]** (code-bug/conf:high) **_read_option_quotes_batch() does not include availability field; violates contract** — `bots/hydra/strategy.py:1620, 1665-1671`
  - _domain:_ Option quote real-time gate implementation verification
  - The docstring at line 1620 claims _read_option_quotes_batch() returns 'the same per-quote shape as :meth:`_read_option_quote`', but the actual code at lines 1665-1671 constructs quote dicts with only {bid, ask, last, mid, mark} — missing the 'availability' field that _read_option_quote now includes (line 1605). This is a contract violation. The batch function is used in critical MKT-020/MKT-022 paths, so batch quote consumers cannot check availability even if they wanted to.
  - _fix:_ Update _read_option_quotes_batch() to extract and include availability from rows: add '"availability": row.get("6509"),' at line 1671 (after mark). Update the docstring at line 1620 to explicitly list availability in the return-shape. Add test case in TestGetQuotesBatch to verify availability is populated.

- **[BACKFILL]** (code-bug/conf:high) **Commit 651a5cc added availability surfacing but left gating incomplete** — `bots/hydra/strategy.py (multiple), commit 651a5cc`
  - _domain:_ Option quote real-time gate implementation verification
  - Commit 651a5cc (2026-05-31 23:53:46) titled 'IBKR audit fix A: market-data availability now actually gates trading (#2/#4/#10/#11/#12)' added the availability field to _read_option_quote() and a warning at lines 1593-1598, with the explicit comment that 'Callers gate on it (see _option_quote_is_realtime)'. However, the commit did NOT implement _option_quote_is_realtime() and did NOT add gating logic to any caller. The index-price gate (SPX/VIX) was implemented (DATA-001 in base_strategy.py), but the option-quote gate was left as a stub. This is a critical incomplete fix.
  - _fix:_ Complete the gating that was promised in commit 651a5cc. Implement _option_quote_is_realtime(), add it to _read_option_quotes_batch() return dict, and add checks in all 7 call sites (2699, 6538, 4054, 4287, 4697, 7045, 7089) to skip or default to conservative pricing on non-'R' quotes.

- **[BACKFILL]** (comment/conf:high) **Misleading comment claiming resize shrinks stale rows** — `shared/logger_service.py:1563-1568`
  - _domain:_ Google Sheets Position Snapshot Logging
  - The comment at lines 1563-1568 states 'just resize-to-exact + one update. resize both grows AND shrinks to end_row (removing stale rows)', but this guarantee is only valid when all_rows is not empty. When all_rows is empty, the resize is never called, so stale rows are NOT removed. The comment should be updated to clarify that resize is conditionally called only when all_rows is not empty, or the code should be fixed to always call resize.
  - _fix:_ Either update the comment to accurately reflect the conditional behavior, or fix the code to always call resize. The fix is preferred per the meta-auditor guidance.

- **[BACKFILL]** (comment/conf:high) **flip_ac_live.sh header comment conflicts with operational plan** — `scripts/flip_ac_live.sh:2-4`
  - _domain:_ flip_ac_live.sh systemd integration and date guard
  - The header says 'One-shot manual go-live' (line 2) but then claims it is 'Scheduled via a one-shot systemd timer' (line 7). These are contradictory: if it is 'manual', it is not scheduled automatically. The commit b9e79a4 message also says 'Scheduled via a one-shot systemd timer at 21:30 UTC' but no timer was created, making the header misleading.
  - _fix:_ Update the header comment to clarify the actual execution model, e.g., 'One-shot manual flip script — must be run manually after the 2026-06-02 market close (or create a systemd timer: deploy/flip_ac_live.timer + deploy/flip_ac_live.service with OnCalendar=2026-06-02 21:30:00 America/New_York for automatic execution)'.

- **[BACKFILL]** (test-gap/conf:high) **No unit test coverage for margin_pct=None formatting on IBKR path** — `tests/test_hydra_init_broker_kwarg.py (TestReadAccountBalance)`
  - _domain:_ Margin NULL format crash in buying-power gate (ORDER-004)
  - The TestReadAccountBalance class in tests/test_hydra_init_broker_kwarg.py tests _read_account_balance but does not verify that _check_buying_power handles None margin_pct correctly. No test mocks balance.get('MarginUtilizationPct')=None and verifies the log output. This gap allowed the formatting bugs on lines 5536/5540/5543 to slip through—they were not caught by the suite despite being in the code path under test.
  - _fix:_ Add a unit test class TestCheckBuyingPowerIbkrPath in test_hydra_init_broker_kwarg.py. Mock _read_account_balance to return IBKR shape (MarginAvailableForTrading only, no MarginUtilizationPct). Test three scenarios: (1) sufficient BP (verify no crash, correct log); (2) insufficient BP dry-run (verify no crash, correct log); (3) insufficient BP live (verify no crash on line 5536, correct warning message). Use mocks to verify logger.info and logger.warning calls include safe strings, not crashes.

- **[BACKFILL]** (code-bug/conf:high) **flip_ac_live.sh references a missing flip_ac_live.timer systemd unit file** — `scripts/flip_ac_live.sh:1-10 (commit b9e79a4)`
  - _domain:_ systemd-timers | go-live-hardening
  - The flip_ac_live.sh script commit message (b9e79a4, 2026-06-02 09:39:44) explicitly states: 'Scheduled via a one-shot systemd timer at 21:30 UTC (post-close + post-settlement).' However, no corresponding deploy/flip_ac_live.timer file exists in the repository. The script is designed to run as a conditional auto-flip (only if broker is connected + today's smoke PASS sentinel exists), but without the timer file in git, there is no documented deployment artifact for the scheduled 21:30 UTC (17:30 ET) run on 2026-06-02. This creates a deployment gap: either (a) the timer was manually created on the VM outside git (undocumented), (b) the timer creation was deferred and the flip_ac_live.sh is meant to be run manually, or (c) the timer file was forgotten in the commit.
  - _fix:_ URGENT: Verify on the live VM whether flip_ac_live.timer exists and is active (systemctl status flip_ac_live.timer). If it does NOT exist but flip_ac_live.sh was meant to run at 21:30 UTC on 2026-06-02: (a) create deploy/flip_ac_live.timer with OnCalendar=2026-06-02 21:30:00 America/New_York (or the appropriate scheduled run date if it's recurring), add it to git, and deploy it to /etc/systemd/system/. If the flip_ac_live.sh already ran successfully on 2026-06-02 (as evidenced by commit b9e79a4's decision to commit it 'after today's close'), verify via systemd journal that it executed with exit 0 and that A and C were actually flipped to dry_run:false. If flip_ac_live.sh is intended to be MANUALLY run (not timer-scheduled), update its docstring to remove the timer reference and update deploy/README.md to clarify the deployment model. Do NOT leave a dangling script-to-timer reference in production.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **deploy/README.md does not list broker-paper-smoke.timer in installation instructions or active units table** — `deploy/README.md (lines 7-22, 52-101)`
  - _domain:_ systemd-timers | go-live-hardening
  - The deploy/README.md is the canonical deployment reference for which systemd units to install on the VM. The table at lines 7-22 lists 'Active units (install these on the VM)' and includes many timers (entry-window-watch, apollo, hermes, homer, clio, argus, db_backup) but DOES NOT include broker-paper-smoke.timer or broker-paper-smoke.service. Additionally, the installation bash script at lines 52-101 does not copy broker-paper-smoke.* files to /etc/systemd/system/. This creates ambiguity: either (a) broker-paper-smoke is not meant to be deployed on the live VM (only during pre-go-live setup), (b) the README is stale and needs updating, or (c) the timer was manually deployed outside the standard playbook.
  - _fix:_ UPDATE deploy/README.md: (1) Clarify the role of broker-paper-smoke.timer — is it a one-shot pre-deployment test that gets disabled after use, or a permanent fixture? (2) If it's temporary, add a new section 'Temporary deployment units for pre-go-live validation' and explain when to disable/remove it. (3) If it's permanent, add broker-paper-smoke to the 'Active units' table with justification, and include it in the bash installation script. (4) For flip_ac_live.timer (if it exists on the VM), follow the same clarification. Update the docstring in PROJECT_STATUS.md to reflect the final deployment model chosen.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **Active work section (line 191) omits flip_ac_live.sh execution and 2026-06-03 observation gate** — `docs/migration/PROJECT_STATUS.md:189-191`
  - _domain:_ Documentation Staleness Audit (PROJECT_STATUS.md)
  - Current text claims 'No code work is in flight' and 'A Claude session should not start new code work unless the user explicitly requests it.' This understates the current operational state. While no NEW code work should start, there IS active operational work: (1) flip_ac_live.sh is SCHEDULED for 21:30 UTC tonight (2026-06-02) — a critical one-shot flip that should be documented as the NEXT IMMEDIATE GATE; (2) the 2026-06-03 morning observation is a GATE 2.5 that must clear before Gate 3 (integration tests) and Gate 4 (5-day validation); (3) the file's own section on 'What's blocked / pending external input' (line 115) lists Gates 2-6, but the current status ('Now') fails to locate the reader precisely within that sequence (post-Gate-2-smoke, pre-Gate-2.5-flip). A Claude session reading this will not understand that there is an imminent, time-critical operational step (flip at 21:30 UTC) that should be mentioned prominently.
  - _fix:_ Rewrite lines 189-191 to: 'Code work: none in flight. Operations: ACTIVE. Flip_ac_live.sh scheduled 21:30 UTC (2026-06-02, tonight, 17:30 ET post-close). A/C will flip to live paper; B stays dry-run; entry windows 2026-06-03 open. Next gate: 2026-06-03 morning live observation (real A/C orders, broker + watchdog + Telegram confirm → clears Gate 2.5 → then Gate 3, 4). Do NOT start new code work. Monitor flip execution + morning live session.'

- **[BACKFILL]** (doc-inaccuracy/conf:high) **Missing go-live flip documentation in operator reference (CLAUDE.md)** — `CLAUDE.md (entire file — no section exists)`
  - _domain:_ HYDRA on IBKR — Go-Live Documentation
  - CLAUDE.md is the primary operator reference but contains no documentation of the flip_ac_live.sh procedure or the go-live timing for variants A and C. The 'Quick Reference Commands' section (line 594) shows how to start/stop services and restart for config changes, but omits the critical go-live flip procedure scheduled for 2026-06-02 21:30 UTC after market close. An operator reading this file will not find instructions on: (1) when to flip (21:30 UTC / 17:30 ET after the 16:00 ET close + after-hours settlement), (2) prerequisites (broker /health must show connected=true, and a FRESH dated (today's) paper-smoke PASS sentinel must exist at /opt/calypso/data/smoke/last_pass.txt), (3) the command to run (/opt/calypso/scripts/flip_ac_live.sh), or (4) verification steps (check journalctl for flip success, verify config.json has dry_run=false, confirm Telegram alert received).
  - _fix:_ Add a new section to CLAUDE.md titled 'Go-Live: Flipping A and C to Live Paper Trading' (suggested after the 'Deployment Workflow' section, before 'Troubleshooting'). The section should document: (1) **Timing**: 21:30 UTC (17:30 ET) on 2026-06-02 after market close + after-hours settlement, before premarket session next morning. (2) **Prerequisites** (guards in flip_ac_live.sh lines 15-29): (a) broker /health endpoint must show 'connected': true (verify with: `curl -s http://127.0.0.1:8788/health | grep connected`), (b) a FRESH (today-dated) paper-smoke PASS sentinel must exist at /opt/calypso/data/smoke/last_pass.txt (verify with: `cat /opt/calypso/data/smoke/last_pass.txt | grep $(date +%F)`). (3) **Command to run**: `sudo /opt/calypso/scripts/flip_ac_live.sh` (runs as root to restart services). (4) **What it does**: Flips variants A (hydra) and C (hydra_variant_c) from dry_run:true to dry_run:false, restarts both units, logs to journalctl, and sends a Telegram alert. B stays dry-run. (5) **Verification**: (a) Check systemctl status: `systemctl status hydra hydra_variant_c` (both should be active/running), (b) Check config.json has flipped: `cat /opt/calypso/bots/hydra/config/config.json | grep dry_run`, `cat /opt/calypso/bots/hydra/config/config_variant_c.json | grep dry_run` (both should show false), (c) Check journalctl for flip success: `journalctl -u hydra --since '10 minutes ago' | grep -E 'DRY RUN|live'` and same for hydra_variant_c, (d) Confirm Telegram alert received with title 'HYDRA A + C → LIVE paper trading ✅'. (6) **Rollback**: If the flip is incorrect, set dry_run:true in both config files and restart: `sudo -u calypso nano /opt/calypso/bots/hydra/config/config.json` and `sudo -u calypso nano /opt/calypso/bots/hydra/config/config_variant_c.json`, then `systemctl restart hydra hydra_variant_c`.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **Missing go-live flip runbook in docs/migration/RUNBOOKS.md** — `docs/migration/RUNBOOKS.md (no RB-X section exists for go-live flip)`
  - _domain:_ HYDRA on IBKR — Go-Live Documentation
  - RUNBOOKS.md contains 7 incident runbooks (RB-1 through RB-7) for failure scenarios, but does NOT include a runbook for the operator-driven go-live flip procedure. An operator consulting RUNBOOKS.md for guidance on how to execute the 2026-06-02 21:30 UTC flip will find nothing. The file is the canonical reference for 'Diagnostic + resolution steps for every IBKR-specific failure mode the operator might encounter' (line 3) and 'Telegram alert names are quoted exactly so an operator who sees an alert can grep this file for the matching runbook' (line 7). The missing go-live flip runbook breaks this contract — there is a critical operator action (the A+C flip) with no matching runbook section.
  - _fix:_ Add a new runbook section 'RB-8 — Flip A + C to live paper trading after close' to docs/migration/RUNBOOKS.md. Follow the existing RB format: **Symptom** (operator scheduled task / external gate), **Triage** (pre-flight checks / prerequisites), **Root cause** (N/A; planned action), **Resolution** (run the flip script with prerequisites verified), **Verification** (systemctl status, config state, journalctl, Telegram alert), **Post-mortem trigger** (any flip failure is a P0 — investigate before retrying). The runbook should include: (1) **Prerequisites** (Guard 1 & 2 from flip_ac_live.sh L15-29): broker /health connected && fresh today PASS sentinel. (2) **Timing**: 21:30 UTC (17:30 ET) on 2026-06-02 after market close. (3) **Command**: `sudo /opt/calypso/scripts/flip_ac_live.sh`. (4) **Verification checklist**: both A+C units active, both configs dry_run=false, no DRY-RUN log lines in last 60s, Telegram alert received. (5) **Failure recovery**: abort is safe (bots stay dry-run); if guards fail, troubleshoot why /health is down or why sentinel is missing, fix those issues, retry.

- **[BACKFILL]** (docstring/conf:high) **Docstring references non-existent function `_option_quote_is_realtime()`** — `bots/hydra/strategy.py:1590`
  - _domain:_ Docstring accuracy & implementation gap (HYDRA strategy.py option quote handling)
  - The docstring at lines 1588-1592 of `_read_option_quote()` states: 'Callers gate on it (see _option_quote_is_realtime); a non-R option quote during RTH means a delayed/frozen/unentitled OPRA feed — do not price a trade off it.' However, the function `_option_quote_is_realtime()` does not exist anywhere in the codebase (verified via grep across all Python files). This misleads future maintainers into believing a feature is implemented when it is not.
  - _fix:_ Remove the reference to the non-existent function. The docstring should accurately describe the actual behavior: (a) log a warning if availability is not 'R', but (b) do NOT actually gate/refuse trading on non-real-time data. Callers do not check the availability field at all.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **Docstring claim 'Callers gate on it' is false; no callers check availability** — `bots/hydra/strategy.py:1589`
  - _domain:_ Docstring accuracy & implementation gap (HYDRA strategy.py option quote handling)
  - The docstring promises that 'Callers gate on it' — implying that consuming code validates the availability flag before using a quote. However, a thorough review of all callers of `_read_option_quote()` found at lines 2699 and 6538 shows neither checks the 'availability' field returned by the method. Both callers extract only 'bid', 'ask', and other price fields; neither verifies 'availability' before pricing trades. This directly contradicts the docstring's claim.
  - _fix:_ Update the docstring (lines 1589-1592) to accurately describe the actual behavior: 'Log a DATA_QUALITY warning if 6509 is not R, but do not gate/block trading. Callers do not currently validate this flag.'

- **[BACKFILL]** (comment/conf:high) **Stale comment at Fix #87 block misleads about settlement P&L logic** — `bots/hydra/strategy.py:10787-10790`
  - _domain:_ Settlement Logic Comment Clarity - _process_expired_credits Post-ITM Fix
  - The inline comment states '_process_expired_credits assumes full credit kept (ClosePrice=$0), but options near ATM can settle at non-zero values' — this describes the OLD PRE-IBKR-AUDIT #5 behavior. As of commit 1952c04, the function now: (1) reads settlement_level at line 10693-10696, (2) calls _settlement_booked_pnl() at lines 10711-10712 and 10743-10744 to calculate actual ITM intrinsic (credit - settle_value), which can be NEGATIVE, and (3) checks `if booked != 0` at lines 10714 and 10746 to include these negative values. The comment fails to mention this settlement-aware calculation now happens INSIDE the loop, and that the != 0 guard at line 10777 is specifically needed to book negative settlement P&L from ITM shorts — which the comment does not explain.
  - _fix:_ Replace the misleading comment at lines 10787-10790 with a clarification that: (1) explains the function NOW calculates settlement-aware P&L via _settlement_booked_pnl (not just assumes full credit), (2) clarifies why != 0 is needed: 'an ITM-settled short produces a LOSS (credit - intrinsic), which must be booked even if negative; the old > 0 guard silently dropped ITM losses, overstating realized P&L', (3) adds a concrete edge case: 'Example: short call struck at 5600, opened for +$200 credit, SPX settles at 5650 (ITM), intrinsic=$500 → booked_pnl = $200 - $500 = -$300 (loss), which now counts toward realized_pnl instead of being ignored'.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **CALYPSO_IBKR_MAX_RPS=8 comment drift — actual value is 5** — `shared/ib_client.py:746`
  - _domain:_ CALYPSO_IBKR_MAX_RPS Hardcoded Value Drift Audit
  - Comment states 'the broker unit sets CALYPSO_IBKR_MAX_RPS=8 (20% headroom)' but the actual deployed value is 5 (50% headroom) as set in deploy/calypso-broker.service:32. This comment was written in commit 76657f7 (2026-05-31) when the value WAS 8. The value was changed to 5 in commit 56669ba (2026-06-01) to fix a production issue (429 burst at 10:45 ET entry window), but the code comments were never updated. The service file correctly documents the reason for the change (ThreadPool+TLS+WAN jitter re-clusters requests), but the Python code comment is stale.
  - _fix:_ Update shared/ib_client.py:746 from 'CALYPSO_IBKR_MAX_RPS=8 (20% headroom)' to 'CALYPSO_IBKR_MAX_RPS=5 (50% headroom, corrected on 2026-06-01 after 429-burst incident)' or similar. Add a cross-reference to the service file or the related commit/incident.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **CALYPSO_IBKR_MAX_RPS=8 comment drift in strategy.py line 377** — `bots/hydra/strategy.py:377`
  - _domain:_ CALYPSO_IBKR_MAX_RPS Hardcoded Value Drift Audit
  - Comment in the api_pacing_multiplier config docstring states '(CALYPSO_IBKR_MAX_RPS=8, below IBKR's 10 req/s/session)' but the actual deployed value is 5. This comment was added in commit 6140b9f (Saxo->IBKR migration reconciliation) and references the stale value 8. The correct context should reference the 50% headroom value and ideally mention the 2026-06-01 incident that prompted the change.
  - _fix:_ Update bots/hydra/strategy.py:377 to '(CALYPSO_IBKR_MAX_RPS=5, 50% headroom under IBKR's 10 req/s/session, set after 2026-06-01 429-burst fix)' or similar. Maintain consistency with the service file comment.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **CALYPSO_IBKR_MAX_RPS=8 comment drift in strategy.py line 9291** — `bots/hydra/strategy.py:9291`
  - _domain:_ CALYPSO_IBKR_MAX_RPS Hardcoded Value Drift Audit
  - Comment in the get_recommended_check_interval() docstring states 'the broker's IBKR rate gate (CALYPSO_IBKR_MAX_RPS=8, under IBKR's 10 req/s/session limit)' but the actual deployed value is 5. Same root cause as line 377: introduced in commit 6140b9f with value 8, not updated when the value was changed to 5 in commit 56669ba on 2026-06-01.
  - _fix:_ Update bots/hydra/strategy.py:9291 to '# the broker's IBKR rate gate (CALYPSO_IBKR_MAX_RPS=5, 50% headroom under IBKR's' or similar. Maintain consistency with shared/ib_client.py and the service file.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **CLAUDE.md Variant Comparison section missing day-of-week max_entries documentation** — `CLAUDE.md:380-402`
  - _domain:_ HYDRA-on-IBKR 0DTE SPX trading bot — Day-of-week max_entries capping feature audit
  - The Variant Comparison table (lines 386-390) lists entry schedules but does not mention the skip_weekdays or dow_max_entries config options. These features allow variants to skip trading on specific weekdays or cap the maximum number of base entries per weekday. The features are: (1) fully implemented in strategy.py, (2) documented in docs/HYDRA_STRATEGY_SPECIFICATION.md, (3) available as config keys, but (4) completely absent from the CLAUDE.md operator reference, creating a documentation-reality gap.
  - _fix:_ Add a new section to CLAUDE.md after the 'Variant Comparison' subsection documenting day-of-week entry capping: (1) Explain that config can include 'skip_weekdays' (list of weekday numbers 0=Mon..4=Fri to skip entirely) and 'dow_max_entries' (dict mapping weekday number to max base entry count). (2) Document the interaction with VIX regime: both caps apply, VIX regime runs second and may further restrict the day-of-week cap. (3) Show example configurations for when these might be useful (e.g., Friday={4:2} to cap Friday entries to 2). (4) Note that both features currently unused in production configs (A/B/C all leave these keys unset).

- **[BACKFILL]** (doc-inaccuracy/conf:high) **Module docstring incomplete — missing v7/v8/v9 schema documentation** — `shared/data_recorder.py:15-19`
  - _domain:_ data_recorder.py module docstring audit — v7/v8/v9 schema documentation
  - The module docstring opening paragraph only documents Schema v5 and v6 changes. It does not mention v7 (shadow_entries table), v8 (per-row contract count columns), or v9 (per-leg fill prices and mid-at-fill columns). The SCHEMA_VERSION constant is set to 9 (line 31), indicating the current schema is v9, but the docstring makes no mention of these three major schema versions. This forces readers to manually search the migration blocks or trace the code to understand what the schema versions contain.
  - _fix:_ Update lines 15-19 to document all schema versions from v5 through v9. The opening paragraph should be expanded to include: (1) v7: shadow_entries table for OTM-based strategy comparison (records what OTM selection would have chosen for retroactive analysis); (2) v8: per-row contracts column for 2-contract scaling (added to trade_entries, trade_stops, spread_snapshots, shadow_entries, daily_summaries); (3) v9: per-leg ground-truth execution prices (4 fill_price columns and 4 mid_at_fill columns for real entry slippage calculation and broker reconciliation). Keep the descriptions concise but ensure scope is clear to a reader seeing the docstring first.


### MEDIUM

- **[AUDIT]** (comment/conf:high) **Stale docstring/comments reference old 'Z' only behavior** — `bots/hydra/base_strategy.py:4107-4109`
  - _domain:_ Market-data-availability gating (commit 651a5cc + audit of subsequent commits through d83d50b)
  - The comment at lines 4107-4109 says 'only treat the SPX spot as current when the broker did NOT flag it stale — otherwise keep the previous current_price so strike calc / price-based stops don't act on a frozen quote.' and the code checks `if avail != "Z"`, but the commit spec and update_spx/update_vix functions now recognize 'Z', 'Y', and 'N' as non-real-time. The comment is technically accurate but the implementation is incomplete, creating a disconnect.
  - _fix:_ Update comment to reflect all three non-real-time cases: '...when the broker did NOT flag it real-time (Z/Y/N) — otherwise keep the previous current_price...'

- **[AUDIT]** (code-bug/conf:high) **Smoke test gate uses correct logic but main trading loop does not** — `scripts/broker_paper_smoke.py:132-138 vs bots/hydra/base_strategy.py:4111`
  - _domain:_ Market-data-availability gating (commit 651a5cc + audit of subsequent commits through d83d50b)
  - The smoke test (go-live guard) correctly checks `a[:1].upper() == "R"` for SPX, VIX, and option leg (lines 132-138). This is the correct gate. But the main trading loop in _update_market_data() uses `if avail != "Z"` which is weaker. This inconsistency means the smoke test can pass but the real trading loop will accept non-real-time data post-go-live.
  - _fix:_ Align _update_market_data() to use the same first-character check as the smoke test.

- **[AUDIT]** (code-bug/conf:high) **Zero-price check in slippage calculation skips valid 0.0 mids** — `bots/hydra/strategy.py:4719-4722`
  - _domain:_ ITM settlement reconciliation + fill-price sources (commits 1952c04, df56441)
  - The slippage calculation uses `if entry.short_call_fill_price and entry.short_call_mid_at_fill:` which will skip if mid_at_fill is exactly 0.0 (a legitimate price for worthless deep-OTM options). While mid_at_fill==0 is used to signal 'not captured', the distinction between 0.0 (real price) and 0 (not captured) is lost. In practice, affects only deep-OTM legs with zero market, which is rare but technically incorrect.
  - _fix:_ Change to explicit `is not None` checks: `if entry.short_call_fill_price is not None and entry.short_call_mid_at_fill is not None:`. Initialize mid_at_fill fields to None instead of 0.0 to distinguish real zero from not-captured.

- **[AUDIT]** (code-bug/conf:high) **Index price truthiness check in _update_market_data skips 0.0 prices** — `bots/hydra/base_strategy.py:4105, 4115`
  - _domain:_ ITM settlement reconciliation + fill-price sources (commits 1952c04, df56441)
  - The _update_market_data method checks `if price:` and `if vix:` before updating. If SPX or VIX were exactly 0.0 (a valid halt-state price), the update would be skipped. While SPX=0.0 is impossible in practice, this violates the explicit-is-not-None discipline established elsewhere in _read_index_price (line 1954-1960 uses `is not None`). Consistency issue.
  - _fix:_ Change to `if price is not None:` and `if vix is not None:`. Makes zero-value handling explicit and consistent. Pre-existing (not a regression from audit commits).

- **[AUDIT]** (other/conf:high) **Margin NULL storage is correct (database schema and data flow)** — `bots/hydra/base_strategy.py:5501-5506, shared/data_recorder.py:62`
  - _domain:_ Margin NULL handling and buying-power gate (df56441, 21c9ca0)
  - The df56441 commit correctly stores margin_pct as None in _last_margin_snapshot (line 5504) for IBKR. The DataRecorder schema (v5 migration, line 62) defines margin_utilization_pct as a nullable REAL column. When _record_entry_to_db writes to the DB, it passes entry_data.get("margin_utilization_pct"), which correctly serializes None as SQL NULL. No truncation or misinterpretation occurs. This part of the fix is sound.
  - _fix:_ None required; this is a confirmed safe implementation. Document the NULL behavior for operators: margin_utilization_pct will be NULL in trade_entries rows for IBKR live trades (not a data loss, but an indicator that IBKR does not surface utilization).

- **[AUDIT]** (other/conf:high) **Max-loss catastrophic display double-count fix is correct** — `bots/hydra/base_strategy.py:4798-4799, 4804, 4809, 4815-4816 (commit 21c9ca0)`
  - _domain:_ Margin NULL handling and buying-power gate (df56441, 21c9ca0)
  - Commit 21c9ca0 fixed a display-only bug where the catastrophic max-loss calculation double-counted a phantom stop-residual on entries flagged as BOTH *_side_expired AND *_side_stopped after a Brandon take-profit. The fix guarded residual additions with `and not *_expired`, preventing a closed/TP'd side from contributing to theoretical worst-case loss. Display-only; net P&L and commission were already correct. The fix is sound and addresses audit finding 2026-06-02.
  - _fix:_ No action required.

- **[AUDIT]** (comment/conf:medium) **Max-loss calculation comments could be clearer on expired vs stopped state interaction** — `bots/hydra/base_strategy.py:4787-4792`
  - _domain:_ HYDRA-on-IBKR 0DTE SPX Bot: B/C CRITICAL Log Spam, B Max-Loss Display, Commission Rounding (Commits 21c9ca0, d83d50b)
  - The audit comment in the fix explains the double-counting bug well ('Brandon TP that sets both *_side_expired AND *_side_stopped'), but it's not immediately clear HOW both flags can be simultaneously true. The comment should note that a stop can be executed in tick N, then a TP closes the side in tick N+1, resulting in both flags. The current comment doesn't explain the race condition or sequence of events.
  - _fix:_ Enhance the comment to include: 'This can occur if a stop is triggered in one heartbeat and a Brandon TP closes the side in the next heartbeat before the stop fully settles, resulting in both flags true simultaneously.'

- **[AUDIT]** (code-bug/conf:high) **broker_paper_smoke.py returns exit 0 even if sentinel write fails — guard still works but error masking** — `scripts/broker_paper_smoke.py:209-211`
  - _domain:_ Dry-run→live-paper auto-flip guard (commits 578e4cf, ef6795a, b9e79a4)
  - The sentinel file write (lines 203-210) is wrapped in a try-except that catches exceptions and logs them as WARN, but the function ALWAYS returns 0 (success) at line 211 regardless of whether the sentinel was written. If /opt/calypso/data/smoke/ directory cannot be created (e.g., permission error, disk full), the entire makedirs + write will fail, yet main() still returns 0. This causes systemd to see a successful smoke and run ExecStartPost (flip_a_live.sh), which then aborts on the missing sentinel file. While the flip guards are fail-safe (they abort when sentinel missing), the error is misrepresented in the exit code and logs.
  - _fix:_ Either: (1) Move the return 0 outside the exception handler and return 1 if sentinel write fails (treats missing sentinel as a smoke FAIL), OR (2) Log the sentinel write failure as WARN but keep return 0 (current behavior) and document that the flip guards are the safety net. Option 1 is stricter and prevents false-positive smoke successes; option 2 is what's currently coded. If choosing option 2, add a comment explaining that the guards catch this.

- **[AUDIT]** (code-bug/conf:high) **flip_ac_live.sh does not error-check flip_one return codes — partial flip possible without operator awareness** — `scripts/flip_ac_live.sh:61-62`
  - _domain:_ Dry-run→live-paper auto-flip guard (commits 578e4cf, ef6795a, b9e79a4)
  - The flip_ac_live.sh script calls flip_one twice (for A and C) but does NOT check the return codes. If flip_one returns 1 (error) for variant C (config edit or systemctl restart fails), the script continues anyway. The verification loop at lines 69-74 will detect this and report it in the Telegram message (showing active=failed or drline=<unexpected>), so the operator WILL see the partial failure, but there is no rollback and no exit-code indication that the script failed overall. This could result in A being flipped to live while C remains dry-run, contrary to the operator's intent.
  - _fix:_ Add error checking: `flip_one 'A' ... || { log 'ABORT: A flip failed'; exit 1; }` and similar for C. Alternatively, add a rollback loop at the end: if either unit is inactive or still has DRY-RUN lines, call systemctl restart on the opposite one with dry_run:true to revert. The current behavior is 'fail-visible' (operator sees it in Telegram) but not fail-safe (partial flip happens).

- **[AUDIT]** (other/conf:high) **flip_ac_live.sh timer is documented but not implemented — no systemd unit created** — `scripts/flip_ac_live.sh:7-8 (comment), but no deploy/flip_ac_live.timer found`
  - _domain:_ Dry-run→live-paper auto-flip guard (commits 578e4cf, ef6795a, b9e79a4)
  - The flip_ac_live.sh script's header comment states 'Scheduled via a one-shot systemd timer at 21:30 UTC (17:30 ET)' but no such timer file exists in the deploy/ directory. The script is functional if called manually, but the documented automated scheduling is missing. This means the operator must manually invoke flip_ac_live.sh or create the timer themselves, contrary to the script's design intent.
  - _fix:_ Create deploy/flip_ac_live.timer with a one-shot OnCalendar entry (e.g., 2026-06-02 21:30:00 UTC) that runs flip_ac_live.sh. Or clarify in the script comment that it is 'manual-only' and the operator must invoke it via systemctl start flip-ac-live.service or a direct shell call. Current state is ambiguous and could cause the operator to miss the flip.

- **[AUDIT]** (code-bug/conf:medium) **flip_ac_live.sh does not verify that both A and C are truly dry_run:true before flipping** — `scripts/flip_ac_live.sh:34-39 (flip_one function)`
  - _domain:_ Dry-run→live-paper auto-flip guard (commits 578e4cf, ef6795a, b9e79a4)
  - The flip_one function checks if the current dry_run value is 'True' before flipping (line 36), which provides idempotency (won't try to flip twice). However, if ONLY ONE of A or C is already False (already flipped), the script will flip only the other, leaving both in potentially inconsistent states. For example, if A is already live (dry_run:false) and the operator runs flip_ac_live.sh expecting both to flip, only C will be processed, and the Telegram message will accurately report this, but the operator might not notice the asymmetry.
  - _fix:_ Before entering the flip loop, verify that both A and C are currently dry_run:true. If not, log a WARNING and abort. Or modify the verification loop (lines 69-74) to check that both units are active AND both have zero/low DRY-RUN log lines AND both are confirmed non-dry-run in the config. The current check at lines 69-74 is sufficient to alert the operator, but a pre-check would be more explicit.

- **[AUDIT]** (comment/conf:high) **Stale comment on is_market_hours helper (lines 67-70)** — `services/argus/health_check.sh:67-70`
  - _domain:_ ARGUS health-check fixes (commits ec07d79, 3a77971)
  - The comment states 'The state-file heartbeat check + log-staleness check rely on this' (referring to is_market_hours), but the implementation now splits these: Check 2 (heartbeat) uses is_trading_session() while Check 6 (log-staleness) uses is_market_hours(). The comment accurately described the old behavior but is now outdated.
  - _fix:_ Update the comment on line 67-70 to clarify that is_market_hours() is used for log-staleness check only, while is_trading_session() is used for heartbeat check.

- **[AUDIT]** (doc-inaccuracy/conf:high) **db_backup.sh extraction justified but older inline bash pattern left in codebase comments** — `deploy/db_backup.service, scripts/db_backup.sh`
  - _domain:_ deploy/ + scripts/ dead code and redundancy audit (commits 38ac9d6..HEAD, HYDRA-on-IBKR go-live hardening)
  - Commit 6140b9f+later moved db_backup logic from inline ExecStart bash (in the service file) to scripts/db_backup.sh. The migration was well-motivated: the inline $(date +%%Y%%m%%d) expanded EMPTY in systemd exec context, causing dateless backups. However, the codebase retains vestigial references to the problem. The db_backup.service comments (lines 12-16) now explain why a script file is necessary, which is correct. The db_backup.sh comments (lines 5-22) repeat the same bug explanation. This is not dead code but redundant documentation that could confuse maintainers who fix one but not the other.
  - _fix:_ Clarify the redundancy: keep the detailed explanation in ONE place (suggest scripts/db_backup.sh header as the source of truth) and simplify deploy/db_backup.service comment to reference the script file comment ('Logic lives in scripts/db_backup.sh — see that file for detailed rationale'). Not a bug but reduces confusion for future maintainers.

- **[AUDIT]** (code-bug/conf:medium) **flip_a_live.sh && flip_ac_live.sh date check uses date +%F which is system-local, not ET** — `scripts/flip_a_live.sh:32, scripts/flip_ac_live.sh:25`
  - _domain:_ deploy/ + scripts/ dead code and redundancy audit (commits 38ac9d6..HEAD, HYDRA-on-IBKR go-live hardening)
  - Both flip scripts check for a fresh PASS sentinel by comparing today's date via $(date +%F), which returns the system's local timezone. The smoke script writes the sentinel with date.today().isoformat() (Python), which is also system-local. On a VM in a non-ET timezone, this creates a date mismatch: the broker_paper_smoke.py was armed to run at 'Mon 2026-06-01 09:35 ET' and write a sentinel dated YYYY-06-01. If the VM is in UTC (or another TZ), date +%F will NOT be 2026-06-01 at the same UTC timestamp. For example, if the smoke runs at 2026-06-01 14:35 UTC (= 09:35 ET), the Python date.today() in UTC is 2026-06-01, but if the VM is set to UTC and the sentinel says 2026-06-01, the flip script running later that day in any other TZ will fail the guard.
  - _fix:_ Explicit timezone handling: (1) modify broker_paper_smoke.py to write the sentinel with explicit ET date (from shared.market_hours.get_us_market_time().date()) instead of date.today(), and (2) modify flip scripts to compare against ET date using TZ=America/New_York date +%F instead of bare date +%F. This ensures the sentinel date and the guard date are in the same timezone (ET), matching the smoke timer schedule.

- **[AUDIT]** (doc-inaccuracy/conf:medium) **RUNBOOKS.md doesn't document the flip_ac_live.sh procedure or post-flip verification** — `docs/migration/RUNBOOKS.md`
  - _domain:_ DOC ACCURACY: docs/migration/*.md + CLAUDE.md coverage of go-live plan, gate status, and deployment state post-AUD4
  - RUNBOOKS.md was last updated before the flip_ac_live.sh introduction (commit b9e79a4, 2026-06-02). It provides incident runbooks for session-lost, auth failures, etc., but has NO runbook for: (1) monitoring/manually running flip_ac_live.sh, (2) verifying A+C flipped to live correctly, (3) what to do if flip fails (variants stuck in dry-run past 06-03 open), (4) reverting a flip (manual flip back to dry-run). An operator on duty 2026-06-03 morning might not know what 'A and C flipped to live paper' means or how to verify it.
  - _fix:_ Add a new runbook RB-8 or RB-X: 'Variants A/C flipped to live paper' covering: (1) confirmation checks (`systemctl status hydra hydra_variant_c`; grep DRY RUN in journalctl; check config.json dry_run field), (2) first-trade verification (watch the 10:15 entry window for live orders in real account statement vs. dry-run logs), (3) rollback if needed (manual flip back to dry_run:true + restart). Also update the entry-window watchdog runbook to note that it now monitors LIVE trades for A/C on 06-03+.

- **[AUDIT]** (doc-inaccuracy/conf:medium) **CLAUDE.md doesn't document the flip_ac_live.sh plan or current live-paper state** — `docs/migration/CLAUDE.md (entire 'Deploy' / 'Active Gates' section missing)`
  - _domain:_ DOC ACCURACY: docs/migration/*.md + CLAUDE.md coverage of go-live plan, gate status, and deployment state post-AUD4
  - CLAUDE.md is the operator reference and correctly says 'Account: **paper only** on this branch' (line 45). However, it doesn't document: (1) that A+C are flipping to live paper on 06-03 (vs. staying dry-run), (2) how to verify the flip succeeded, (3) how to manually flip or revert, (4) the flip_ac_live.sh guards (broker /health + smoke-PASS sentinel). An operator reading CLAUDE.md to understand 'what state am I in' will miss the critical fact that A+C go live tomorrow.
  - _fix:_ Either (1) add a 'Current State (2026-06-02)' note at the top of CLAUDE.md pointing to PROJECT_STATUS.md for current project state, OR (2) add a brief section in CLAUDE.md noting: 'As of 2026-06-02, variants A and C are scheduled to flip from dry-run to live paper after today's close for the 2026-06-03 session. See PROJECT_STATUS.md for current gate status + execution plan.' This keeps CLAUDE.md focused on mechanics while delegating state to PROJECT_STATUS.md.

- **[AUDIT]** (comment/conf:high) **flip_ac_live.sh has unverified systemd timer scheduling claim** — `scripts/flip_ac_live.sh:4-7 + commit b9e79a4 message`
  - _domain:_ DOC ACCURACY: docs/migration/*.md + CLAUDE.md coverage of go-live plan, gate status, and deployment state post-AUD4
  - The script and commit claim: 'Scheduled via a one-shot systemd timer at 21:30 UTC (17:30 ET — well past the 16:00 ET close + after-hours settlement)'. However, there is no timer file in the repo. The claim could be: (a) the timer was never created, (b) it's created via a separate deployment step not in the repo, (c) it's planned but not yet deployed, (d) the script is meant to be invoked manually. The ambiguity means the operator won't know whether to wait for automation or act manually.
  - _fix:_ Clarify in flip_ac_live.sh comment (line 2-8) whether this is: (1) AUTOMATED: 'This script is invoked automatically by flip_ac_live.timer (see deploy/flip_ac_live.timer) at 21:30 UTC 2026-06-02.' (and create the timer file), OR (2) MANUAL: 'This script is run manually by the operator after the 2026-06-02 close via: sudo /opt/calypso/scripts/flip_ac_live.sh'. Also update PROJECT_STATUS.md to match.

- **[AUDIT]** (doc-inaccuracy/conf:medium) **No documentation of what happens if flip_ac_live.sh fails or is skipped** — `docs/migration/ (PROJECT_STATUS.md, RUNBOOKS.md, CLAUDE.md)`
  - _domain:_ DOC ACCURACY: docs/migration/*.md + CLAUDE.md coverage of go-live plan, gate status, and deployment state post-AUD4
  - flip_ac_live.sh has guards: broker /health must be connected, smoke-PASS sentinel must be fresh (today's date). If either guard fails, the script exits with no-op and A+C stay dry-run. If the timer never fires (no wiring), A+C stay dry-run. If the operator forgets to run it manually, A+C stay dry-run. But there is NO documentation of what the correct behavior is post-06-03 open if A+C are still dry-run (is this a failure requiring intervention, or a safe fallback?). The operator will be unsure what state is correct.
  - _fix:_ Add to PROJECT_STATUS.md Gates section: 'If flip_ac_live.sh fails to run (timer didn't fire, broker /health down, or smoke-PASS stale), variants A+C remain dry-run on 06-03. This is a SAFE fallback (no unintended live orders), but the operator should verify the reason and manually flip if appropriate. To flip manually: ssh calypso-bot; sudo /opt/calypso/scripts/flip_ac_live.sh and verify via journalctl / systemctl status.'

- **[AUDIT]** (doc-inaccuracy/conf:high) **Module docstring incomplete: missing v7/v8/v9 schema documentation** — `shared/data_recorder.py:1-19`
  - _domain:_ Docstring + Inline-Comment Accuracy Audit (IBKR Migration, commits 38ac9d6..HEAD)
  - The module-level docstring lists schema versions v5 and v6 (lines 15-19) but omits v7, v8, and v9. The file header says 'Schema v6 adds: per-leg broker (IBKR; originally Saxo) bid/ask...' and stops there. Meanwhile, v9 was added in commit df56441 (2026-06-02) with significant changes for live-capture (per-leg fill prices, mid-at-fill for slippage). Inline comment at lines 107-110 correctly documents v9, and v8/v7 are documented inline at lines 122/96, but the module docstring is stale.
  - _fix:_ Update the module docstring to either: (a) list all versions v5–v9 with their changes, or (b) remove the version-specific details and refer readers to the inline comments at the migration definitions. Option (b) is simpler: 'Schema migrations: see MIGRATION_V*_SQL blocks below for version-by-version details.'

- **[AUDIT]** (comment/conf:high) **Misleading inline comment about _process_expired_credits assumptions** — `bots/hydra/strategy.py:10787-10792`
  - _domain:_ Docstring + Inline-Comment Accuracy Audit (IBKR Migration, commits 38ac9d6..HEAD)
  - The comment says '_process_expired_credits assumes full credit kept (ClosePrice=$0), but options near ATM can settle at non-zero values.' This describes OLD behavior from before audit fix 1952c04. Post-fix, _process_expired_credits now calls _settlement_booked_pnl() (line 10711-10713) which explicitly handles ITM settlement: compares SPX settlement level vs short strike, books credit - intrinsic if ITM (lines 10635-10657). The comment makes it sound like the assumption is still active, when in fact the ITM handling is now the MAIN path. The comment is describing a legacy safety check (Fix #87 / Saxo cross-check) that is now a no-op on IBKR (line 10816 returns immediately).
  - _fix:_ Clarify the comment to: 'Fix #87 (Saxo-era): cross-check expired P&L against actual settlement report. On IBKR, this verification is a no-op (no real-time closed-positions report), so the ITM settlement check in _settlement_booked_pnl (audit #5) is the sole accuracy mechanism.' Or shorten it to just call _verify_settlement_pnl_from_saxo() without explaining assumptions.

- **[AUDIT]** (doc-inaccuracy/conf:high) **Strategy spec lists stale variant entry schedules (v1.26.0 outdated)** — `docs/HYDRA_STRATEGY_SPECIFICATION.md:43-49 (Variant Comparison & Directional Pivot section header)`
  - _domain:_ CONFIG + SPEC + VERSION-HISTORY consistency (HYDRA trading bot)
  - The preamble (line 43-49) notes 'variants B and C run a new **directional pivot strategy**' and references that 'Variant A is the unchanged control' (line 44). But the Entry Schedule table (line 154-159) shows only a single schedule (1=10:15/DROPPED, 2=10:45, 3=11:15, 6=14:00) without noting that variant B actually has 4 slots ([09:45,10:45,11:15,11:45]) and C has 3 slots ([10:15,10:45,11:15]) — different from the 'canonical' schedule shown. This conflates the base-HYDRA (variant A) schedule with the overall 'current' spec.
  - _fix:_ Clarify Entry Schedule section: add note that the 'Current schedule' table applies to variant A (control). Add a second table 'Variant B/C Schedules' showing B's 4-slot dense grid and C's 3-slot morning grid, with rationale (B tests entry cadence; C is sparser control; both tests Brandon GEX features).

- **[AUDIT]** (config-drift/conf:medium) **Config variant B comment references outdated incident date and cost (2026-05-07, $8.7K)** — `bots/hydra/config/config_variant_b.json:104`
  - _domain:_ CONFIG + SPEC + VERSION-HISTORY consistency (HYDRA trading bot)
  - Line 104 states: '2026-05-07 incident: tightener walked B's E#5/E#6 puts from 125pt OTM (safe) to 35-40pt OTM (right on the 7330 GEX wall) chasing credit; when SPX broke the wall at 13:26 the resulting 4 breach exits cost ~$8.7K.' This is a real incident cited to justify disable_progressive_tightening=true on variant B. However, without verification it's unclear if this incident is still live or if it was subsequently fixed. The config comment should cite the exact PR/issue/commit that remediated this pattern if post-dated.
  - _fix:_ Append to the comment: 'See [GitHub issue/PR #NNN] for the breach-exit GEX-wall tuning that prevents this pattern going forward.' Or note: 'This incident occurred during shadow-only GEX testing (v1.27.0-1.27.1); after v1.27.1 Brandon's breach exit is LIVE and the GEX adjuster prevents SKIPping into walls by design.' Either way, clarify whether the $8.7K incident is an open risk or a resolved one.

- **[BACKFILL]** (docstring/conf:high) **BUG-004: Docstring at line 708 only mentions 'Z', not 'Y'/'N'** — `bots/hydra/base_strategy.py:708`
  - _domain:_ HYDRA-on-IBKR 0DTE SPX trading bot: Market-data availability Y/N codes test coverage
  - The docstring for MarketData.update_spx says 'When it is "Z" the broker is serving a STALE tick...', but the code at line 722 also rejects 'Y' (Frozen-Delayed) and 'N' (Not-Subscribed). The docstring is incomplete and could mislead future maintainers into thinking only 'Z' is handled.
  - _fix:_ Update docstring at line 708 to say: 'When it is "Z", "Y", or "N" the broker is not serving real-time data (frozen, frozen-delayed, or unentitled) — we record the flag but DO NOT advance `last_spx_update`...'

- **[BACKFILL]** (doc-inaccuracy/conf:high) **Docstring mismatch: _read_option_quotes_batch claims to return availability but doesn't** — `bots/hydra/strategy.py:1620, 1639`
  - _domain:_ Option quote real-time gate implementation verification
  - Line 1620 states: 'Returns ``{instrument_id: {"bid","ask","last","mid","mark"}}`` — the same per-quote shape as :meth:`_read_option_quote`'. But _read_option_quote now includes 'availability', making this docstring inaccurate. Line 1639 repeats the false contract: '``{instrument_id: {bid, ask, last, mid, mark}}``'. This is a documentation-as-contract issue that could mislead future refactorers.
  - _fix:_ Update line 1620-1621 and line 1639 to explicitly state that _read_option_quotes_batch does NOT currently include availability (even though _read_option_quote does), OR update the implementation to include it (recommended per the critical findings above).

- **[BACKFILL]** (test-gap/conf:high) **No test coverage for empty positions snapshot** — `shared/logger_service.py:1434-1625`
  - _domain:_ Google Sheets Position Snapshot Logging
  - The log_position_snapshot() method has no unit test coverage verifying that it correctly handles the empty positions case. When all positions expire, the sheet should be resized to header-only, but there is no test confirming this behavior. This gap allowed the bug to slip through the audit.
  - _fix:_ Add a unit test in tests/test_logger_service.py (new file or existing if present) that:
1. Mocks the Google Sheets API
2. Calls log_position_snapshot(positions=[]) for meic/hydra strategy
3. Verifies that worksheet.resize(1, 17) is called to shrink to header-only
4. Verifies that worksheet.update() is NOT called (no data rows to write)

- **[BACKFILL]** (other/conf:high) **broker-paper-smoke.service lacks Environment TZ setting for sentinel date consistency** — `deploy/broker-paper-smoke.service:14`
  - _domain:_ flip_ac_live.sh systemd integration and date guard
  - The service writes a sentinel file with date.today() in broker_paper_smoke.py:207, but the service unit does not set Environment=TZ=America/New_York. This leaves the date written by the smoke dependent on the system timezone. Although the smoke and flip scripts both use their system TZ, a deployment on a non-ET system would write the sentinel in UTC or Pacific time, causing the date guard in flip_a_live.sh/flip_ac_live.sh to fail even with the fixes applied.
  - _fix:_ Add 'Environment=TZ=America/New_York' to broker-paper-smoke.service [Service] section (after line 14) so the Python smoke test writes the sentinel in ET, matching the flip scripts' expectation when they use ET date.

- **[BACKFILL]** (config-drift/conf:medium) **flip_a_live.sh (conditional auto-flip) references broker-paper-smoke.service ExecStartPost hook but ExecStartPost is wired only in the service file, not the timer** — `deploy/broker-paper-smoke.service:23`
  - _domain:_ systemd-timers | go-live-hardening
  - The broker-paper-smoke.service file defines ExecStartPost=+/opt/calypso/scripts/flip_a_live.sh, which runs ONLY if ExecStart exits 0 (i.e., paper smoke PASS). This conditional auto-flip depends entirely on systemd's ExecStart exit code semantics. However, there is a subtle sequencing risk: the timer triggers the service, systemd runs ExecStart (broker_paper_smoke.py --place), waits for exit code, and if 0, runs ExecStartPost. If the systemd timer is fired but ExecStart takes a long time or hangs, the ExecStartPost waits. If the VM loses connectivity or crashes during that window, the flip may be incomplete, leaving A in dry_run but the smoke sentinel written (creating a false 'pass' state). The flip_a_live.sh script includes Guard 1.5 to re-check the sentinel, but this defense assumes the script is run fresh each time.
  - _fix:_ This is ACCEPTED as design: the sentinel + guard structure is intentional defense-in-depth. However, update the service file's comment to explicitly note that ExecStartPost is conditional on ExecStart exit 0, and add a log-monitoring runbook item to detect hung ExecStart (systemd will eventually timeout, but that should trigger an alert). Verify on the live VM that broker-paper-smoke.service ran to completion on 2026-06-01 or 2026-06-02 and that flip_a_live.sh was executed (check systemd journal: 'journalctl -u broker-paper-smoke.service' and 'journalctl -u hydra.service' for the restart).

- **[BACKFILL]** (doc-inaccuracy/conf:medium) **PROJECT_STATUS.md last updated 2026-05-31, does not reflect the 2026-06-02 flip_ac_live.sh go-live decision or flip_ac_live.timer deployment status** — `docs/migration/PROJECT_STATUS.md:5-6, 189-193`
  - _domain:_ systemd-timers | go-live-hardening
  - PROJECT_STATUS.md is marked 'Last updated: 2026-05-31' but the branch has received multiple commits after that date, including b9e79a4 on 2026-06-02 09:39:44 which introduced the flip_ac_live.sh script and documented an operator decision to flip A+C to dry_run:false after the 2026-06-02 close. The file states 'Active work (none — branch is deployed and in the Gate 2 paper-smoke watch)' but does not mention the 2026-06-02 decision or the flip_ac_live.timer scheduled run. This stales the project status document and could mislead a future Claude session about what has been decided and what is in flight.
  - _fix:_ UPDATE docs/migration/PROJECT_STATUS.md immediately to record: (1) 'Last updated: 2026-06-02'; (2) new subsection under 'What's blocked / pending external input': 'Gate 4 — flip_ac_live auto-execution at 21:30 UTC 2026-06-02' with status (pending/in-progress/done), timestamp, and verification (systemd journal or dry_run flag check). (3) If flip_ac_live already executed, record the outcome (A/C flipped to dry_run:false, verified active + non-DRY-RUN in journal). (4) Confirm the flip_ac_live.timer was deployed to the VM and that the 21:30 UTC run is scheduled or has already completed.

- **[BACKFILL]** (doc-inaccuracy/conf:medium) **Test suite count not updated post-AUD4; line 8 says 'grew past 953' but gives no current number** — `docs/migration/PROJECT_STATUS.md:8`
  - _domain:_ Documentation Staleness Audit (PROJECT_STATUS.md)
  - Line 8 states: 'The suite is deterministic at any wall-clock hour (the intraday-OHLC tests were time-gated; fixed 2026-05-28).' The file references 953 as a baseline (the 'old 953 snapshot') but does not provide the current count after AUD4 (2026-05-31) and the subsequent 12 commits. A Claude session trying to verify the test count will find the guidance unclear ('grew past the old 953 snapshot after the 2026-05-31 30-agent audit') and unable to determine what the expected count is NOW.
  - _fix:_ Update line 8 to give the exact current test count. Run `python -m pytest tests/ -q --ignore=tests/test_dashboard 2>&1 | tail -1` and insert the result (e.g., 'X passed, Y skipped').

- **[BACKFILL]** (config-drift/conf:medium) **Missing systemd timer automation for flip_ac_live.sh** — `deploy/ (missing deploy/flip_ac_live.timer and deploy/flip_ac_live.service)`
  - _domain:_ HYDRA on IBKR — Go-Live Documentation
  - The commit message for b9e79a4 states that flip_ac_live.sh is 'Scheduled via a one-shot systemd timer at 21:30 UTC (17:30 ET — well past the 16:00 ET close + after-hours settlement)'. However, there is NO systemd timer or service file in the deploy/ directory to actually schedule and execute this script. The script requires manual operator invocation or relies on an undocumented external cron job. Compare: flip_a_live.sh IS automated via deploy/broker-paper-smoke.service + deploy/broker-paper-smoke.timer (verified by reading those files). The flip_ac_live.sh script is mentioned in the commit message as scheduled but has no corresponding .timer/.service files deployed. This is a config-drift issue: the code (flip_ac_live.sh) exists but the systemd automation to run it on 2026-06-02 21:30 UTC does NOT.
  - _fix:_ Create two new systemd unit files in deploy/: (1) **deploy/flip_ac_live.timer** — a one-shot timer scheduled for 2026-06-02 21:30 UTC (17:30 ET). Suggested content: `[Unit] Description=Flip A + C to live paper trading after 2026-06-02 close [Timer] OnCalendar=2026-06-02 21:30:00 UTC Persistent=false AccuracySec=30s [Install] WantedBy=timers.target`. (2) **deploy/flip_ac_live.service** — a oneshot service that runs the flip script as root (to restart services). Suggested content: `[Unit] Description=Flip A and C to live paper trading [Service] Type=oneshot ExecStart=/opt/calypso/scripts/flip_ac_live.sh`. Alternatively, if manual operator invocation is preferred (to allow for last-minute checks), document this clearly in CLAUDE.md and RUNBOOKS.md with a note that the operator must run `sudo /opt/calypso/scripts/flip_ac_live.sh` at 21:30 UTC. The commit message's claim of being 'Scheduled' should match the actual deployment topology.

- **[BACKFILL]** (docstring/conf:high) **Return value docstring omits 'availability' field that is actually returned** — `bots/hydra/strategy.py:1559-1566`
  - _domain:_ Docstring accuracy & implementation gap (HYDRA strategy.py option quote handling)
  - The docstring lists the return dict shape as {bid, ask, last, mid, mark} with no mention of 'availability'. However, line 1605 returns {'bid': ..., 'ask': ..., ..., 'availability': avail}. The availability field is included in the actual return value but absent from the documented signature. This creates confusion about what a caller receives.
  - _fix:_ Update the docstring (lines 1559-1566) to include 'availability' in the return dict description: 'availability': Optional[str] # IBKR 6509 flag; first char R=real-time / D=delayed / Z=frozen / Y=frozen-delayed / N=not-subscribed.'. Also update the description to clarify: 'Note: a non-R availability is logged as a warning but does NOT gate trading.'

- **[BACKFILL]** (doc-inaccuracy/conf:high) **Batch method docstring claims same return shape as single method, but omits availability** — `bots/hydra/strategy.py:1620,1639`
  - _domain:_ Docstring accuracy & implementation gap (HYDRA strategy.py option quote handling)
  - `_read_option_quotes_batch()` docstring (lines 1620, 1639) says it returns 'the same per-quote shape as :meth:`_read_option_quote`' — but the actual implementation (lines 1665-1671) does NOT extract or return the 'availability' field. The single-quote method returns {bid, ask, last, mid, mark, availability}, but the batch method returns only {bid, ask, last, mid, mark}. The shapes are NOT the same, contradicting the docstring.
  - _fix:_ Either (1) add availability extraction to the batch method (line 1671: add '"availability": row.get("6509")') and update the docstring to match, OR (2) update the batch docstring to clarify: 'Returns the same price fields (bid, ask, last, mid, mark) as :meth:`_read_option_quote`, but does NOT include the availability (6509) flag. Callers requiring availability should use the single-quote method.' Also update line 1639 docstring signature.

- **[BACKFILL]** (docstring/conf:high) **Module docstring missing Schema v7, v8, v9 documentation** — `shared/data_recorder.py:1-20`
  - _domain:_ data_recorder.py module docstring / schema documentation
  - The module docstring documents only v5 (line 15-16) and v6 (line 18-19), but SCHEMA_VERSION = 9 (line 31). Versions v7, v8, and v9 are completely absent from the docstring. v7 adds shadow_entries table (line 122-162); v8 adds contracts columns (lines 99-105); v9 adds fill_price and mid_at_fill columns (lines 107-120). Future maintainers and auditors cannot understand the full schema history from the module docstring alone.
  - _fix:_ Update the module docstring (after line 19) to add: 'Schema v7 adds: shadow_entries table for OTM-based selection auditing. Schema v8 adds: per-row contract counts for 2-contract scaling. Schema v9 adds: ground-truth per-leg fill prices + mid_at_fill for entry slippage calculation.'

- **[BACKFILL]** (comment/conf:medium) **Comment block at lines 10773-10776 lacks concrete example of the != 0 fix benefit** — `bots/hydra/strategy.py:10773-10776`
  - _domain:_ Settlement Logic Comment Clarity - _process_expired_credits Post-ITM Fix
  - The comment explaining why `!= 0` is needed is present and mostly clear ('book ANY nonzero net, not just positive — an ITM-settled side contributes a LOSS, and the net (or an individually-negative side) must still be applied. The old > 0 guard silently dropped a net-negative settlement, overstating realized P&L.'), but could benefit from a 1-2 sentence concrete example to illustrate the scenario where a profitable TP close coexists with a subsequent ITM-settled loss on a remaining/paired side.
  - _fix:_ Extend the comment at lines 10773-10776 with 1-2 sentences of concrete example: 'Example: Entry opens with $200 call + $200 put credit. Call side TP-closes (booked +$100 profit, leaves $0 residual credit). Put side expires ITM (SPX at strike, booked -$50 loss). Net = +$100 - $50 = +$50; old > 0 guard would drop the -$50 put loss if put_expired_put_credit was -$50, overstating net to +$100.'

- **[BACKFILL]** (code-bug/conf:high) **Early close settlement path also uses throttled 'Intraday' period** — `bots/hydra/strategy.py:2638`
  - _domain:_ Settlement logging throttling during EOD reconciliation
  - The _execute_early_close() method calls self.log_performance_metrics() at line 2638, also during settlement/position reconciliation (line 2600 sets state to DAILY_COMPLETE). This call uses the same hardcoded period='Intraday' and will also be throttled despite being a settlement operation that should not be throttled per commit d83d50b intent.
  - _fix:_ Same fix as above: make period parameterizable and pass period='End of Day' when called from early-close settlement context.

- **[BACKFILL]** (comment/conf:high) **Missing explanation of where CALYPSO_IBKR_MAX_RPS constant should originate** — `shared/ib_constants.py`
  - _domain:_ CALYPSO_IBKR_MAX_RPS Hardcoded Value Drift Audit
  - The CALYPSO_IBKR_MAX_RPS value is defined as an environment variable in deploy/calypso-broker.service and referenced in comments in ib_client.py and strategy.py, but there is no constant definition or documentation in ib_constants.py (the 'single source of truth for IBKR CP API field codes + default field sets'). The file is explicitly designed as a deduplication point to avoid maintenance hazards of duplicate constant blocks drifting apart. The RPS value should be documented there with a comment explaining the IBKR SLA limits and the reason for the current value (50% headroom for ThreadPool+WAN jitter absorption).
  - _fix:_ Add a new section to shared/ib_constants.py documenting CALYPSO_IBKR_MAX_RPS with a constant and explanatory comment: 'CALYPSO_IBKR_MAX_RPS_DEFAULT = 5 # IBKR CP API: ~10 req/s/session limit. 5 rps = 50% headroom under sustained load to absorb ThreadPool+TLS+WAN jitter re-clustering (corrected from 8 on 2026-06-01 after 429-burst incident at 10:45 ET entry window). Read from os.environ in _RateGate.__init__(). Also used to configure api_pacing_multiplier scaling in strategy.py.' This makes the value a maintainable constant and prevents future drift.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **Config template _comment_downday conflates base-entry conversion with conditional E6 entry windows** — `bots/hydra/config/config.json.template:52-55`
  - _domain:_ E6 Entry Documentation and Configuration Clarity
  - The _comment_downday field (lines 52-55) describes two separate features in one paragraph: (1) base-entry down-day call-only conversion (disabled via base_entry_downday_callonly_pct=null) and (2) conditional E6 at 14:00 fires call-only when down. The comment's structure blurs whether E6 is a separate time slot or a directional override of base entries. This causes operators to misunderstand: Is E6 a third entry window, or does it apply to E1/E2/E3 on down days?
  - _fix:_ Split _comment_downday into two separate comments: (1) _comment_base_entry_downday for the base-entry conversion (currently disabled), and (2) _comment_conditional_e6 for the E6-specific conditional window behavior. Clarify that E6 is a SEPARATE time slot (14:00) independent of the base-entry schedule (10:45/11:15).

- **[BACKFILL]** (doc-inaccuracy/conf:high) **CLAUDE.md line 133 does not explicitly state E6 is a separate third entry window** — `CLAUDE.md:133`
  - _domain:_ E6 Entry Documentation and Configuration Clarity
  - Line 133 states 'Conditional entries: **E7 disabled.** **E6 (14:00)** fires put-only on up-days...' This correctly describes E6's behavior but does NOT explicitly say E6 is a SEPARATE scheduled entry window (time slot). An operator reading '2 base entries per day' followed by 'Conditional entries: E6' might interpret E6 as an optional mode that modifies the base entries, rather than a third independent window at a specific time (14:00). The word 'Conditional' could mean 'conditionally enabled' or 'conditional on market direction' vs. 'independent scheduled slot.'
  - _fix:_ Rewrite CLAUDE.md line 133 to explicitly state: 'E6 is a **third independent scheduled entry** at **14:00 ET** that fires ONLY if market direction meets threshold (put-only on up-days ≥+0.25%, call-only on down-days ≤-0.25%; flat days skip). E7 disabled.' This makes clear that E6 is a separate time slot, not an optional override of base entries.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **v7 schema description location inconsistent — split between docstring gap and inline comment** — `shared/data_recorder.py:122-124`
  - _domain:_ data_recorder.py module docstring audit — v7/v8/v9 schema documentation
  - Schema v7 (shadow_entries table creation) is documented inline at lines 122-124 ('v7: shadow entries table...') but is NOT mentioned in the module-level docstring at lines 15-19. This creates an inconsistency where the main module docstring claims it only covers v5-v6, while schema v7 is partially documented deep in the code block. The inline comment exists but is hidden from readers who rely on the module docstring for a schema overview.
  - _fix:_ Consolidate schema documentation: move the v7 description from the inline comment (lines 122-124) to the module docstring, and update the module docstring opening paragraph to be the single authoritative source for all schema versions. The inline comment can remain as a close-to-code reference, but the module docstring should be comprehensive.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **v8 and v9 schema descriptions exist only in migration blocks — no user-facing overview** — `shared/data_recorder.py:96-120`
  - _domain:_ data_recorder.py module docstring audit — v7/v8/v9 schema documentation
  - Schema v8 and v9 migrations are defined with explanatory comments (lines 96-98 for v8, lines 107-110 for v9) but these descriptions are located only in the migration SQL blocks and are NOT mentioned in the module-level docstring. A reader opening the file to understand the schema scope will see only v5 and v6 documented in the docstring, then must scroll past 80+ lines of migration SQL to find v8 and v9 descriptions. This violates usability principles for module documentation.
  - _fix:_ Expand the module docstring (lines 15-19) to include one-line summaries of v8 and v9: 'Schema v8 adds: per-row contract count for 2-contract scaling (contracts column on trade_entries, trade_stops, spread_snapshots, shadow_entries, daily_summaries). Schema v9 adds: ground-truth per-leg execution prices (short/long_call/put_fill_price and short/long_call/put_mid_at_fill for real slippage and broker reconciliation).'


### LOW

- **[AUDIT]** (other/conf:high) **Unprotected write to _rate_penalty_until (non-issue due to atomic assignment)** — `shared/ib_client.py:1372`
  - _domain:_ 429 entry-window burst + penalty box (IBKR safety)
  - The penalty box deadline is written without a lock when a 429 is caught. Multiple threads hitting 429 simultaneously could write concurrently to _rate_penalty_until.
  - _fix:_ No action required. Python's GIL and atomic float assignment make this safe; worst case a slightly stale deadline is used for ~1ms, which is negligible in a 10-minute window. The later timestamp wins, which is the correct behavior.

- **[AUDIT]** (other/conf:high) **Commission rounding fix in heartbeat display is correct** — `bots/hydra/main.py:639 (commit 21c9ca0)`
  - _domain:_ Margin NULL handling and buying-power gate (df56441, 21c9ca0)
  - Commit 21c9ca0 changed commission format from :.0f to :.2f for visual consistency with net_pnl (:.2f). The fix is cosmetic (display-only) and correct. No trading logic or P&L calculations are affected.
  - _fix:_ No action required.

- **[AUDIT]** (dead-code/conf:high) **Unused variable 'contracts' in _brandon_check_take_profit** — `bots/hydra/brandon/strategy.py:498`
  - _domain:_ Brandon variant close recording (commit df56441) — trade_stops data recording for TP, GEX-breach, and MKT-018 early closes
  - Line 498 captures 'contracts' variable but never uses it. The variable was likely prepared for a future calculation but the P&L correction at lines 503-510 uses spread_value directly without reference to contracts.
  - _fix:_ Remove the unused variable assignment to clean up code.

- **[AUDIT]** (dead-code/conf:high) **Unused variable 'contracts' in _brandon_check_breach_exit** — `bots/hydra/brandon/strategy.py:640`
  - _domain:_ Brandon variant close recording (commit df56441) — trade_stops data recording for TP, GEX-breach, and MKT-018 early closes
  - Same pattern as TP path: line 640 captures 'contracts' but never uses it. The P&L correction at lines 644-651 does not reference this variable.
  - _fix:_ Remove the unused variable assignment.

- **[AUDIT]** (comment/conf:low) **Commission format inconsistency in Telegram alert messages** — `bots/hydra/strategy.py:5711, 5728, 5756, 6656, 7890, 8611`
  - _domain:_ HYDRA-on-IBKR 0DTE SPX Bot: B/C CRITICAL Log Spam, B Max-Loss Display, Commission Rounding (Commits 21c9ca0, d83d50b)
  - Commission is displayed at .0f in Telegram Markdown-bold lines (e.g., line 5711, 6656, 7890, 8611) for readability in alert text, but at .2f in heartbeat display (main.py:639) and settlement logs (base_strategy.py:5109). The .0f format in Telegram is intentional for brevity in alert messages, but the inconsistency could cause confusion if an operator compares a Telegram alert value to the heartbeat or settlement log.
  - _fix:_ Document in a comment near line 5711 that Telegram uses .0f for brevity, while heartbeat/settlement use .2f for precision. Consider adding a footnote in the heartbeat display if the difference is significant, or standardize on .2f everywhere if Telegram brevity is less important than consistency.

- **[AUDIT]** (comment/conf:high) **Commit message claim about resize behavior is misleading** — `shared/logger_service.py:1563-1568 (comment), git commit 21c9ca0 message`
  - _domain:_ Google Sheets 429 retry + grid expansion (commit 21c9ca0, shared/logger_service.py)
  - The commit message claims 'resize both grows AND shrinks to end_row (removing stale rows)' and the code comment (lines 1564-1568) repeats this claim. However, the 'shrinking to remove stale rows' ONLY happens when all_rows is not empty. When all_rows is empty, resize is never called, so no shrinking occurs. The comment is therefore technically inaccurate because it doesn't account for the empty-list case.
  - _fix:_ Update the comment to clarify: 'If there are positions, resize to exact size (both grows and shrinks). If all positions have closed (all_rows empty), skip resize to leave sheet as-is — this means old rows persist until next snapshot (see bug #xyz).' Or, better yet, fix the bug first and then update the comment to remove the caveat.

- **[AUDIT]** (code-bug/conf:low) **broker_paper_smoke.py availability check parses first char but doesn't validate it's a known value** — `scripts/broker_paper_smoke.py:131`
  - _domain:_ Dry-run→live-paper auto-flip guard (commits 578e4cf, ef6795a, b9e79a4)
  - The _is_rt function at line 131 checks if a[:1].upper() == 'R' (first char is R), which correctly identifies real-time. However, if IBKR returns an unexpected format (e.g., empty string, non-string, or a new IBKR format not seen in the OpenAPI spec), the check will silently fail without logging the raw value. The raw availability IS logged at line 132, so the operator can see it, but the code doesn't validate the format.
  - _fix:_ Optional improvement (low priority): add a check like `if a and a[0] not in 'RZDNY'` and log a WARNING. Current behavior is acceptably fail-safe (unknown values fail the gate).

- **[AUDIT]** (other/conf:low) **broker /health endpoint caching (5s) could briefly report stale status during rapid checks** — `shared/broker_service.py:160-162`
  - _domain:_ Dry-run→live-paper auto-flip guard (commits 578e4cf, ef6795a, b9e79a4)
  - The /health endpoint caches results for 5 seconds (HEALTH_CACHE_S = 5.0). If the broker session dies and is immediately restored within 5 seconds, a flip script might see a stale 'connected: true' and proceed. However, since the flip scripts only call /health once, and 5 seconds is a short window, this is low-severity. The cache is intentional to prevent hammering IBKR with repeated auth checks.
  - _fix:_ Acceptable as-is. The cache window is documented in the comment at line 155. If a flip script wants the freshest status, it could POST to /health with a 'refresh' flag to bypass the cache, but current behavior is reasonable.

- **[AUDIT]** (comment/conf:medium) **ITM settlement fix verification claim ('now confirmed against the real broker')** — `shared/ib_client.py:2614 (approx) in _settlement_booked_pnl method`
  - _domain:_ Retry predicate + circuit-breaker hardening (commits 9b83067, 1952c04, 56669ba)
  - Commit 1952c04 claims the ITM settlement fix (#5) was 'live-validated' but the actual settlement code was never observed in production — it only executes at daily close on entries that expired. The implementation is correct (compares SPX settlement level vs short strike, books credit - intrinsic), but the 'live-validated' claim in the commit message is aspirational. The code will execute for the first time on 2026-06-02 close.
  - _fix:_ Monitor the 2026-06-02 close settlement P&L for correct ITM booking on any expired entries. The implementation is sound (safe-closed: defaults to full credit if settlement level unavailable), but real-world validation will occur on first close.

- **[AUDIT]** (comment/conf:high) **Imprecise comment on Check 2 timing (lines 12-13)** — `services/argus/health_check.sh:12-13`
  - _domain:_ ARGUS health-check fixes (commits ec07d79, 3a77971)
  - The header comment says Check 2 runs 'during regular market hours', which is vague. After the ec07d79 fix, Check 2 specifically runs during the tight trading session window (9:40-16:00 ET on weekdays), not the broader 9 AM-5 PM window. The comment should be more specific.
  - _fix:_ Update the comment to specify 'during trading session (9:40 AM-4:00 PM ET on weekdays)' for accuracy.

- **[AUDIT]** (comment/conf:high) **Outdated warning message on line 198** — `services/argus/health_check.sh:198`
  - _domain:_ ARGUS health-check fixes (commits ec07d79, 3a77971)
  - The warning message 'HYDRA state file not found (may be normal pre-9:30)' references the 9:30 open time, but after the ec07d79 fix, the check gates on is_trading_session() which starts at 9:40. The message should reflect the new 9:40 window.
  - _fix:_ Update the warning message to say 'may be normal in the first few minutes after trading session open' or similar to reflect the 9:40-16:00 window.

- **[AUDIT]** (comment/conf:high) **CLAUDE.md describes E6 conditional entry but does not explain the day-of-week variant capping (dow_max_entries)** — `CLAUDE.md, lines 133, 380-402 (Variant Comparison)`
  - _domain:_ DOC ACCURACY: CLAUDE.md + README.md vs current code
  - CLAUDE.md correctly documents E6 conditional entries (14:00, put-only on up-days, call-only on down-days) and the VIX regime entry capping via max_entries [2,2,2,1]. However, it does not mention the dow_max_entries config setting (e.g., day-of-week max entries like Fri=2) which further caps entries. This is a real config knob that can override the VIX regime cap. An operator adjusting entry counts needs to know about this.
  - _fix:_ In CLAUDE.md section on VIX Regime or Variant Comparison, add a note: 'Day-of-week entry caps (dow_max_entries config): can further limit entries on specific days (e.g., Fri=2 limits Friday to 2 base entries even if VIX regime allows more). Applied after VIX regime max_entries in the entry-time calculation.'

- **[AUDIT]** (doc-inaccuracy/conf:medium) **CLAUDE.md says E6 is a third conditional entry, but config template suggests E6 may be the only conditional on variant A** — `CLAUDE.md line 133; config.json.template lines 56-70`
  - _domain:_ DOC ACCURACY: CLAUDE.md + README.md vs current code
  - CLAUDE.md states 'Conditional entries: E7 disabled. E6 (14:00) fires put-only on up-days...' This is correct for variant A. However, the language could be clearer: E6 is the ONLY active conditional entry on A (E7 disabled). The config template shows conditional_entry_times: ['14:00'] + conditional_downday_e6_enabled: true + conditional_upday_e6_enabled: true, confirming E6 is the sole conditional. A new reader might think E6 and E7 are both available and one is just disabled; they should know E7 is dormant.
  - _fix:_ In CLAUDE.md line 133, clarify: 'Conditional entries: E6 (14:00) ONLY — E7 is disabled and dormant. E6 fires put-only on up-days (≥ 0.25%) or call-only on down-days (≥ 0.25%).'

- **[AUDIT]** (dead-code/conf:medium) **Variant A/B/C schedule mention in CLAUDE.md does not align with the actual runtime variant behavior after the broker pivot** — `CLAUDE.md lines 384-398 (Variant Comparison table)`
  - _domain:_ DOC ACCURACY: CLAUDE.md + README.md vs current code
  - The Variant Comparison table (line 386-390) states: 'Variant A: Schedule: 10:45 / 11:15 (+ E6 14:00 conditional)' and 'Variant B/C: Schedule: 09:45 / 10:45 / 11:15 / 11:45 (+ E6)'. However, these are the CANONICAL entry_times from config files. In practice: (1) Variant A runs on the broker (BrokerClient), Variant B/C also run on the broker. Both A and B/C have their VIX regime caps applied at runtime. So the documented schedules are CONFIG defaults, not guaranteed runtime schedules. (2) The table should note that actual entry times depend on VIX regime max_entries, dow_max_entries, and early-close gates. An operator expecting B to run 4 entries when VIX < 18 will see only 2 because the VIX regime cap on A (max_entries=[2,2,2,1]) is ALSO applied to B/C if they share the same logic. Need to verify if B/C have separate vix_regime configs.
  - _fix:_ Update CLAUDE.md Variant Comparison table to note: 'Schedules are CONFIG defaults; actual runtime entries subject to VIX regime max_entries and day-of-week caps. Variant B max_entries [3,3,3,2] allows up to 4 entries in low VIX; A/C capped at [2,2,2,1]/[2,2,2,1] respectively.'

- **[AUDIT]** (comment/conf:low) **External price feed comment contains outdated Saxo references marked as obsolete but not clarified for IBKR path** — `bots/hydra/config/config_variant_c.json:175`
  - _domain:_ CONFIG + SPEC + VERSION-HISTORY consistency (HYDRA trading bot)
  - Line 175 contains: 'NOTE: post-IBKR cutover the old Saxo 'NoAccess' / 'FullTradingAndChat session contention' rationale is obsolete (no Saxo on this branch). HYDRA's own VIX read now goes through self.broker.get_vix_price() → IBClient.get_vix_price (strategy.py:1913/1922) and does NOT consume this key — the external_price_feed feed is only wired into the shared/apollo path (services/apollo/market_data.py), so this flag has no effect on HYDRA's spread-width / vix_regime math today.' This is thorough but hard to parse — clarifies that external_price_feed.enabled is effectively dead for HYDRA but kept for other services.
  - _fix:_ Simplify comment to: 'Yahoo Finance fallback (only used by APOLLO service, not HYDRA). HYDRA's VIX reads via IBClient.get_vix_price() directly; this key has no effect on HYDRA's spread-width / vix_regime math. Kept enabled for APOLLO's defensive use.' Move the strategy.py line references (1913/1922) to a doc comment in strategy.py itself, not a config comment.

- **[BACKFILL]** (dead-code/conf:high) **NOTE: _option_quote_is_realtime() is referenced but not defined** — `bots/hydra/strategy.py:1590`
  - _domain:_ HYDRA-on-IBKR 0DTE SPX trading bot: Market-data availability Y/N codes test coverage
  - Line 1590 contains a comment reference to '_option_quote_is_realtime()' as a caller of the availability flag, but this function does not exist in the codebase. A grep for 'def _option_quote_is_realtime' returns no results. This suggests either (a) the function was intended to be written and never was, or (b) it was removed at some point and the comment was not updated.
  - _fix:_ Either (1) create the function if it was intended, or (2) remove the comment reference if it's vestigial. Given that the gating is already inline, option (2) is simpler: change line 1590 comment to remove the parenthetical reference.

- **[BACKFILL]** (comment/conf:low) **flip_ac_live.sh script comment discrepancy — mentions manual invocation but commit says scheduled** — `scripts/flip_ac_live.sh (lines 1-9)`
  - _domain:_ HYDRA on IBKR — Go-Live Documentation
  - The script header (lines 1-9) describes it as 'One-shot manual go-live' with a comment 'Scheduled via a one-shot systemd timer at 21:30 UTC', but the term 'manual' is potentially contradictory with 'scheduled'. If the intent is manual operator invocation at a specific time, the comment should say 'Manual (operator-triggered at 21:30 UTC)'. If the intent is full systemd automation, the word 'manual' should be removed and replaced with 'Automated one-shot flip'. The current phrasing is ambiguous: 'manual' suggests operator runs it, but 'Scheduled via systemd timer' suggests it runs automatically.
  - _fix:_ Clarify the header comment. If the script is meant to be manually invoked by the operator: 'One-shot manual go-live: operator invokes at 21:30 UTC (17:30 ET) on 2026-06-02 to flip variants A and C...'. If it is meant to be systemd-scheduled (i.e., automatic): 'One-shot automated go-live: systemd timer invokes at 21:30 UTC on 2026-06-02 to flip variants A and C...'. Additionally, if manual invocation is the model, add a note: 'Operator must run: `sudo /opt/calypso/scripts/flip_ac_live.sh` at 21:30 UTC on 2026-06-02 after market close.'

- **[BACKFILL]** (comment/conf:high) **Comment claims warning is 'once-ish' but uses logger.warning (no rate limiting)** — `bots/hydra/strategy.py:1592`
  - _domain:_ Docstring accuracy & implementation gap (HYDRA strategy.py option quote handling)
  - Line 1592 comment says 'Warn once-ish so a feed-entitlement problem is visible.' However, line 1595-1598 uses logger.warning() with no rate limiting or 'once' gate. This will log every single time a non-R availability is encountered (every entry attempt if the account lacks OPRA real-time subscription). The comment promise of 'once-ish' is not implemented.
  - _fix:_ Either (1) implement actual rate limiting (e.g., logging.handlers.MemoryHandler or a per-conid 'warned_once' dict + bool check), or (2) change the comment to reflect the actual behavior: 'Log a warning if the quote is non-real-time; this will fire repeatedly if the account lacks real-time entitlement.'

- **[BACKFILL]** (doc-inaccuracy/conf:high) **v6 docstring mentions 'originally Saxo' but module targets IBKR** — `shared/data_recorder.py:18`
  - _domain:_ data_recorder.py module docstring / schema documentation
  - Line 18 says 'per-leg broker (IBKR; originally Saxo)' which is correct and up-to-date (migrated from Saxo to IBKR during this audit cycle), but the shadow docstring comment at line 122-124 still refers to this as an OTM comparison table without mentioning the schema versioning context.
  - _fix:_ When updating the docstring to add v7/v8/v9, ensure v6's description remains correct (IBKR, not Saxo). No action required here; it's already correct.

- **[BACKFILL]** (comment/conf:medium) **Missing clarity on which log_performance_metrics calls are intraday vs settlement** — `bots/hydra/main.py:288, 464, 696`
  - _domain:_ Settlement logging throttling during EOD reconciliation
  - Three calls to strategy.log_performance_metrics() in main.py with different contexts: line 288 (startup), line 464 (post-settlement), line 696 (intraday heartbeat). The hardcoded period='Intraday' makes it unclear which context each call serves.
  - _fix:_ Add inline comments at each call site documenting the expected period. After implementing the period parameter fix, explicitly pass the appropriate period to each call for documentation.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **Config template lacks inline clarification of entry_times vs. conditional_entry_times distinction** — `bots/hydra/config/config.json.template:9-14 and 56-57`
  - _domain:_ E6 Entry Documentation and Configuration Clarity
  - The _comment_entry_times (line 9) explains the entry_times array but does not mention that conditional_entry_times (line 56) is a SEPARATE array that appends to entry_times after base entries are set. Lines 56-57 define conditional_entry_times but lack a _comment explaining when/why they are used. An operator modifying the config might not realize these are two separate arrays, or that conditional_entry_times are appended to entry_times AFTER the base count is recorded (see strategy.py:1067-1073).
  - _fix:_ Add a _comment_conditional_entry_times field (before line 56) stating: 'Conditional entry times (separate from base entry_times). These times are APPENDED to the schedule after base entry count is recorded. They fire only when market direction conditions are met (Upday-035 / Downday-035). Set empty list to disable all conditional entries.'


### INFO

- **[AUDIT]** (other/conf:high) **Conid pin regression accurately described and completely fixed** — `shared/ib_client.py:1528, 1926-1959, 1163-1164`
  - _domain:_ Conid pin + /secdef/search priming regression fix
  - Commit 1952c04 introduced pinned conids for SPX (416904) and VIX (13455763) to remove ambiguity in fuzzy-search results and save an API call. However, the pin DIRECTLY SKIPPED the search_contract_by_symbol call, which inadvertently removed the ONLY place where /iserver/secdef/search was being issued. IBKR requires this search to PRIME its session cache before /secdef/strikes and /secdef/info will resolve option contracts — without it they 500 with 'No Contracts retrieved'. This regression broke all option-chain resolution on 2026-06-01. Commit ff95877 correctly diagnosed this and introduced _ensure_secdef_search_primed() to re-issue the search purely for its server-side priming effect, keeping the pinned conid as the authoritative response. The priming is idempotent per (symbol, sec_type) and the primed set is cleared in LOCKSTEP with _conid_cache on disconnect so a cached conid never outlives its priming.
  - _fix:_ No action required. The fix is complete and properly tested.

- **[AUDIT]** (other/conf:high) **Lockstep cache clearing on disconnect enforced correctly** — `shared/ib_client.py:1162-1165`
  - _domain:_ Conid pin + /secdef/search priming regression fix
  - Both _conid_cache and _secdef_search_primed are cleared together in a single atomic block under self._call_lock during disconnect(). Line 1164 includes explicit comment 'lockstep with _conid_cache'. This ensures a cached conid never outlives its priming across a reconnect cycle. The clearing happens inside the lock, so concurrent threads can never observe torn state.
  - _fix:_ No action required.

- **[AUDIT]** (other/conf:high) **Priming is idempotent and resilient to transient failures** — `shared/ib_client.py:1942-1959`
  - _domain:_ Conid pin + /secdef/search priming regression fix
  - _ensure_secdef_search_primed checks if (symbol, sec_type) is already in _secdef_search_primed and returns immediately if so (line 1946-1947). If the search call fails, the exception is caught and logged as a warning (lines 1954-1959), but the key is NOT added to the set, so a retry can occur. This is correct: a failed priming still allows subsequent secdef calls to attempt the priming again, and if they fail too, that error bubbles up. Best-effort semantics (logged, not raised) mean genuine errors surface at the right layer (secdef/strikes/info calls).
  - _fix:_ No action required.

- **[AUDIT]** (other/conf:high) **Pinned conids (SPX=416904, VIX=13455763) are documented stable IBKR identifiers** — `shared/ib_client.py:529-536`
  - _domain:_ Conid pin + /secdef/search priming regression fix
  - Pinned conids for SPX (416904) and VIX (13455763) are documented in commit message 1952c04 as 'stable IBKR identifiers, confirmed live against the paper session'. These are INDEX conids, not option conids, so they never expire or rotate per expiry. Research documentation (docs/migration/research_scratch/02_ib_market_data.md) confirms SPX=416904. Both conids are defined ONCE in _PINNED_UNDERLYING_CONIDS dict and referenced ONLY via lookup, so they cannot be overridden.
  - _fix:_ No action required.

- **[AUDIT]** (other/conf:high) **RLock re-entrant semantics correctly applied** — `shared/ib_client.py:1505-1509, 1943-1945`
  - _domain:_ Conid pin + /secdef/search priming regression fix
  - qualify_contract holds self._call_lock (RLock) while calling _ensure_secdef_search_primed. The nested lock re-acquisition in _ensure_secdef_search_primed (line 1945) is free and safe because RLock is re-entrant on the same thread. Code comments at L1505-1509 explicitly state this design. No deadlock risk.
  - _fix:_ No action required.

- **[AUDIT]** (other/conf:high) **Pin is applied to both IND-quote and OPT paths** — `shared/ib_client.py:1517-1541, 1524-1527`
  - _domain:_ Conid pin + /secdef/search priming regression fix
  - The pinned path in qualify_contract runs for BOTH sec_type='IND' (underlying quotes) and sec_type='OPT' (options). For OPT, the pinned conid feeds Step 2 (secdef chain walk) directly, bypassing the fuzzy search but preserving the strike-resolution logic. Comments at L1524-1527 confirm this is intentional: 'Runs on BOTH the IND-quote and OPT paths because both option readers (get_option_chain / qualify_option_strikes) obtain the underlying via qualify_contract(symbol, sec_type="IND"), and this is the first call of the session before any secdef query.'
  - _fix:_ No action required.

- **[AUDIT]** (other/conf:high) **Test coverage confirms idempotency and priming behavior** — `tests/test_ib_client_reads.py:128-163`
  - _domain:_ Conid pin + /secdef/search priming regression fix
  - Two test cases verify the pin + priming fix: (1) test_pinned_index_conids_are_deterministic_and_prime_once (L128-143) asserts the conid is pinned, one priming search is issued per symbol, and repeat qualify does NOT re-search (cache hit). (2) test_pinned_underlying_used_for_option_chain_walk (L145-162) verifies the pinned underlying feeds the secdef chain walk and one priming search is issued. Both tests updated in ff95877 to expect priming searches instead of zero searches (as in the broken 1952c04 version). All 1020 tests pass.
  - _fix:_ No action required.

- **[AUDIT]** (other/conf:high) **Ensure_connected() properly clears caches on reconnect** — `shared/ib_client.py:1066, 1072-1076`
  - _domain:_ Conid pin + /secdef/search priming regression fix
  - ensure_connected() calls disconnect() (L1066) then connect() (L1073) as an atomic transaction under self._call_lock. This ensures caches are cleared between the old and new session. Any in-flight calls complete before the swap or block until the new session is in place (audit #11/#12). The next qualify_contract after reconnect will re-prime.
  - _fix:_ No action required.

- **[AUDIT]** (config-drift/conf:high) **B/C configs have alerts and sheets enabled, contradicting audit requirement** — `bots/hydra/config/config_variant_b.json:177-189 and config_variant_c.json:178-190`
  - _domain:_ Brandon variant close recording (commit df56441) — trade_stops data recording for TP, GEX-breach, and MKT-018 early closes
  - The audit scope requires 'alerts/sheets disabled' for B/C, but both config files have google_sheets.enabled=true and alerts.enabled=true. While B/C are dry_run=true (code-enforced), the configs enable alerts to Telegram and writes to Google Sheets. This may be intentional for pre-live monitoring, but contradicts the stated requirement.
  - _fix:_ Clarify intent: if alerts/sheets must be disabled before C's 2026-06-03 go-live, update config files accordingly. If intentional for pre-live ops, document the decision and plan removal. Note: the flip_ac_live.sh script does NOT disable alerts/sheets, only flips dry_run flag.

- **[AUDIT]** (docstring/conf:low) **Position-snapshot throttle return value behavior documented but could be clearer** — `shared/logger_service.py:1449-1457`
  - _domain:_ HYDRA-on-IBKR 0DTE SPX Bot: B/C CRITICAL Log Spam, B Max-Loss Display, Commission Rounding (Commits 21c9ca0, d83d50b)
  - The throttle returns True when skipped, documented as 'benign' in the comment. This is correct—a skipped snapshot is harmless because the NEXT unthrottled call will write the current state. However, the function docstring (lines 1434-1444) doesn't mention this throttle behavior or the return value semantics. A reader reviewing the function signature would expect return False on throttle, not True.
  - _fix:_ Update docstring at line 1443-1444 to clarify: 'bool: True if write completed or was throttled (both benign); False only on actual error.'

- **[AUDIT]** (other/conf:high) **Sheets API call reduction verified: 4 calls → 2 calls per snapshot (meic/hydra paths only)** — `shared/logger_service.py:1462-1576`
  - _domain:_ HYDRA-on-IBKR 0DTE SPX Bot: B/C CRITICAL Log Spam, B Max-Loss Display, Commission Rounding (Commits 21c9ca0, d83d50b)
  - Commit 21c9ca0 correctly reduced Sheets write calls for meic/hydra layouts from 4 to 2: (1) removed delete_rows (now harmless for resize-based layouts), (2) removed per-snapshot bold-clear format (data rows via update are not bold). The Sheets Positions tab now uses resize+update only. This is correctly implemented: iron_fly/rolling_put_diagonal paths retain delete_rows (append-based), while meic/hydra skip it (resize-based).
  - _fix:_ No fix needed. The implementation is correct and well-commented.

- **[AUDIT]** (comment/conf:low) **Throttle returns True on skip, masking snapshot freshness from caller** — `shared/logger_service.py:1451-1456`
  - _domain:_ Google Sheets 429 retry + grid expansion (commit 21c9ca0, shared/logger_service.py)
  - When the position snapshot is throttled (less than 60s since last write), the function returns True (line 1456), indicating success. This makes the caller think the snapshot was written, when in fact it was skipped. The comment correctly notes 'A skipped snapshot is benign (the next one rewrites the full current state)' but returning True could be confusing if a caller assumes True = 'written to Sheets'. The Positions sheet data age is then unknown to the caller.
  - _fix:_ This is a design choice and works as intended. If caller visibility is desired, consider returning a tuple or a separate method returning last_snapshot_age. For now, document the behavior clearly in the docstring (e.g., 'Returns True if write was skipped due to throttle'). The current code is safe and correct; this is just a visibility concern.

- **[AUDIT]** (comment/conf:low) **flip_a_live.sh Telegram message mentions 'effective entry windows' with hardcoded times — may be outdated** — `scripts/flip_a_live.sh:76-80`
  - _domain:_ Dry-run→live-paper auto-flip guard (commits 578e4cf, ef6795a, b9e79a4)
  - The Telegram alert message at lines 76-80 references specific entry windows ('10:45/11:15 ET, plus the 14:00 conditional') and notes that 'the configured 10:15 slot is dropped at all VIX levels by the regime cap'. This is accurate as of the commit date, but if entry windows or VIX regime caps are later changed in the strategy config, this message will become stale. It's informational only (doesn't affect safety) but could mislead operators.
  - _fix:_ Optional: Read the entry windows from the config.json at alert time and substitute them dynamically. For now, add a comment to update this message if entry windows change.

- **[AUDIT]** (comment/conf:high) **Docstring: 'rate limit' still mentioned in old code but correctly removed** — `shared/ib_retry.py:335`
  - _domain:_ Retry predicate + circuit-breaker hardening (commits 9b83067, 1952c04, 56669ba)
  - The comment on line 335 explicitly notes 'NOTE: 429 / "rate limit" intentionally NOT matched here', which is correct — the old code (9b83067) had 'or "rate limit" in msg' but this was removed in 1952c04 commit. The current code does NOT match on 'rate limit' string.
  - _fix:_ No action needed — the comment is accurate documentation of the intentional removal. This prevents a 429 with body containing 'rate limit' from being mis-classified as retryable.

- **[AUDIT]** (code-bug/conf:high) **Penalty box detection uses time.monotonic() consistency check** — `shared/ib_client.py:1323-1325, 1354-1356`
  - _domain:_ Retry predicate + circuit-breaker hardening (commits 9b83067, 1952c04, 56669ba)
  - Two places check the penalty box deadline: _invoke (line 1323-1325 for gate selection) and the guard (line 1354-1356 for refusal). Both use 'time.monotonic() < self._rate_penalty_until' which is correct — monotonic time is never affected by clock skew and won't spuriously expire/re-arm. The deadline is set at line 1372: 'time.monotonic() + _RATE_PENALTY_COOLDOWN_S' (10 min = 600s).
  - _fix:_ No issue found. The monotonic clock usage is the correct approach for timeout tracking.

- **[AUDIT]** (other/conf:high) **Breaker state does not record failures for 429 exceptions** — `shared/ib_retry.py:418-420`
  - _domain:_ Retry predicate + circuit-breaker hardening (commits 9b83067, 1952c04, 56669ba)
  - When a 429 is raised in a wrapped function, is_retryable(exc) returns False, so the condition 'if br is not None and is_retryable' (line 419) is False and br.record_failure() is NOT called. The exception is immediately released and re-raised (line 428-429). This is correct: 429 should NOT trip the per-family breaker because it is a global rate-limit event, not a broker degradation event.
  - _fix:_ No issue. The isolation of 429 from the breaker is architecturally correct — a 429 arms the penalty box (in _ib_call) but does not trip the breaker.

- **[AUDIT]** (other/conf:high) **Risk-critical paths correctly bypass penalty-box refusal** — `shared/ib_client.py:1354-1356, 1325-1328`
  - _domain:_ Retry predicate + circuit-breaker hardening (commits 9b83067, 1952c04, 56669ba)
  - Risk-critical calls (marked with _risk_critical=True) are never refused by the penalty box (line 1354: 'if (not _risk_critical and ...'). Instead, they are routed through the slow _penalty_gate (3 rps, ~0.33s spacing) in _invoke (line 1325-1326). This gate is chosen only when both conditions hold: risk_critical AND boxed. Non-risk-critical calls when boxed raise RatePenaltyError. All critical stop-loss paths (get_quote, get_order_status, cancel_order, _submit_order place) are marked _risk_critical=True.
  - _fix:_ No issue. The risk-critical threading is comprehensive and correctly preserves stop-loss management during a penalty box.

- **[AUDIT]** (other/conf:high) **HALF_OPEN probe slot released correctly for non-retryable exceptions** — `shared/ib_retry.py:421-429`
  - _domain:_ Retry predicate + circuit-breaker hardening (commits 9b83067, 1952c04, 56669ba)
  - When a non-retryable exception (including RatePenaltyError raised during a risk-critical call in HALF_OPEN state) is encountered, the breaker's release_probe() is called (line 428) BEFORE re-raising (line 429). This prevents the breaker from wedging in HALF_OPEN with every future caller short-circuiting. The probe slot is correctly released without recording a failure (which would re-OPEN the breaker).
  - _fix:_ No issue found. The probe-release logic is correct and prevents breaker wedging.

- **[AUDIT]** (other/conf:high) **IBKR 503-misuse patterns properly short-circuit before status_code check** — `shared/ib_retry.py:301-314`
  - _domain:_ Retry predicate + circuit-breaker hardening (commits 9b83067, 1952c04, 56669ba)
  - Permanent-error patterns (is not found, no longer found, already filled, already cancel, order is filled or canceled) are checked BEFORE the structured status_code check (line 313 return False, line 328 status_code check). This is correct because ibind tags these patterns with status_code=503, so checking the message first ensures they are never retried even if ibind happens to set a 5xx code. A permanent pattern match immediately returns False without further checks.
  - _fix:_ No issue. The ordering of checks is a defensive design that prevents false retry attempts on IBKR's 5xx-misuse patterns.

- **[AUDIT]** (other/conf:high) **Conid pin regression (fix ff95877) verified: secdef priming restored** — `shared/ib_client.py:1436-1460 (approx)`
  - _domain:_ Retry predicate + circuit-breaker hardening (commits 9b83067, 1952c04, 56669ba)
  - Commit ff95877 fixed a regression where the pinned conid path (commit 1952c04 cluster F #14) skipped the /iserver/secdef/search call needed to prime IBKR's contract cache. Without this priming, every option-chain lookup would fail with 'No Contracts retrieved' (500). The fix adds _ensure_secdef_search_primed() helper that issues the search once per (symbol, sec_type) per session, clears on disconnect, and is best-effort (failures logged, not raised).
  - _fix:_ No issue. The regression was caught (only visible in paper smoke with real IBKR path) and properly fixed. The priming is now guaranteed for all pinned and non-pinned paths.

- **[AUDIT]** (other/conf:high) **Regex non-standard leading-anchor pattern requires lowercase msg** — `shared/ib_retry.py:59-68`
  - _domain:_ Retry predicate + circuit-breaker hardening (commits 9b83067, 1952c04, 56669ba)
  - The regex uses lowercase status codes (429|500|...) and the msg is converted to lowercase (line 299: 'msg = str(exc).lower()'), so the regex is applied against lowercase strings. However, the leading-anchor pattern '^' (line 66: '|^\s*(?:500|502|503|504)\s+[a-z]') requires the message to START with the code. This is correct for exceptions that format as 'XXX Description', but if an exception message is nested or prefixed, the anchor will not match. This is intentional — the anchor prevents embedded codes from matching (e.g., '...quantity 500.00...' with a leading anchor at the start of the full string will never match).
  - _fix:_ No issue. The regex design is intentional: the leading-anchor form is for detecting a status-code-only message format, while the 'status...' keyword form handles messages where the code is prefixed by context.

- **[AUDIT]** (other/conf:high) **Penalty-box and breaker are independent; both can be active simultaneously** — `shared/ib_client.py:1302-1379, shared/ib_retry.py:364-442`
  - _domain:_ Retry predicate + circuit-breaker hardening (commits 9b83067, 1952c04, 56669ba)
  - The penalty box (managed in _ib_call) and the circuit breaker (managed in retry_with_backoff) operate on separate state machines. A breaker can be OPEN while the penalty box is NOT active, or vice versa. When a call is made: (1) the penalty-box guard refuses non-risk-critical calls first, (2) then retry_with_backoff checks if the breaker allows the request, (3) then the actual function runs. This design allows independent tuning: a 429 arms the penalty box without affecting the breaker, and transient 5xx can trip the breaker independently of the penalty box.
  - _fix:_ No issue. The independence is architecturally sound and allows correct handling of rate-limit vs broker-degradation events.

- **[AUDIT]** (dead-code/conf:high) **Legacy helper methods retain Saxo-name aliases for API stability** — `bots/hydra/base_strategy.py:10574 + strategy.py (multiple callers)`
  - _domain:_ HYDRA-on-IBKR dead-code audit (shared/ + bots/hydra/)
  - Methods like _get_total_saxo_pnl(), _recover_positions_from_saxo(), _verify_settlement_pnl_from_saxo() retain 'saxo' in their names even though they now work with IBKR. This is intentional for backward compatibility with the base MEIC class, not dead code — all are properly called from strategy.py (grep confirms 3+ callers each).
  - _fix:_ No action. These are intentional aliases that preserve the MEIC contract. Document the pattern if new methods added.

- **[AUDIT]** (other/conf:high) **Class-level defaults added for buffer_decay attributes to prevent startup error** — `bots/hydra/strategy.py:283-284`
  - _domain:_ HYDRA-on-IBKR dead-code audit (shared/ + bots/hydra/)
  - buffer_decay_start_mult and buffer_decay_hours are now initialized as None at class level (not just in __init__). This prevents 'no attribute' errors during state-reload recomputes that fire BEFORE __init__ completes. The comment documents that __init__ overrides them from config.
  - _fix:_ No action. This is defensive initialization, not dead code.

- **[AUDIT]** (other/conf:high) **RatePenaltyError properly imported and handled in strategy.py and broker_client.py** — `bots/hydra/strategy.py:48 + shared/broker_client.py:116`
  - _domain:_ HYDRA-on-IBKR dead-code audit (shared/ + bots/hydra/)
  - New exception class RatePenaltyError (ib_client.py:584) is imported in strategy.py and caught at 2 sites (lines 3708, 3740) with proper alerting via _alert_rate_penalty(). broker_client.py re-raises it correctly to preserve the exception type across RPC boundary.
  - _fix:_ No action. Exception handling is complete.

- **[AUDIT]** (other/conf:high) **Database schema v9 migration correctly adds new fill-price and mid-at-fill columns** — `shared/data_recorder.py:105-119 + base_strategy.py:366-375`
  - _domain:_ HYDRA-on-IBKR dead-code audit (shared/ + bots/hydra/)
  - Schema v9 adds 8 new REAL columns for per-leg fill prices and mid prices at fill time, to enable real slippage analysis on IBKR. The IronCondorEntry dataclass has matching fields (short_call_fill_price, short_call_mid_at_fill, etc.) all initialized to 0.0. In _execute_entry, these are populated from the fill results (base_strategy.py:2177, 2214, etc.). DataRecorder includes them in the INSERT statement (line 467-468).
  - _fix:_ No action. Schema upgrade is complete and properly wired.

- **[AUDIT]** (other/conf:high) **Settlement logic for ITM-settled shorts correctly handles missing SPX level** — `bots/hydra/strategy.py:10587-10657`
  - _domain:_ HYDRA-on-IBKR dead-code audit (shared/ + bots/hydra/)
  - _settlement_spx_level() returns None on any exception or missing price, and _settlement_booked_pnl() guards its calculation: 'if settlement_level is None or short_k <= 0 or itm_points <= 0: return credit, True' — falls back to the legacy full-credit assumption. The settlement_level read is only attempted on the real IBKR path, not in dry-run (line 10697-10700). Also safely handles missing contracts field with 'max(int(getattr(entry, "contracts", 1)), 1)' at line 10648.
  - _fix:_ No action. Settlement logic is defensive and correct.

- **[AUDIT]** (other/conf:high) **Rate-penalty box logic correctly separates risk-critical from non-critical paths** — `shared/ib_client.py:1310-1379`
  - _domain:_ HYDRA-on-IBKR dead-code audit (shared/ + bots/hydra/)
  - The _ib_call method correctly routes requests through different gates when the penalty box is active (lines 1323-1328). Risk-critical calls (_read_open_positions, get_order_status, etc.) marked with _risk_critical=True use the slow _penalty_gate (3 rps) to keep stop-loss management alive. Non-critical calls are hard-refused with RatePenaltyError (lines 1354-1362). A 429 sets the penalty box timeout (line 1372), never retried (removed from ib_retry.py _HTTP_RETRYABLE_CODE_RE at line 61-62).
  - _fix:_ No action. Rate-penalty logic is sound.

- **[AUDIT]** (doc-inaccuracy/conf:high) **flip_ac_live.sh script not documented in CLAUDE.md operator reference** — `CLAUDE.md (entire file)`
  - _domain:_ DOC ACCURACY: CLAUDE.md + README.md vs current code
  - The one-shot flip_ac_live.sh script (added in commit b9e79a4, 2026-06-02) for flipping A and C to live paper trading after the 06-02 close is not mentioned anywhere in CLAUDE.md. Similarly, flip_a_live.sh (conditional auto-flip from broker-paper-smoke.service) is not documented. The Deployment Workflow section (Line 658+) covers push/pull/restart but does not mention these critical go-live tools.
  - _fix:_ In CLAUDE.md Deployment Workflow section, add a subsection 'Live-paper flip scripts' documenting flip_a_live.sh (conditional auto-flip after smoke test PASS) and flip_ac_live.sh (manual one-shot scheduled flip). Include guards/sentinels, failure modes, and how to verify success.

- **[AUDIT]** (doc-inaccuracy/conf:low) **CLAUDE.md references strategy.py:_apply_vix_regime_overrides() but method is actually in strategy.py, not base_strategy.py** — `CLAUDE.md line 151`
  - _domain:_ DOC ACCURACY: CLAUDE.md + README.md vs current code
  - CLAUDE.md line 151 states: 'strategy.py:_apply_vix_regime_overrides() drops EARLIEST entries when capped (keeps best-performing E#3 at 11:15).' The method location is correct (it IS in strategy.py, not base_strategy.py), but the reference would be clearer with the full method signature or a note that this is a HydraStrategy override, not a base MEICStrategy method. This is a minor clarity issue, not a factual error.
  - _fix:_ No change needed; this is accurate. However, adding '(HydraStrategy)' or '(bots/hydra/strategy.py)' would clarify for an operator reading the code that this is a subclass-specific override.

- **[BACKFILL]** (comment/conf:low) **broker-paper-smoke.service comment references 'manual run' scenario but the timer is the primary deployment model** — `deploy/broker-paper-smoke.service:1-8`
  - _domain:_ systemd-timers | go-live-hardening
  - The service unit description says 'HYDRA paper-account order-path smoke (through calypso-broker) — validates a real 1-contract paper fill before A goes live'. The comment is accurate but doesn't clarify that this service is meant to be triggered ONLY by the broker-paper-smoke.timer one-shot (scheduled for 2026-06-01 09:35 ET) or manually for troubleshooting. A future operator reading just the .service file might assume it's a recurring service or can be started directly.
  - _fix:_ Add a comment line after the Description: '# This service is triggered ONE-SHOT by broker-paper-smoke.timer at 2026-06-01 09:35 ET. Do NOT start manually in production; the systemd timer is the sole trigger. After the timer fires and the conditional auto-flip completes (or fails and is decided manually), the timer should be disabled to prevent re-firing on system-time rewind (see deploy/README.md for post-go-live cleanup).'

- **[BACKFILL]** (comment/conf:high) **Clarification: both comments and code are otherwise accurate about the mechanism** — `shared/ib_client.py:738-751, shared/ib_client.py:775-781`
  - _domain:_ CALYPSO_IBKR_MAX_RPS Hardcoded Value Drift Audit
  - The comments correctly explain the RateGate mechanism, the IBKR session-level 10 req/s limit, the reason for the gate (prevent 429 burst during overlapping entry windows), the risk-critical gate bypass during penalty box, and the penalty gate at 3 rps. Only the hardcoded value '8' in the comments is wrong; the mechanism, limits, and design are accurate. The actual deployed configuration is safe and correct.
  - _fix:_ No code changes needed; the mechanism is correct. Only the comments referencing '8' need updating to '5'.

- **[BACKFILL]** (comment/conf:high) **config.json.template conditional-entry flags use inconsistent naming patterns** — `bots/hydra/config/config.json.template:59-65`
  - _domain:_ E6 Entry Documentation and Configuration Clarity
  - The config template defines both legacy (conditional_e6_enabled, conditional_e7_enabled) and new (conditional_downday_e6_enabled, conditional_downday_e7_enabled, conditional_upday_e6_enabled) flag sets. The comment explains the OR'd logic, but the flag naming itself is confusing: 'conditional_e6_enabled' could mean 'is E6 a conditional slot?' vs 'is the conditional (legacy) variant of E6 enabled?' The new names (conditional_downday_e6_enabled, conditional_upday_e6_enabled) are more precise but the legacy fallback is documented only in the _comment_downday, not at the flag definitions themselves.
  - _fix:_ Optional: add inline comments to each flag (e.g., '# legacy MKT-035 flag (superseded by conditional_downday_*)')  or rename legacy flags with _legacy suffix (conditional_e6_enabled_legacy) to make the migration path explicit. This is low-priority because the _comment_downday already explains the OR logic.

- **[BACKFILL]** (docstring/conf:medium) **HYDRA_STRATEGY_SPECIFICATION.md correctly documents E6 as separate conditional but uses dense prose** — `docs/HYDRA_STRATEGY_SPECIFICATION.md:44, 76, 125, 159, 379-389`
  - _domain:_ E6 Entry Documentation and Configuration Clarity
  - The strategy spec document accurately describes E6 as an independent conditional window (line 44: 'Conditional E#3 at 14:00', line 76: 'Conditional entry E6 fires as put-only...', line 159: 'E6 (14:00) | Conditional (Upday-035 / Downday-035)', line 379-389: detailed trigger logic). However, the documentation is technical and tightly packed; an operator skimming quickly might not grasp the relationship between E6 as a separate time slot vs. the directional logic (Upday-035/Downday-035). The spec's section 6.5 (MKT-035) mixes base-entry and conditional logic in the same subsection.
  - _fix:_ Minor: clarify line 44 to say 'Conditional E6 at 14:00' instead of 'E#3'. The spec is otherwise sound and accurate; no major rewrite needed. This is a naming/clarity issue.

- **[BACKFILL]** (doc-inaccuracy/conf:high) **CLAUDE.md VIX Regime section should mention interaction with day-of-week caps** — `CLAUDE.md:140-152`
  - _domain:_ HYDRA-on-IBKR 0DTE SPX trading bot — Day-of-week max_entries capping feature audit
  - The VIX Regime Adaptive section (lines 140-152) explains how vix_regime.max_entries caps the number of base entries based on VIX level. The section states entries are dropped from the front (keeping best-performing E#3 at 11:15) but does not mention that if dow_max_entries is also configured, it applies FIRST, and then VIX regime applies on top. This interaction is important for operators to understand the effective entry schedule.
  - _fix:_ Add a clarifying note to CLAUDE.md line 151 (end of VIX Regime section) explaining that if both dow_max_entries and vix_regime.max_entries are configured, dow_max_entries applies during startup/recovery (_parse_entry_times), then vix_regime.max_entries applies during the first entry check (_should_attempt_entry). The effective schedule is the intersection of both caps (most restrictive wins). Both caps drop entries from the front, preserving best-performing later slots.

- **[BACKFILL]** (comment/conf:high) **Day-of-week max_entries feature is fully implemented and correct but completely unused in production** — `bots/hydra/strategy.py:486-487, 811-821, 1076-1083; bots/hydra/config/*.json`
  - _domain:_ HYDRA-on-IBKR 0DTE SPX trading bot — Day-of-week max_entries capping feature audit
  - The dow_max_entries feature is: (1) implemented in strategy.py (initialization at 486-487, logging at 811-821, application at 1076-1083), (2) functional (correct logic for truncating entry_times and _base_entry_count), (3) documented in HYDRA_STRATEGY_SPECIFICATION.md, but (4) NOT USED in any current config file. Config files checked: config.json.template, config_variant_b.json, config_variant_c.json — none set dow_max_entries. Similarly, skip_weekdays is not set in any current config. This is not a bug (the feature is opt-in), but it means the code path is not currently exercised in production, so any config-specific edge cases would not be caught by live testing.
  - _fix:_ No action required — this is a feature gap, not a bug. If/when operators want to use skip_weekdays or dow_max_entries, they will add the config keys and the code will work. The feature is well-implemented and handles interactions with VIX regime correctly.

