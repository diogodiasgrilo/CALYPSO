# P7 — Go-Live Plan: HYDRA on Interactive Brokers

**Status**: 📋 in progress — Step 3 ✅, Steps 1–2 / 4–6 pending
**Date**: 2026-05-22
**Predecessors**: F1–F7, P2-full, P4, P5b, P5c, P6 (code/scripts docs) —
all ✅. `bots/hydra/` is 100% Saxo-free; 848 repo tests green.

P7 takes the merged, Saxo-free HYDRA from "code-complete on a branch"
to "validated and running on the VM against IBKR", then merges to
`main`.

---

## 1. Research basis (2026-05-22)

Three web-research agents verified the operational unknowns. Key
findings, with the implications that shaped this plan:

### ibind / IBKR session lifecycle (24/7 operation)
- ibind's **Tickler auto-runs** (60s `/tickle`) and holds off the
  ~6-minute *idle* timeout — including overnight.
- **But the session still drops** at IBKR's ~01:00 ET daily server
  reset and at the 24h live-session-token TTL. The Tickler is a
  timeout-preventer, not a reset-survivor.
- ibind does **not** proactively rotate the live session token; it
  only re-auths *reactively* (`handle_auth_status()` on a detected
  failure). Nothing calls that on a schedule.
- → **Implication**: HYDRA needs a morning re-auth gate. **Done in
  Step 3.** Recommended `systemd`: `Restart=always`, `RestartSec=30`,
  `After=network-online.target`, `KillSignal=SIGTERM`,
  `TimeoutStopSec=30` (let ibind's SIGTERM handler run `oauth_shutdown`).

### IBKR paper-account market data — the critical-path blocker
- A paper account gets **only 15-minute delayed data** unless its
  linked **live** account is funded + IBKR Pro, holds the
  subscriptions, and *Paper Trading data-sharing* is enabled (≤24h to
  propagate).
- Subscriptions required on the live account: **OPRA** (SPX/SPXW
  option quotes) + its prerequisite **US Securities Snapshot & Futures
  Value Bundle**; **CME S&P Indexes L1** (SPX index spot + the
  "underlying" entitlement for IBKR-computed greeks); **CBOE Streaming
  Market Indexes L1** (VIX).
- **Concurrent-login trap**: if the *live* username is logged in
  anywhere (TWS/mobile/another API client) while the bot runs on the
  paper username, the paper session silently drops to delayed data.
- **Delayed data is fatal** for a 0DTE bot (stale SPX/VIX/option
  prices break strike selection, the credit gate, and stop timing).
- The `/iserver/marketdata/snapshot` endpoint: preflight
  `/iserver/accounts` + `/iserver/secdef/search`; the first call per
  conid set returns no data (subscription priming) — retry; the
  underlying market-data topic silently expires after ~15 min — must
  re-prime; intermittent HTTP 500 *"Please query /accounts first"*.
- **Open question**: the snapshot endpoint may expose only implied
  volatility (field 7633), not delta/gamma/theta/vega. HYDRA writes
  greeks to the analytics DB only (not trading decisions), so this is
  not fatal — but `_read_option_greeks` behaviour must be verified.

### Secrets deployment (single GCE VM, systemd service)
- Best: `systemd LoadCredentialEncrypted=` — no `/proc/<pid>/environ`
  exposure, not inherited by child processes, tmpfs at runtime,
  encrypted at rest, no GCP API call at startup.
- Acceptable / lowest-effort: a root-owned `0600` `EnvironmentFile`
  for the string secrets + mode-`0600` PEM key files on the
  (already-encrypted) GCE disk — matches the existing `polygon.env`
  precedent. `ib_oauth.py` already reads PEM files from
  `$CALYPSO_IBKR_KEYS_DIR/{paper,live}/` + env vars, so this path
  needs no code change.
- Secret Manager: not overkill, not required — its win here is audit
  logging + rotation + keeping secrets out of VM snapshots.

Full agent reports + source citations: see the session record /
`docs/migration/IB_OPEN_QUESTIONS_ANSWERED.md` (which already settled
the architecture: OAuth 1.0a runs gateway-free, headless, unattended
for retail/paper — no IB Gateway, no token-keeper service).

---

## 2. The steps, in order, and why

### Step 1 — IBKR account prerequisites *(user-side; longest lead time)*
Link the paper account to a funded **IBKR Pro** live account; add the
four market-data subscriptions on the **live** account (OPRA + US
Securities Snapshot Bundle + CME S&P Indexes L1 + CBOE Streaming
Indexes L1); enable **Paper Trading data-sharing** and wait ≤24h;
confirm the OAuth 1.0a paper credentials are registered + activated
(activation can take up to ~2 weeks — see `IB_OPEN_QUESTIONS_ANSWERED`
§Q1).

*Why first*: multi-day to multi-week lead times, and the bot is
useless on delayed data — nothing downstream is meaningful until the
account genuinely delivers real-time SPX/VIX/option data.

### Step 2 — verify market data + the greeks question *(probe)*
Once Step 1 has propagated, run `scripts/probe_ibkr_chain.py` against
the paper account to confirm: real-time SPX spot, VIX level, and SPX
0DTE option bid/ask/last all flow; and whether the snapshot endpoint
returns greeks or only IV. Decide if `_read_option_greeks` needs a
local Black-Scholes fallback (the strategy already has the BS
machinery).

*Why here*: cheap, and it confirms the Step 1 entitlements actually
reached the paper account before any deploy.

### Step 3 — morning re-auth gate ✅ *(done — code)*
`IBClient.ensure_connected()` + a once-per-trading-day gate in
`main.py`. Closes the session-lifecycle gap (daily reset / 24h TTL).
Committed; +6 tests.

### Step 4 — secrets + systemd service *(code + ops)*
Choose the secrets mechanism (recommend: `EnvironmentFile` + mode-600
PEM files to match `polygon.env` and avoid a code change; or
`LoadCredentialEncrypted=` for best security). Write
`deploy/hydra.service` with the research's `systemd` settings. Delete
the obsolete Saxo OAuth infrastructure: `deploy/token_keeper.service`,
`shared/token_coordinator.py`, `services/token_keeper/` — OAuth 1.0a
needs no keeper service.

### Step 5 — VM deploy + staged paper validation *(ops)*
Deploy the branch to the VM; install IBKR paper credentials; start the
service. Validate in stages: read-only probes → a dry-run trading day
→ one real paper entry + monitored exit. Watch for the snapshot
priming / 15-min re-prime / HTTP-500 quirks from the research.

### Step 6 — docs + final audit + merge *(docs + review)*
Rewrite `CLAUDE.md` for the standalone IBKR HYDRA (one bot, IBKR, IBKR
VM commands, no Saxo/MEIC/multi-bot/Token-Keeper), plus
`bots/hydra/README.md` and the strategy specs — done now because the
operational picture is concrete. Run a final multi-agent audit of the
whole branch. Merge to `main`.

---

## 3. Sequencing rationale

Steps 1–2 are **user-side and slow** — they gate everything and start
now, in parallel with the code work. Step 3 (code) is done. Step 4 is
code/ops that can proceed any time before deploy. Step 5 needs Steps
1–4 complete. Step 6 is last so the docs describe the *actually
deployed* system, not a guess — which is exactly why P6 deferred the
`CLAUDE.md` rewrite to here.

## 4. Open items / deferrals carried into go-live

- **DEF-4** (STATE-002), **DEF-6** (settlement P&L value-check — no
  IBKR closed-positions report), **DEF-7** (POS-003 mid-session
  reconciliation still position_id-keyed) — see `DEFERRED_WORK.md`.
- During-day 401/410 handling: the morning gate (Step 3) covers the
  daily reset and resets the 24h TTL each morning, so a mid-day token
  expiry is unlikely. A belt-and-suspenders "`ensure_connected()` on a
  401/410 from `_ib_call`" is a possible follow-up, not a blocker.
- Snapshot subscription re-priming (~15-min topic expiry): confirm
  HYDRA's REST snapshot path re-primes; verify during Step 5.
