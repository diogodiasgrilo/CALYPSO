# Running real money alongside paper — design

**Status:** DESIGN, not built. Written 2026-09-18, before any code.
**Goal:** run one or more strategies on a **funded live account** while the
existing paper fleet keeps trading **exactly as it does today**, and make
promoting a strategy from paper to live a config-level action.

> **Read §3 before §4.** The pre-design audit found that most of the plumbing
> already exists (§3.0), which makes this smaller than expected — and four gaps
> in the seams between those pieces, one of which is a live-money safety hole
> (§3.1).

---

## 1. What the operator actually asked for

> "The paper account is going to trade exactly like it's been trading, right?
> Nothing's going to change. We're just going to have a new account that's live."

That is **additive**, not a cutover. It rules out the first design considered
(flip the shared session to live), which would have *stopped* paper trading: B
would trade the live account instead of the paper one, and the paper account
would go idle.

It requires **two concurrent IBKR sessions**.

## 2. Verified IBKR facts

Researched 2026-09-18 rather than assumed. Sources at the bottom.

| | Fact | Consequence |
|---|---|---|
| 1 | Paper and live are **separate usernames** with separate OAuth registrations. IBKR: *"Customers must use their specific Paper username to authenticate."* `ibind`: *"login using your paper credentials… It is advised to not use the same private and public keys between live and paper accounts, but to generate them separately for each account."* | The existing `_keys_dir(environment)` design (separate `paper/` and `live/` key dirs) is already correct. Gate 5's "NEW live keypair, not the paper one re-purposed" is right. |
| 2 | The one-session limit is **per USERNAME**: *"An IB username can only have one brokerage session open at a time."* | Because paper and live are different usernames, **two concurrent sessions are permitted**. This is what makes the whole design possible. |
| 3 | The live account must be **fully open, funded, and of IBKR Pro type**. Pro confirmed by the operator 2026-09-18. | Funding remains the critical path. |
| 4 | Market data is **per account** and starts at **zero** on a live account. The free Cboe One/IEX feed covers **US stocks and ETFs only** — not options, not index levels. | The live account needs OPRA (SPX/SPXW quotes) **and** a CBOE index subscription (SPX/VIX levels), both paid. Without them the live bot is blind — it will not trade badly, it will not trade at all. Exact fees could not be retrieved: IBKR's pricing pages return 403 to automated fetching, so read them logged in. |

## 3. Pre-design audit

### 3.0 ✅ What already exists — most of the plumbing is built

Checked in code, not assumed. The 2026-09-11 work did more groundwork than
expected, and it shrinks this project considerably:

| Already done | Where | Means |
|---|---|---|
| `resolve_environment()` reads `$CALYPSO_IBKR_ENV`, defaults paper, **raises** on a typo rather than guessing | `shared/ib_oauth.py:189` | The broker is **already** environment-parameterized: `load_credentials(resolve_environment())` at `services/broker/main.py:152`. A live broker needs an env var, not a code change. |
| `_keys_dir(environment)` → `{paper,live}/` | `ib_oauth.py:226` | Environment-scoped credentials already exist. §4's `/etc/calypso/ibkr/{paper,live}/` is the systemd-creds mirror of a split the code already makes. |
| `_assert_account_matches_env()` — paper-declared + live-looking account ⇒ **raise**; live-declared + paper-looking ⇒ warn | `ib_client.py:1223` | Asymmetric on purpose, and the asymmetry is correct: only one direction loses real money. |
| `IBClient.account_id` / `.is_paper` properties | `ib_client.py:1363, 1383` | The data S1 needs to publish is **already on the object** — S1 is a few lines, not a subsystem. |
| `CALYPSO_BROKER_HOST` / `CALYPSO_BROKER_PORT` | `services/broker/main.py:75` | A second broker on 8789 needs a unit file and zero code. |
| `HYDRA_VARIANT_ID` → isolated `data/variant_*/` | throughout | A new variant gets its own state, DB and logs for free. |

**This is why the design below is small.** The remaining work is the *seam
between* these pieces — what the broker publishes about itself, and what the
strategy checks — not the pieces themselves.

### 3.1 🔴 A strategy cannot tell which account it is trading

`_probe_health()` (`shared/broker_service.py:264`) returns exactly four fields:

```python
{"status": ..., "connected": ..., "authenticated": ..., "competing": ...}
```

**No environment. No account code.** `BrokerClient.health()` passes it through
and `connect()`/`ensure_connected()` read only `connected`.

So the *only* thing binding a strategy to an account is the
`CALYPSO_BROKER_URL` port in its unit file. A typo silently routes a strategy
to the other account, and **the dangerous direction is a paper-intended variant
reaching the live broker and placing real orders** — nothing anywhere would
notice.

`_assert_account_matches_env()` is the right pattern but sits one layer too low
to help: it validates the broker's own session against the broker's own
declaration. It cannot see that a *strategy* dialled the wrong port.

**Required:** `/health` reports `environment` and the account code — both
already available as `IBClient.account_id` / `.is_paper`, so this is a small
change — and each strategy declares the account kind it expects and **refuses
to start** on a mismatch. Precedent in-tree: `StrangleStrategy` raises in
`__init__` before `super().__init__()` unless `dry_run=true`, then re-checks
after init in case the value was derived (`strangle_strategy.py:76, 86`).

### 3.1b 🟡 The broker hard-codes "paper" in its own startup banner

`services/broker/main.py:149` logs *"calypso-broker starting — paper account,
single shared session"* — three lines above `load_credentials(resolve_environment())`
picks the environment dynamically. A live broker would announce itself as
paper in the journal. Cosmetic today, actively misleading the moment a second
broker exists, and journal lines are what an operator reads during an incident.

### 3.2 🔴 "live" already means something else, and two live seats break the dashboard

`variant_readers.live_seat_id()` is the self-declared *"SINGLE SOURCE OF TRUTH
for the bot"*. It scans a hardcoded `LIVE_SEAT_IDS = ("b", "c")` for configs
with `dry_run=false` and ends:

```python
return live[0] if len(live) == 1 else FALLBACK_SEAT_ID   # FALLBACK_SEAT_ID = "c"
```

**A first draft of this section claimed adding B-live breaks it outright. That
was wrong, and being wrong about it is instructive** — a new `bm` id is *not in
the tuple*, so `live_seat_id()` would not see it and would keep returning `"b"`
correctly. The real finding is a fork where **both branches are wrong**:

- **Leave `bm` out of the tuple** (the default if nobody thinks about it): the
  real-money bot is structurally invisible to "which is the bot" — it can never
  be primary, and `reader_for()`, the WS broadcaster and the agent suite will
  never follow it. Silent, and it degrades the moment live matters most.
- **Add `bm` to the tuple** (what any reasonable person would do, since it
  plainly *is* a live seat): now paper-B and bm are both `dry_run=false`,
  `len(live) == 2`, and the function returns the fallback — the dashboard
  silently declares **C**, a dry-run variant, to be "the bot".

Either way the surfaces downstream inherit it: `_primary_id()`, `is_primary`,
the WS broadcaster, `reader_for()`, and the agent suite's `read_db`. Note also
`PRIMARY_ID = FALLBACK_SEAT_ID` — a static `"c"` alias that "a few call sites
still read" and that never follows a swap at all.

The root cause is a conflation, and it is the **same shape** as the `pnl_shape`
bug that forced the dashboard rebuild: one field carrying two independent facts.

```
dry_run        = does this strategy place REAL ORDERS, or simulate?
account_kind   = are those orders against PAPER or REAL MONEY?      <- missing
```

`status="live"` in the taxonomy currently means *live paper seat*. There is no
representation of real money at all.

**Required:** a new taxonomy axis `account_kind: "paper" | "live_money"`,
defaulting to `paper`, added exactly as `capital_basis` was in Phase 2 — a NEW
field, never an overload of `status`.

That dissolves the fork rather than picking a side, because "which seat is
live" splits into the two questions it always was:

```python
def live_seat_id()        -> str:  # the PAPER seat  — dry_run=false, account_kind=paper
def live_money_seat_id()  -> str | None:  # the REAL-MONEY seat, None until one exists
```

The tuple stops being hardcoded and is derived from the taxonomy, so a future
variant is picked up by existing code instead of needing a `LIVE_SEAT_IDS`
edit that nobody remembers. `PRIMARY_ID` goes with it. Crucially the paper
answer is **unchanged** — `live_seat_id()` still returns `"b"` today — so the
dashboard, agents and alerts keep behaving exactly as they do now.

### 3.3 🟡 The live broker would be unmonitored

Port and log path are both env vars (`CALYPSO_BROKER_PORT`,
`CALYPSO_BROKER_LOG`), so a second broker needs **no code change** to run or to
log separately. The gap is on the watching side:

- **ARGUS scans one hardcoded log.** `services/argus/health_check.sh:65` pins
  `BROKER_LOG="${CALYPSO_DIR}/logs/broker/broker.log"`. Circuit breakers live
  in the broker process and ARGUS finds them by scanning that file, so **a
  breaker opening on the live-money broker would be seen by nobody** — the one
  session where an unnoticed outage costs real money. Either ARGUS loops over
  brokers, or the live broker gets its own check.
- **Point the second broker's log elsewhere.** If both default to the same
  path, two processes rotate one file underneath each other and the post-mortem
  record is interleaved and lossy.
- **`flip_bc_*.sh` hard-code `http://127.0.0.1:8788`** in their health and
  flatness probes. Correct today, and they must stay paper-scoped — but that
  should be a stated invariant, not a coincidence.

**A genuine fail-safe worth keeping:** `BrokerClient.__init__` defaults
`base_url` to `http://127.0.0.1:8788` (`shared/broker_client.py:68`). A unit
that loses its `CALYPSO_BROKER_URL` therefore falls back to **paper**, never to
live. That default must not become dynamic, and a test should pin it.

## 4. The architecture

**Nothing about today changes.** `calypso-broker` stays on 8788 with paper
credentials. All seven units keep pointing at it. **B keeps trading paper
exactly as now** — same record, same Gate 4 streak, same dashboard, same alerts.

Added alongside:

```
  paper (unchanged)                      live money (new)
  ─────────────────                      ────────────────
  calypso-broker        :8788            calypso-broker-live      :8789
    CALYPSO_IBKR_ENV=paper                 CALYPSO_IBKR_ENV=live
    /etc/calypso/ibkr/paper/*.cred         /etc/calypso/ibkr/live/*.cred
        ↑                                      ↑
  hydra, variant_{b,c,d,e,f,g}           hydra_variant_bm   (B, money)
    dry_run per config                     dry_run=false
    account_kind=paper                     account_kind=live_money
                                           contracts_per_entry=1
```

Live is **purely additive**. Rollback is `systemctl stop hydra_variant_bm` —
nothing else is touched. That is a far better rollback story than flipping a
shared session, where recovery means re-authenticating everything.

### 4.1 An unexpected benefit

Paper-B and `bm` run the **same strategy, same signals, same days**, one on
each account. The difference between them *is* the execution drag — currently
estimated at ~30% of B's edge and never measured. This design measures it as a
side effect, which is also the strongest argument for keeping paper-B running
rather than retiring it once real money starts.

## 5. Safety design

Four layers, of which **two already exist**. A mis-pointed unit has to defeat
all of the ones that apply to it.

| | Guard | Status | Why |
|---|---|---|---|
| S1 | `/health` reports `environment` + account code | NEW (small — data is already on `IBClient`) | Without it nothing downstream can verify anything (§3.1) |
| S2 | Strategy refuses to start when the broker's account kind ≠ its declared `account_kind` | NEW | Catches the mis-pointed unit — the one gap nothing else covers |
| S3 | `_assert_account_matches_env()` | **exists** | Declared paper + live account ⇒ raise, before the session is served |
| S4 | The four `SAFETY-DRY` order gates | **exists** | A `dry_run` strategy cannot place an order on ANY session |
| S5 | `contracts_per_entry=1` on bm for week 1 | config | Gate 8 |
| S6 | Live margin via `what_if_order`, replacing the paper-derived `min_buying_power_per_ic=500` | NEW | ORDER-004 gates entries on a number currently calibrated to paper |

Worth being precise about what S3+S4 already cover, because it is most of the
risk: a **dry-run** variant cannot place an order anywhere, and a broker whose
credentials disagree with its declared environment refuses to serve a session
at all. The uncovered case — and the entire reason S2 exists — is a
**non-dry-run** strategy reaching a **correctly-configured** broker for the
**wrong account**.

## 6. Deliberately NOT in scope

- **A daily loss limit.** Measured against B's own 27 post-A2 sessions, a halt
  at −$700 costs −$260 and at −$1,400 costs −$1,400. The A2 %-of-width stop
  already bounds the day: worst session −$1,745 at 7 contracts, which is
  **−$249 at the 1 contract** live week 1 uses. Halting there would stop a +EV
  activity to protect against a rounding error. Revisit when scaling contracts.
- **A second dashboard.** The existing one is taxonomy-driven; a live-money
  variant is another row, once `account_kind` exists.

## 7. Rollout

**Everything in step 1 is buildable now, against paper, with no live money and
no dependency on IBKR.** That is the point of the split: by the time the
account is funded, the only genuinely new thing is a credential file.

1. **Now, in parallel with funding** — all testable on the PAPER account:
   - ✅ **DONE** — S1 `/health` publishes environment + account code; S2
     strategy-side assertion; the §3.1b banner fix; `account_kind` taxonomy axis;
     the `live_seat_id()` / `live_money_seat_id()` split. 49 tests, 13 mutations
     all killed, full suite 4159 passed. No trading behaviour changed: every
     variant still declares paper and the live seat still resolves to `b`.
   - `calypso-broker-live` unit on 8789 — **written and verified statically, NOT
     started** (see the correction below)
   - the bm variant: taxonomy row, registry row, config, unit, dashboard
   - S6 live-margin check

> ### 🔴 CORRECTION (2026-09-18) — "run it on paper first" was WRONG and unsafe
>
> This section originally said to start `calypso-broker-live` **on paper
> credentials** first, "to prove two brokers coexist with zero live-money
> exposure". **Do not do that.** Caught during the audit-before for the unit
> file, not in production.
>
> Two brokers on the paper credentials are two sessions on the **same IBKR
> username**, and fact 2 in §2 is per-USERNAME. They would evict each other in
> exactly the crash-loop `calypso-broker` was built to end — taking the **live
> paper seat offline** in the process. The design is safe precisely *because*
> paper and live are different usernames; pointing both brokers at paper
> destroys the premise it rests on.
>
> **What can genuinely be verified before live credentials exist:**
> the unit file statically (`systemd-analyze verify`), the S1/S2 guards (unit
> tested, 13 mutations killed), and the health-payload contract (against a stub
> `IBClient`, no session at all). **Two brokers actually running concurrently is
> only testable once live credentials exist** — and at that point it is no
> longer a rehearsal, so it gets the full pre-start verification in
> `deploy/IBKR_CREDENTIALS_SETUP.md` and a first start with **no strategy
> pointed at it**.
>
> The one-session limit is also why the live unit must never be handed the
> paper credential paths: a test enforces that the two units share no
> credential file.
2. **When the IBKR chain completes:** encrypt the live credentials into
   `/etc/calypso/ibkr/live/`, set `CALYPSO_IBKR_ENV=live` on the second broker,
   restart it, and confirm `/health` reports `environment=live` with a
   non-`D` account code. Only then start bm, at 1 contract.
3. **Rollback:** `systemctl stop hydra_variant_bm`. Nothing else is touched,
   and paper is unaffected at every step.

The step-1/step-2 boundary is deliberate: step 1 changes **no** trading
behaviour on any existing variant, so it can ship incrementally under the
normal deploy discipline rather than waiting for a big-bang cutover.

## 8. Test plan

- **Two brokers on paper credentials** — the whole design exercised end to end
  with zero live-money risk, and the cheapest possible test of fact 2. If the
  one-session-per-username limit were ever going to bite, it fails here, loudly,
  on paper.
- **S1 contract test** — `/health` reports `environment` + account code, and
  keeps its fail-closed behaviour (any error still degrades to
  `connected: False`; adding fields must not add a raising path).
- **S2 negative test** — a paper-declared unit pointed at the live broker must
  **refuse to start**, not trade. This is the test the whole design exists for,
  so it gets the adversarial treatment: mismatch in both directions, and a
  broker that answers `/health` without the new fields at all (an old broker
  against a new strategy — the exact 2026-06-08 deploy-order bug shape, where a
  strategy forwarded something an un-restarted broker could not serve).
- **`live_seat_id()` regression** — with paper-B and bm both
  `dry_run=false`, the paper seat still resolves to `"b"`. This is the
  "changes nothing today" guarantee; it should fail loudly if that stops being
  true.
- **`BrokerClient` default stays paper** — pin `base_url`'s default, so the
  fail-safe in §3.3 cannot be refactored away silently.
- **ARGUS sees both brokers** — a breaker-OPEN line in the live broker's log
  must produce an alert.
- **Mutation testing on every new guard**, per standing practice: each
  assertion is verified to fail when the behaviour it claims to protect is
  removed. A guard that has never been seen to fail has not been tested.
- **Full suite + chaos test (RB-10)** against the two-broker topology.

## 9. Decisions taken (operator, 2026-09-18)

### 9.1 The agent suite follows the REAL-MONEY seat

**Decided against the recommendation in this doc's first draft, deliberately.**
The proposal was to keep HERMES/CLIO/HOMER on paper-B for statistical
continuity; the operator chose to repoint them at real money once it runs. That
is a coherent preference — the nightly analysis should be about the account
that can actually lose money — and the cost is understood and accepted:

- the first weeks of reports analyse a handful of 1-contract trades, so
  **treat early HERMES/CLIO conclusions as anecdote, not signal**;
- `docs/HYDRA_TRADING_JOURNAL.md` becomes the **real-money** record from the
  switchover date. Worth a dividing line in the journal on that day so the two
  eras are never read as one series.

*Implementation:* `read_db` is a literal path string in `agents_config.json`
(`"read_db": "data/variant_c/backtesting.db"` in the template — note the
template is **stale**, production was repointed to `variant_b` at the 2026-07-24
swap). Switching is a one-line VM edit to `data/variant_bm/backtesting.db` at
step 2. Any code that derives this should follow `live_money_seat_id()` when one
exists and fall back to the paper seat otherwise — and the stale template should
be corrected in the same pass, since the repo currently lies to the next reader.

### 9.2 A money marker leads every real-money alert

Every alert from `bm` is prefixed so it is unmistakable in a phone notification
preview, where the first characters are all you get.

*Implementation:* reuse the existing mechanism rather than inventing one —
`alert_service.py:555-559` already auto-prefixes `[{N}c]` to the title when
`contracts > 1`, for exactly this "readable on a phone" reason. The marker goes
in the same place.

*A trap checked and cleared:* `_VOLATILE_TOKEN_RE` (line 966) strips `[Nc]`,
dollar amounts and percentages before fingerprinting for dedup, so the obvious
worry is a real-money marker being stripped and a real alert collapsing into a
paper one. It cannot happen — `self._dedup_last` is **instance** state
(line 306), and paper-B and `bm` are separate processes with separate
`AlertService` instances, so their dedup caches never meet. The marker's
treatment in that regex is therefore immaterial; no change needed.

### 9.3 The variant id is `bm` — "B, money"

Chosen over `b_live` specifically to avoid the word **live**, which in this
codebase already means *the live PAPER seat*. Reusing it would produce "the live
seat" and "B-live" meaning different things in the same sentence — the exact
conflation §3.2 exists to undo. `bm` also fits the dashboard's letter badge,
which renders `id.toUpperCase()` in a space built for one or two characters.

Pairs naturally with `account_kind="live_money"`. Data lands in
`data/variant_bm/`, logs in `logs/hydra_variant_bm/`, unit
`hydra_variant_bm.service`.

**Integration requirement found while building §3.2** (surfaced by a test fixture,
not by reading): the dashboard's `Settings` is a pydantic model with `extra`
disallowed, so `variant_bm_*` attributes cannot simply appear at runtime —
`setattr` raises `ValueError: "Settings" object has no field "variant_bm_config_file"`.
Adding `bm` therefore requires **explicit `variant_bm_state_file` /
`_config_file` / `_backtesting_db` / `_metrics_file` / `_log_file` fields in
`dashboard/backend/config.py`**. The taxonomy row alone is not enough, and the
failure mode is silent in the other direction: without those fields the seat
resolver simply skips `bm` and it stays invisible — exactly the §3.2 branch
where the real-money bot is never followed by anything.

---

**Sources.** [IBKR — Paper Accounts](https://www.interactivebrokers.com/docs/web-api/authentication/paper) ·
[IBKR — Session Authentication](https://www.interactivebrokers.com/docs/web-api/authentication/sessions) ·
[IBKR — Managing Multiple Sessions](https://www.interactivebrokers.com/docs/web-api/authentication/multiple-sessions) ·
[ibind OAuth 1.0a](https://github.com/Voyz/ibind/wiki/OAuth-1.0a) ·
[IBKR OAuth 1.0a Extended](https://www.interactivebrokers.com/campus/ibkr-api-page/oauth-1-0a-extended/) ·
[Market Data Subscriptions](https://www.interactivebrokers.com/docs/general/market-data-subscriptions/understanding-market-data-subscriptions)
