# Running A/B/C concurrently on IBKR — the one-session-per-username constraint

**Status:** decided 2026-05-29. Chosen approach for paper: **Option 2 — one IBKR
paper username per strategy.** Target for live / shared-capital: **Option 1 —
single shared broker-session service** (deferred).

## The problem (observed at cutover, 2026-05-29)

Strategy **A** alone runs fine on IBKR. With **A + B + C** running (3 systemd
processes, all using OAuth 1.0a consumer key `CALYPSOPP` against one paper
account `DUR049068`), all three crash-loop: `/iserver/auth/ssodh/init` →
`410 Gone`, `auth/status` → `competing` / `Invalid_username_or_password`.

## Root cause (IBKR primary docs)

- **The brokerage session is the contended resource, not the Live Session Token.**
  The LST is a per-consumer-key 24h HMAC *signing key*; it does not establish a
  trading session. Sharing/duplicating LSTs does not help.
- **"Only a single active brokerage session can exist for any username across all
  IBKR services."** Opening a new one (`ssodh/init`) closes the existing one.
  — IBKR Client Portal Web API reference (cpapi-v1).
- `ssodh/init` param **`compete:true`** = *"disconnect other brokerage sessions to
  prioritize this connection."* So three processes on one username perpetually
  evict each other. `compete:false` does **not** let three coexist either.
- Both order placement **and market data** live behind `/iserver/*`, which needs
  the one brokerage session — so even dry-run B/C (which only need quotes)
  contend.
- **Isolate by USERNAME, not consumer key** — a second consumer key under the
  same login still maps to the same single per-username session. Confirmed via
  5-agent research (2026-05-29); sources: IBKR `oauth.pdf`, OAuth-1.0a Extended,
  cpapi-v1 reference; Voyz/ibind has no cross-process coordination.

This is why the Saxo era had a `token_keeper`/coordinator (shared one token
across bots); deleting it on the IBKR branch exposed the constraint.

## Option 2 — one paper username per strategy (CHOSEN for paper)

Each variant gets its OWN IBKR paper username + OAuth registration → its own
brokerage session → no contention (verify `competing:false` on all three).

- **Why it fits now:** B/C are dry-run *shadows* (no real orders; P&L simulated
  locally), so the usual downside of separate accounts — split balance/positions
  — is moot. They still see the same SPX/VIX market, so the A-vs-B-vs-C
  comparison holds. Near-zero code: each systemd process keeps its own
  `IBClient`, just different creds.
- **Wiring (already in the repo):** `hydra_variant_b.service` loads creds from
  `/etc/calypso/ibkr-b/`, `hydra_variant_c.service` from `/etc/calypso/ibkr-c/`
  (A stays on `/etc/calypso/ibkr/`). The credential *IDs* (`ibkr_consumer_key`
  …) are unchanged — `shared/ib_oauth.load_credentials` reads them by ID from
  `$CREDENTIALS_DIRECTORY`, so only the source `.cred` paths differ per account.
- **Cost:** maintain 3 OAuth credential sets; per-username market-data
  subscriptions (negligible on paper; would be per-account fees on live).

### Operator provisioning (one-time, per variant B and C)
1. Create a new IBKR **paper username** (Client Portal → Settings → Users &
   Access Rights → add user; or a separate paper account). One per variant.
2. For each, complete OAuth 1.0a **self-service registration** (same as
   `CALYPSOPP` for A): choose a consumer key, generate signing + encryption
   private keys + a `dhparam`, upload the public keys, and obtain the
   `access_token` + `access_token_secret`.
3. Keep each set on the laptop, e.g. `~/ibkr-oauth/paper-b/` and
   `~/ibkr-oauth/paper-c/` (PEMs) + note each consumer key + tokens.
4. Hand off to deploy: encrypt each set into `/etc/calypso/ibkr-b` and
   `/etc/calypso/ibkr-c` on the VM (same `systemd-creds` flow as A — see
   `GATE2_DEPLOY_RUNBOOK.md` Phase 4), then `systemctl enable --now
   hydra_variant_b hydra_variant_c` and confirm `auth/status` shows
   `competing:false` for all three.

> ⚠️ New OAuth consumer keys may have an **activation delay** (A's key took
> ~days to activate). Provision early.

## Option 1 — single shared broker-session service (TARGET for live)

One `calypso-broker` process owns the single `IbkrClient` (one LST, one
`ssodh/init`, one tickler). A/B/C stop owning sessions and request
quotes/orders from it over loopback RPC; centralize reconciliation + a global
risk gate. This is the production-grade single-account pattern and the clean
IBKR-era replacement for the deleted `token_keeper`. Build it before A/B/C ever
trade **live, shared capital** (where split accounts are unacceptable and one
funnel for risk/reconciliation is required). Bigger engineering effort — write
an ADR + design first.

## Ruled out
- More consumer keys on the same username (limit is per username).
- IB Gateway + multiple `clientId`s (works, but abandons the headless-OAuth
  design and requires running/monitoring a Gateway).
