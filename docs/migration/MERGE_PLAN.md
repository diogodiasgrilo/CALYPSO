# Merge Plan — `hydra-ibkr-standalone` → `main`

**Status:** PLAN. Do NOT execute any squash, rebase, or merge command without explicit user approval AND a fresh `git push origin hydra-ibkr-standalone:hydra-ibkr-standalone-backup-YYYYMMDD` first.

**Branch tip at planning time:** `5fdf850` (97 commits ahead of `main`).

---

## Why squash before merge

97 commits on a feature branch is a lot to land on `main`'s `git log`. Many are intermediate (probe iterations, audit remediation passes, doc-only tweaks). A squash pass reduces the noise to ~8-10 logical phases that match the design docs (`HYDRA_STANDALONE_REWRITE_PLAN.md`), making `git log main` navigable for future maintainers.

The alternative — merge-commit preserving all 97 — keeps the full audit trail but pollutes `main`'s history. **Recommendation: squash by phase.** The full audit trail is preserved on the `hydra-ibkr-standalone-backup-*` branch and in the design docs.

## Chunks (8 logical phases)

Each chunk below produces ONE squashed commit on `main` post-merge, with a commit message that summarizes the phase + lists every original commit SHA + author for the audit trail.

### Chunk 1 — Phase NEW-2 + F3 + F4 + F5: broker abstraction (read path)

The entire read-side IBKR migration: broker abstraction primitive, option chain (F3), position reconciliation via the conid-quantity model (F4), settlement / FX / closed-positions (F5).

**Original commits (28):**
```
6858055 docs(rewrite): Phase NEW-2 commit 1 — HYDRA→MEIC call-chain audit
41a2d94 feat(ib): Phase NEW-2 commit 2 — IBClient.place_and_wait_for_fill helper
96941d4 feat(ib): Phase NEW-2 commit 3 — _normalize_position_dict helper
eef4311 docs(rewrite): Phase NEW-2 commit 4 — defer closed-positions IBClient helpers
8552f7f docs(rewrite): Phase NEW-2 commit 5 — state schema design (no migration needed)
52e285c feat(hydra): Phase NEW-2 commit 6 — HYDRA __init__ accepts IBClient broker kwarg
e578401 feat(hydra): Phase NEW-2 commit 7a — chart data through broker abstraction
4b16e51 feat(hydra): Phase NEW-2 commit 7b — get_quote through broker abstraction
98ac894 docs(rewrite): F3 design — option chain via probed IBKR secdef behavior
8092ae9 feat(ib): F3.1 — IBClient.qualify_option_strikes batch resolver
7f78afd feat(hydra): F3.2 — _read_option_chain broker-agnostic chain reader
0c47544 feat(hydra): F3.3 — MKT-045 chain snapping via _read_option_chain
e90e108 feat(hydra): F3.4 — _read_option_quotes_batch broker-agnostic helper
1c80f32 feat(hydra): F3.5 — MKT-020 call-tightening via broker-agnostic chain/quotes
897830a feat(hydra): F3.6 — MKT-022 put-tightening via broker-agnostic chain/quotes
dd27548 feat(hydra): F3.7 — option greeks + entry-DB batch quote broker-agnostic
7a2eb04 docs(rewrite): F4 design — position reconciliation flow via IBKR
573c5f3 feat(hydra): F4.1 — _read_open_positions broker-agnostic helper
2d63619 feat(hydra): F4.2 — _position_is_open quantity-aware reconciliation primitive
7d840ff feat(hydra): F4.3 — MKT-033 salvage path via quantity-aware reconciliation
bb934f5 feat(hydra): F4.4 — POS-003 native conid→quantity reconciliation
e5e07a8 feat(hydra): F4.5 — FIX #82 overnight check via broker, not PositionId sets
012f057 feat(hydra): F4.6 — POS-004 settlement check via conid→quantity model
1e82533 feat(hydra): F4.7 — per-entry P&L sites via conid lookup
03e2406 feat(hydra): F4.8 — recovery rewritten state-file-authoritative
308aaec feat(hydra): F4.9 — monitoring batch quotes broker-agnostic; F4 complete
6717ffa docs(rewrite): F5 design — settlement / FX flow via IBKR
a03cfe6 chore(ib): F5.1 — IBKR trade-execution probe script
5044651 fix(rewrite): audit remediation — recovery state, chart period, +3 MEDIUM
986025e docs(rewrite): audit remediation — migration-doc accuracy + deferral log
0c6f086 fix(ib): strip whitespace from OAuth secrets read from env vars
dd6e879 docs(hydra): fix stale comment — downday_theoretical_put_credit ordering
014d89c chore(ib): F5.1 probe — prime /iserver/accounts before trades()
f8c6cd0 feat(ib): F5.2 — IBClient.get_closed_position_price
69007a0 feat(hydra): F5.3 — _read_fx_rate + _read_closed_position_price helpers
d87883c feat(hydra): F5.4 — rewire close-price + FX sites broker-agnostic
583b63c feat(hydra): F5.5 — settlement P&L verification broker-aware; F5 complete
```

(Note: 5044651, 986025e, 0c6f086, dd6e879 are audit-remediation patches that landed between F4 and F5; they belong with this chunk because they don't introduce new phases.)

**Squashed commit subject:**
```
feat(hydra): F3 + F4 + F5 — broker abstraction read path (IBKR)

Migrates HYDRA's read paths from SaxoClient to a broker-agnostic
interface backed by IBClient. F3: option chain via IBKR secdef.
F4: position reconciliation via the conid-quantity model (IBKR has
no per-leg position id). F5: settlement P&L + FX + closed-position
price via the new _read_closed_position_price / _read_fx_rate /
_read_open_positions helpers.

See docs/migration/{F3,F4,F5}_*_DESIGN.md for the design rationale.
Inheritance + write path follow in subsequent commits (Chunks 2-3).
```

### Chunk 2 — P1 + F6: inheritance reparenting + order write path

P1 reparents HydraStrategy onto a HYDRA-owned `MEICStrategy` base (no more import from sibling `bots/meic/`). F6 ports the order write path (`place_order`, `place_and_wait_for_fill`, `cancel_order`, `modify_order`) to IBKR with cOID dedup safety.

**Original commits (10):**
```
09fe3f0 docs(rewrite): completion plan — inheritance removal + write-path gap
0421415 refactor(hydra): P1 — reparent HydraStrategy onto a HYDRA-owned base
6e3d2fe refactor(hydra): P2 (partial) — delete the 2 dead recovery overrides
0cdeefd docs(rewrite): F6 design — order write-path flow via IBKR
23b790e feat(hydra): F6.1 — IBKR order write-path helpers
76193b1 feat(hydra): F6.2 — _place_option_order IBKR path
15ef596 feat(hydra): F6.3 — _close_position_with_retry IBKR path
b007ac5 feat(hydra): F6.4 — partial-fill recovery write paths via IBKR
bcdbdc1 fix(hydra): F6.5 — IBKR retry loops cancel unfilled orders (orphan prevention)
c20ce9e feat(hydra): F6.6 — close DEF-3 (MKT-033 gate) + DEF-5 (expired-credit keying)
```

**Squashed commit subject:**
```
feat(hydra): P1 + F6 — inheritance reparenting + order write path (IBKR)

P1: HydraStrategy now inherits from a HYDRA-owned MEICStrategy base
(bots/meic/ becomes importable-only for back-compat; not run).

F6: order placement, modification, cancellation, and stop-close all
route through IBClient. cOID is generated once per call and reused
across retries (server-side dedup makes the retry storm safe).
F6.6 closes deferred work items DEF-3 + DEF-5 (gates moved from
*_position_id to *_uic / conid).
```

### Chunk 3 — F7 + P2 + P4: strategy-layer broker abstraction + dead code removal

F7 finishes the broker abstraction inside HYDRA's strategy (read helpers, balance, BP gate). P2 and P4 delete dead Saxo code that was kept temporarily to ease the migration.

**Original commits (15):**
```
e94eda5 docs(rewrite): F7 design — migration gaps F1-F6 missed
8602996 feat(hydra): F7.1 — _read_index_price + _read_account_balance helpers
9563b82 feat(hydra): F7.2/F7.3 — _update_market_data + _check_market_halt via broker
d38846a fix(test): POS-003 test patched the wrong is_market_open module
501045a feat(hydra): F7.4/F7.5 — _check_buying_power + _estimate_entry_credit via broker
35b3404 feat(hydra): F7.6 — strike-liquidity adjusters via broker
8cd16d5 feat(hydra): F7.7 — _verify_entry_fill_prices + GAP-A gate via broker
7b3d96b refactor(hydra): P2.1 — delete dead recovery subsystem from base
36563c4 refactor(hydra): P2.2 — delete 35 remaining unreachable base methods
9237898 refactor(hydra): P4.1 — HYDRA-owned BuySell/OrderType enums
e048182 refactor(hydra): P4.2 — strategy.py read/dispatch helpers IBKR-only
102f665 refactor(hydra): P4.3a — collapse 8 broker-branched base methods
0c5c4fe refactor(hydra): P4.3b-1 — delete 6 now-dead Saxo order-verify helpers
9a5db44 refactor(hydra): P4.3b-2 — log_daily_summary FX via _read_fx_rate
08d6410 refactor(hydra): P4.3b-2 — wire 8 base methods to IBKR helpers
5130cd8 refactor(hydra): P4.3b-2 — _reconcile_positions + _validate_system_clock
72cd12f refactor(hydra): P4.4a — broker mandatory, main.py constructs IBClient
```

**Squashed commit subject:**
```
feat(hydra): F7 + P2 + P4 — strategy-layer abstraction + Saxo dead-code purge

F7 finishes the broker abstraction inside strategy.py: index price,
account balance, BP gate, market halt detection, strike-liquidity
adjustment, fill-price verification.

P2 + P4 delete the now-dead Saxo code (35 unreachable base methods,
6 Saxo order-verify helpers, the dual broker-branched dispatcher).
broker is now a mandatory constructor arg; main.py constructs the
IBClient explicitly.
```

### Chunk 4 — P5 + P6: final Saxo purge + doc refresh

Removes shared/saxo_client.py + shared/broker/ + Saxo scripts. The 3 non-MEIC bots and bots/meic/ get deleted (already kill-switched on `main`; preserving them here would just be dead code). P6 refreshes code comments + scripts/README.md for the IBKR-era state.

**Original commits (6):**
```
f29ae5b chore(rewrite): P5a — delete the 3 retired non-MEIC bots
660705a chore(hydra): P5b — delete the retired bots/meic/ directory
8997c13 chore(rewrite): P5c — delete shared/saxo_client.py + shared/broker/ + Saxo scripts
38ee3b4 docs(rewrite): P4 — record DEF-7 (POS-003 conid reconciliation)
0ba0527 docs(hydra): P4 — refresh migration comments after the Saxo purge
f3de391 docs(hydra): P6.1 — fix code comments describing current behavior
8cc8e9d docs: P6.2 — rewrite scripts/README.md for the IBKR HYDRA repo
```

**Note on P5a/P5b deletes:** P5a deletes bots/iron_fly_0dte/, bots/delta_neutral/, bots/rolling_put_diagonal/. P5b deletes bots/meic/. On THIS branch the sibling bot dirs ARE present + kill-switched (per current state). Need to verify whether P5a/P5b actually deleted them or whether they were restored — git log says deleted but the current `bots/` tree shows them present. **Pre-merge investigation required: was a later commit re-adding them, or did kill-switch addition come later?** This is the one ambiguity to clarify before squashing this chunk.

**Squashed commit subject:**
```
chore(hydra): P5 + P6 — delete Saxo code + refresh docs

P5: delete shared/saxo_client.py, shared/broker/, and the 4
sibling bots (iron_fly_0dte, delta_neutral, rolling_put_diagonal,
meic). All preserved on `main` for historical reference.

P6: refresh code comments + scripts/README.md to reflect the
IBKR-only state.

DEF-7 (POS-003 conid reconciliation) explicitly recorded as
deferred with a concrete trigger in DEFERRED_WORK.md.
```

### Chunk 5 — P7 Steps 1-4: go-live infrastructure (re-auth, creds, service, probe)

Morning re-auth gate, systemd LoadCredentialEncrypted=, hydra.service hardening, the Step-2 market-data probe.

**Original commits (8):**
```
0026ac1 feat(ib): P7 Step 3 — morning IBKR re-auth gate
a21f2d2 docs(rewrite): P7 go-live plan — research basis + 6-step sequence
8c42a74 feat(deploy): P7 Step 4 — systemd encrypted credentials + hydra.service
d187f5e feat(scripts): P7 Step 2 — IBKR market-data verification probe
c15d913 fix(scripts): P7 Step 2 — rewrite probe as a market-data diagnostic
b3361cb fix(scripts): P7 Step 2 probe — line-buffer stdout so output streams under tee
ea65526 test(scripts): P7 Step 2 probe — add /iserver/accounts preflight test
```

**Squashed commit subject:**
```
feat(deploy): P7 Steps 1-4 — IBKR go-live infrastructure

Morning re-auth gate (ensure_connected() called once per trading
day plus every 15 min intraday). Systemd unit with
LoadCredentialEncrypted= for 6 IBKR creds + sandboxing
(NoNewPrivileges, ProtectSystem=strict, ReadWritePaths=/opt/calypso,
kernel protections). Pre-start verification runbook in
IBKR_CREDENTIALS_SETUP.md. Step-2 market-data probe with control
SPY + SPX + VIX (catches index-entitlement issues distinct from
preflight bugs).
```

### Chunk 6 — P7 audit Round 1 + all 49 fixes (Critical + High + Medium + Low)

The 6-agent audit register + every fix commit, organized by severity.

**Original commits (14):**
```
17c3d3b docs(rewrite): P7 audit — findings register (Round 1)
c5f43b3 fix(ib): P7-audit C2 — snapshot endpoint /iserver/accounts preflight
98c2505 fix(hydra): P7-audit C1 — stop-loss must close via conid, not pos_id
9867972 fix(ib): P7-audit C3+C4 — place_and_wait_for_fill instant-fill handling
d694c8b fix(hydra): P7-audit H1/H4/H5/H9 + M1/M5 — pos_id gates, connect, service
881ebf6 fix(ib): P7-audit H8 + H10 — get_balance graceful FX, snapshot warmup
62a538f fix(hydra): P7-audit H3 + H6 + H7 — validate_config, intraday re-auth, credit estimate
f79a8dd fix(hydra): P7-audit H2 — settlement gates on conid model, not the registry
67513c4 fix+docs: P7-audit H11 + H13 — modify_order tick / doc inaccuracies
84b8ddf test: P7-audit H12 — _ib_call retry/breaker, _unwrap, cOID dedup
b8afbef fix: P7-audit M6 + M7 + M8 + M10 — flatten vacuous broker guards, fix dead overnight-0DTE check + dead MKT-033 AUTO path, fix or-chain falsy drop
ea800ff fix: P7-audit M11-M17 — ib_client.py hardening (whatif preflight, breaker propagation, FX shape lookup, order-list filter, WS race, snapshot diagnostics)
2d058de fix: P7-audit M2 + M3 + M4 — ib_oauth credential-load hardening + setup doc
7d6f04f fix: P7-audit L1-L15 — Low/Nit hardening + version-bump + dead-code purge
```

**Squashed commit subject:**
```
fix(hydra,ib): P7 audit Round 1 — close 4 Critical + 13 High + 17 Medium + 15 Low

6-agent multi-domain audit found 49 issues across the IBKR
migration. All 49 closed with fixes + regression tests. Highlights:

Critical (4):
  C1 — stop-loss now closes via *_uic (conid), not *_position_id
       (IBKR has no per-leg position id; the prior gate dead-coded
       every stop-close action).
  C2 — snapshot endpoint requires /iserver/accounts preflight,
       which was not satisfied by portfolio_accounts() (different
       namespace). Without it, snapshots return metadata-only forever.
  C3 — place response's status field is `order_status`, not `status`
       (latter is live-order field).
  C4 — instant-fill response carries status only; fill quantity
       must be fetched from get_order_status to avoid double-positions.

High (13): unwind, settlement on conid model, validate_config,
   broker.connect() error handling, service unit StartLimit*,
   intraday re-auth, credit-estimate quoted-leg gate, get_balance
   graceful FX, snapshot warmup tuning (12*0.5s), modify_order tick
   exposure, retry/breaker/_unwrap test coverage, doc accuracy.

Medium (17): main-loop break threshold, oauth file-read OSError
   split, empty CREDENTIALS_DIRECTORY raises, IBKR_CREDENTIALS_SETUP
   pre-start checklist, service sandboxing, broker-guard flattening,
   STATE-004 broker-direct trigger, MKT-033 uic-only gate, _uic→None
   post-stop, _read_index_price is-not-None ladder, what_if_order
   preflight, place_and_wait_for_fill CircuitBreakerOpen handling,
   _discover_account_id KeyError → IBAuthError, get_fx_rate shape
   ladder, _submit_order order-id-prefers-real-entries, WS handshake
   race, snapshot diagnostic distinction.

Low (15): explicit None checks, log clamping/dropping warnings,
   dead-code purge, qualify_contract precedence, 401 word-boundary
   regex, get_option_chain list coercion, mid=None on crossed market,
   --live deprecation, __init__.py version-history 2.0.0-rc.1,
   operator-visible Saxo string de-Saxo, pytest.approx, test specs.

Full register: docs/migration/P7_AUDIT_FINDINGS.md.
```

### Chunk 7 — P7 audit Rounds 2 + 3 + Step 6 (CLAUDE.md + probe results)

Re-audit of fixes (Round 2, 4 parallel agents, PASS) + senior-overseer full-branch verification (Round 3, PASS) + CLAUDE.md rewrite for the IBKR-standalone branch + probe-driven cleanups.

**Original commits (4):**
```
bd0f44f docs+deploy: P7 audit Round 2 — re-audit pass + 2 minor doc clarifications
d72b0cc docs: P7 audit Round 3 — senior-overseer verification PASS
0a34e04 docs: P7 Step 6 — rewrite CLAUDE.md for the IBKR-standalone branch
af4c11f docs+config: P7 Step 2 probe results — VIX conid fix + dead Saxo key purge from configs
```

**Squashed commit subject:**
```
docs: P7 audit Rounds 2-3 PASS + CLAUDE.md rewrite + Step 2 probe

Round 2 (4 parallel domain agents, read-only): 0 new bugs, 0
incomplete fixes, 3 minor doc-clarity concerns addressed
post-audit. Round 3 (single senior overseer, 7-section verification):
money-path correct, 8 failure modes covered, no Saxo residue,
branch cleared for VM deploy. Money question: 'Would I bet my house
on a full week of paper trading without intervention?' = YES, with
caveat that Step 1 (data-sharing toggle) + Step 2 (probe) must
complete first.

CLAUDE.md rewrite (943 lines, down from 1,520) for the
IBKR-standalone branch: drops the 5-bot inventory + Saxo
API patterns + token_keeper + Saxo Bank symbols; adds IBKR
Integration section (preflight, retry+breaker, conid model,
cOID dedup), credentials section, IBKR symbol table.

Sunday probe: 6509='Z' (frozen) — expected for weekend + Memorial
Day holiday. Pipeline confirmed working. Tuesday re-run gates
real-time confirmation. VIX conid corrected from 13455 to 13455763
(probe-verified). Dead Saxo keys removed from config templates.
```

### Chunk 8 — World-class polish + final audit

This polish pass: alerts, runbooks, observability, docs. Currently in flight.

**Original commits:** TBD — will be added as the polish pass completes (Items 1-12 plus the audit pass).

---

## Squash mechanics

After user approval, execute in this exact order:

```bash
# 1. Safety backup — push the current branch tip to a backup branch.
git push origin hydra-ibkr-standalone:hydra-ibkr-standalone-backup-$(date +%Y%m%d)

# 2. Verify clean working tree, tests pass.
git status                          # must be clean
python -m pytest tests/ -q --ignore=tests/test_dashboard  # must be 885+ passed

# 3. Identify the merge-base.
MERGE_BASE=$(git merge-base hydra-ibkr-standalone main)
echo "Merge base: ${MERGE_BASE}"

# 4. Branch off main for the squashed history.
git checkout main
git pull
git checkout -b hydra-ibkr-standalone-squashed

# 5. For each chunk, cherry-pick the commits onto a clean branch THEN squash.
# (Easier alternative: `git merge --squash hydra-ibkr-standalone` collapses
#  everything to one commit; we want 8 commits instead, so cherry-pick per
#  chunk and amend.)

# Chunk 1 example:
git cherry-pick 6858055^..308aaec  # NEW-2 through F4.9
git cherry-pick 6717ffa..583b63c   # F5 design through F5.5
git reset --soft ${MERGE_BASE}      # collapse to staged changes
git commit -m "feat(hydra): F3 + F4 + F5 — broker abstraction read path (IBKR)
  ... (full body from §Chunk 1 above) ..."

# Repeat for Chunks 2-8 (each preceded by checkout from the prior chunk's tip).

# 6. Verify the squashed branch is byte-identical to the original tip:
git diff hydra-ibkr-standalone hydra-ibkr-standalone-squashed
# Expected: empty (no diff). If non-empty, abort and investigate.

# 7. Fast-forward the original branch to the squashed version.
git checkout hydra-ibkr-standalone
git reset --hard hydra-ibkr-standalone-squashed
git push --force-with-lease origin hydra-ibkr-standalone

# 8. Open the PR.
gh pr create --base main --head hydra-ibkr-standalone \
  --title "v2.0.0 — HYDRA on Interactive Brokers" \
  --body "$(cat docs/migration/MERGE_PLAN.md | head -200)"
```

**Failure recovery:** If anything goes wrong, the backup branch from step 1 holds the full 97-commit history. `git push --force-with-lease origin hydra-ibkr-standalone-backup-$(date +%Y%m%d):hydra-ibkr-standalone` restores it.

## Branch protection (recommended GitHub settings for `main`)

After the merge lands:

- **Require pull request reviews** (1 reviewer, no self-review)
- **Require status checks pass** before merging: `pytest` (full suite), `pip-audit` (no high-severity CVEs), `systemd-analyze verify` (for any deploy/*.service change)
- **Require branches to be up to date** before merging
- **Restrict who can push to matching branches** — main is protected; admins only
- **Disallow force pushes** to `main`
- **Disallow deletions** of `main`
- **Require signed commits** (optional but recommended for production-trading code)

## Post-merge cleanup

```bash
# Tag the merge as v2.0.0
git tag -a v2.0.0 -m "HYDRA on Interactive Brokers — paper-only initial release"
git push origin v2.0.0

# Delete the feature branch (kept on backup branch per step 1)
git push origin --delete hydra-ibkr-standalone

# Verify main + tag
git log main -1
git tag --list v2.*
```

## Pre-squash verification (resolved 2026-05-24)

**Chunk 4 P5a/P5b deletes — verified clean.** `git ls-tree HEAD bots/` on
the branch tip shows ONLY `bots/__init__.py` + `bots/hydra/`. The 4
sibling bot directories (`iron_fly_0dte`, `delta_neutral`,
`rolling_put_diagonal`, `meic`) are NOT tracked. They appear in the
local working tree only as stale untracked leftovers from before
the P5 commits — `git status` does not list them as modified or new.

The kill-switches (`DISABLED_FOR_SAFETY=True` in each `main.py`) were
added to those bots in v1.24.0 on `main` and remain there. On THIS
branch the kill-switch is moot — the bots are gone entirely. The
merge to `main` will land with `bots/` containing only `__init__.py`
and `hydra/`, which is the intended end state.
