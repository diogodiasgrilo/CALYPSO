# Comprehensive Re-Audit Findings (Round 1)

**Branch:** `hydra-ibkr-standalone` @ `3c08434`
**Audit date:** 2026-05-24
**Audit scope:** Every line of code, comment, docstring, .md document modified on this branch (115 files, 109 commits).
**Audit method:** 6 parallel domain agents (IBKR client / HYDRA strategy / Deploy + ops / Tests / Docs / Variants + Brandon + Scripts / Data + state + DB schema). Read-only.
**Test baseline:** 918 passed, 15 skipped (integration), 0 failed.

This is the 4th audit cycle on this branch (Round 1 of the P7 audit + Round 2/3 + 3-agent + senior overseer polish audit + this re-audit). All four previous cycles passed. This re-audit was requested by the user for "world-class" rigor.

---

## Severity counts

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 4 |
| Medium | 7 |
| Low / Nit | ~17 (most are observations / hardening / cleanup) |
| **Total actionable** | **11 (4 H + 7 M)** |

---

## HIGH (must fix before merge)

| ID | File:line | Issue | Status |
|----|-----------|-------|--------|
| AUD2-H1 | `shared/ib_client.py:527` (the `logger.info` call inside `connect()`) | The connect-time INFO log includes `consumer_key=%s` and substitutes `self.cfg.credentials.consumer_key`. This bypasses the Polish #10 `field(repr=False)` hardening — a `consumer_key=CALYPSOPP` line lands in every `journalctl -u hydra` log, every Google Cloud log shipment, every operator screenshot. The consumer key isn't the most sensitive secret (the access token is) but it IS part of the OAuth keypair and IBKR account-recovery is non-trivial. | OPEN |
| AUD2-H2 | `services/argus/README.md:14` | Health-check table in ARGUS's own README still lists `token_keeper service running` (dead on this branch — Saxo-only) and `token_cache freshness` (Saxo file never written on IBKR) as ACTIVE checks. The actual `services/argus/health_check.sh` was rewritten in Polish #2 to remove these and add `last_heartbeat_at`, `breaker` grep, and `backup` checks — but the README wasn't updated. An operator following the README would expect alerts that ARGUS no longer fires (and miss the new checks ARGUS does fire). | OPEN |
| AUD2-H3 | `CLAUDE.md:981-982` (the "Saxo-era docs" cross-reference section) | The two table rows reference `docs/IRON_FLY_*.md` and `docs/DELTA_NEUTRAL_*.md` with text saying "Iron Fly bot (kill-switched on this branch)" and "Delta Neutral bot (kill-switched)". Polish #8 corrected the top-of-doc statements (lines 4, 33, 59, 995 in CLAUDE.md + README.md line 48) to say "deleted on this branch; kill-switched on main." Lines 981-982 in the cross-reference table still have the old wording — operators reading that section get the wrong mental model. | OPEN |
| AUD2-H4 | `scripts/README.md:42-49` | "Migration (Saxo → IBKR rewrite)" section in scripts/README lists Saxo-era one-shot probe scripts (p2_method_ranges.py, p4_collapse.py, etc.) as if they're active operator tools. They're frozen migration artifacts (P2 + P4 phases complete). An operator reading the README might try to run them and expect Saxo behavior. | OPEN |

## MEDIUM (should fix before merge)

| ID | File:line | Issue | Status |
|----|-----------|-------|--------|
| AUD2-M1 | `requirements-lock.txt:22-68` | The lock file regeneration command in the header (line 17) says to grep out pip-audit + cyclonedx + … — but pytest, pytest-asyncio, pytest-cov, pip_audit, coverage, iniconfig, pluggy, Pygments are STILL in the lock file. When the operator runs `pip install -r requirements-lock.txt` on a fresh VM, they get the entire test stack installed in production. | OPEN |
| AUD2-M2 | `deploy/setup_vm.sh` | Bootstrap script references the dead `calypso.service` (which doesn't exist on this branch — `hydra.service` is the entry point). Running `setup_vm.sh` on a fresh VM would fail at the systemctl enable step. The script also references Saxo Secret Manager secrets that no longer exist (`calypso-saxo-credentials`). | OPEN |
| AUD2-M3 | `README.md:7` | README says "operator reference: `CLAUDE.md` — the authoritative single-file reference for this branch (~940 lines, 24 sections)" but CLAUDE.md is now 998 lines (after the polish-pass updates). The "~940" line count is wrong. | OPEN |
| AUD2-M4 | `docs/migration/LIVE_READINESS_CHECKLIST.md` Gate 1 | Gate 1 says "Bot deployed from `main` branch, not a feature branch" — correct for live cutover but misleading for paper validation. Paper validation IS the gate BEFORE the merge to main. The text should clarify this gate applies to live cutover specifically, not the paper week. | OPEN |
| AUD2-M5 | `bots/hydra/config/config_variant_c.json:52` | Variant C declares `"conditional_entry_times": ["14:00"]` but lines 55-62 disable every conditional E6 flag (`conditional_e6_enabled: false`, `conditional_downday_e6_enabled: false`, `conditional_upday_e6_enabled: false`). The 14:00 slot consumes a slot in `vix_regime.max_entries` cap accounting without ever firing — wasted slot. | OPEN |
| AUD2-M6 | `bots/hydra/base_strategy.py:_reconcile_positions ~3780+` | Per-leg reconciliation loop gates on `*_position_id` (Saxo-era) instead of `*_uic` (IBKR-native). On IBKR all `*_position_id` are None, so the per-leg loop never fires — mid-session manual-close detection is dead on IBKR. **This is the DEF-7 deferred-work item** (documented in DEFERRED_WORK.md). The audit flagged this not because it needs to be fixed pre-merge (it doesn't — settlement + stop monitor still function) but because the in-code comment near the loop should explicitly say "DEF-7: dormant on IBKR per migration plan" so a future reader doesn't try to use this code path. | OPEN — code-comment fix only |
| AUD2-M7 | `bots/hydra/strategy.py:_load_state_file_history ~10748` | `json.load(f)` has no explicit `json.JSONDecodeError` handler. A torn state file (mid-write SIGKILL between temp+rename — extremely rare given the atomic write, but possible if the disk fills up between cp and mv) would crash recovery with an unhandled JSONDecodeError, the outer except catches it generically and the bot starts cold. The Polish #5 pre-start snapshot mitigates this (operator can manually restore), but an explicit handler that auto-falls-back to the most recent snapshot is better. | OPEN |

## LOW / Nit (accept or batch-fix)

| ID | File:line | Issue |
|----|-----------|-------|
| AUD2-L1 | `deploy/db_backup.timer:5` | DST comment is backwards (says 23:00 UTC = 6 PM EST during EDT; actually it's 7 PM EDT). Comment cosmetic only. |
| AUD2-L2 | `deploy/clio.service:26` | `ReadWritePaths=/opt/calypso/intel /opt/calypso/.git` — space-separated multi-path syntax is valid in systemd but could be split into two lines for clarity. CLIO writes commit messages to `.git/COMMIT_EDITMSG`, so the `.git` access is needed. |
| AUD2-L3 | `services/argus/health_check.sh:28-29` | Requirements comment lists `jq` but the script doesn't use it (uses Python for JSON parsing). |
| AUD2-L4 | `deploy/hydra.service` + variants | Missing `ProtectDevices=yes` sandboxing — defense-in-depth for a service holding decrypted private keys. The sandboxing block already has 5 directives; adding a 6th is cheap. |
| AUD2-L5 | `deploy/hydra.service:55` | `SyslogIdentifier=hydra` while variants use `hydra_variant_b` etc. — could rename to `hydra_main` for symmetry. Cosmetic. |
| AUD2-L6 | `docs/HYDRA_STRATEGY_SPECIFICATION.md:9` | "DRY-RUN against IBKR paper since 2026-04-27" + version `2.0.0-rc.1 (last updated 2026-05-24)` — two different dates serving different purposes, but the relationship could be clearer. |
| AUD2-L7 | `docs/migration/MERGE_PLAN.md` | Status header says "PLAN — awaiting Plan-agent audit before execution" but the audit happened during the polish pass and the plan is now frozen. Should clarify status as "FROZEN PLAN — squash design approved 2026-05-24; do NOT execute without fresh backup branch." |
| AUD2-L8 | `docs/migration/DEFERRED_WORK.md` DEF-1, DEF-2 headers | Both note "Superseded by F5" but the explicit "✅ IMPLEMENTED (F5.x)" status could be added so readers don't have to chase the F5 doc. |
| AUD2-L9 | `bots/hydra/brandon/strategy.py:37` | Unused `timedelta` import. |
| AUD2-L10 | `bots/hydra/brandon/strategy.py:58` | GEX cache TTL comment says "15 min" (Polygon feed delay) but code uses 3 min (effective TTL). Both correct, comment could be clearer. |
| AUD2-L11 | `bots/hydra/strategy.py:_save_state_to_disk` exception path | Currently swallows exceptions with `logger.error(...)`. Returning bool from `_save_state_to_disk()` (like DataRecorder.record_*) so callers can verify persistence would be more robust. |
| AUD2-L12 | `scripts/pre_start_snapshot.sh:59-68` | The `cp → mv` race window is microseconds, but if interrupted leaves a `.tmp` file until the next run's 1-minute cleanup catches it. Acceptable; could add explicit `.tmp` cleanup at START of each run before attempting new snapshot. |
| AUD2-L13 | `shared/data_recorder.py:316-334` | Schema v8 migration is NOT inside a `BEGIN; ... COMMIT` block (HOMER's `db_manager.py:399-410` is). Migration is idempotent + ALTER is forgiving on duplicate-column, so safe — but inconsistent with HOMER's pattern. |
| AUD2-L14 | `shared/position_registry.py:212-215` | `_write_registry()` uses `seek(0) + json.dump + truncate()` (non-atomic). If SIGKILL between seek and truncate, file is partially written. Risk is low (registry is vestigial on IBKR; HYDRA doesn't write to it on this branch), but should match `hydra_state.json`'s `temp + os.replace()` pattern. |
| AUD2-L15 | `bots/hydra/strategy.py:9488-9596` | Entry serialization writes `short_call_position_id: null` etc. for IBKR entries (always None). Adds ~2 KB/day of null fields to state file. Lossless but noisy. |
| AUD2-L16 | `bots/hydra/strategy.py:9456-9457` | `early_close_time` serialization uses a left-to-right `and ... if ... else None` expression. Works in practice (no falsy non-None datetime), but the pattern is fragile. |
| AUD2-L17 | `shared/ib_reconcile.py:1-25` | Module is never called from any code path in scope. Used for forward-compat (future order-reconcile-on-reconnect). Could be marked as "deferred / forward-compat" in a top-of-file comment. |

---

## Fix plan

**Pre-merge (HIGH + actionable MEDIUM):**

1. **AUD2-H1**: Sanitize the connect-log line. Replace `consumer_key=%s` with a fixed `[redacted]` or print only the length (`consumer_key length=%d`) so logs still useful for debugging without leaking the value. Add a regression test.
2. **AUD2-H2**: Rewrite `services/argus/README.md` to match the Polish #2 health_check.sh. Drop the Saxo Check 2/3 rows; add the heartbeat/breaker/backup rows.
3. **AUD2-H3**: Fix `CLAUDE.md:981-982` to say "deleted on this branch; kill-switched on `main`" matching the rest of the file.
4. **AUD2-H4**: Rewrite `scripts/README.md` to mark the migration-era probe scripts as "(historical, completed during F1-F7)" so operators don't try to run them.

5. **AUD2-M1**: Regenerate `requirements-lock.txt` excluding the test stack. Verify pip-audit still clean.
6. **AUD2-M2**: Add a kill-switch / warning header to `setup_vm.sh` saying it's Saxo-era and not validated for IBKR. Document the new procedure (already in `deploy/README.md`).
7. **AUD2-M3**: Fix `README.md:7` line count (~940 → 1000 or `wc -l`).
8. **AUD2-M4**: Add Gate 1 sub-note clarifying "applies to live cutover; paper validation can proceed on the feature branch."
9. **AUD2-M5**: Resolve Variant C E6 inconsistency. Either remove "14:00" from `conditional_entry_times` OR enable one of the conditional flags. The right call depends on intent — defer to user.
10. **AUD2-M6**: Add explicit code comment to `_reconcile_positions` noting DEF-7 dormancy on IBKR. No behavior change.
11. **AUD2-M7**: Wrap the `json.load(f)` at `_load_state_file_history ~10748` in try/except `json.JSONDecodeError`. Log explicit message + fall through to "no history loaded" path (existing behavior on other exceptions).

**Defer/Accept (LOWs):**

- L1, L3, L5, L10: comment fixes — batch into one cleanup commit.
- L4: ProtectDevices=yes — add as a one-line addition to 3 unit files (hardening pass).
- L7, L8: doc-status clarifications — batch with the README fixes.
- L11-L17: code-quality observations; not pre-merge blockers. Document as DEF-8 / DEF-9 for post-merge tightening.

## Round 2 + Round 3 plan

After fixes:
1. Run full test suite (must still be 918+, no regressions).
2. Re-launch the same 6 domain agents on the fixed code (Round 2).
3. Senior overseer signoff (Round 3).
