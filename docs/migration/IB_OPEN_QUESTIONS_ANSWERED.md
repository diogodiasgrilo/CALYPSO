# IB Migration — Open Questions, Answered

> **Status**: 10 open questions resolved — the original 6 (Q1–Q6) plus four follow-ups (Q7–Q10) added on the 2026-05-14 deep-dive into CP API combo orders, streaming, margin, and error lifecycle. One finding is architecture-changing: **OAuth 1.0a first-party retail works gateway-free**, which eliminates the IB Gateway + IBC + weekly phone tap from our deployment.
>
> **Action**: this doc supersedes the "open questions" section at the bottom of `INTERACTIVE_BROKERS_API_REFERENCE.md` and changes Phase 1 of `SAXO_TO_IB_MIGRATION_PLAN.md`. The migration plan has been amended accordingly.
>
> **Compiled**: 2026-05-13; **last updated**: 2026-05-14.
>
> **Deep-dive scratch files** (verbatim agent research):
> - [`06_oauth_and_2fa_answers.md`](./research_scratch/06_oauth_and_2fa_answers.md) — Q1 + Q4
> - [`07_key_rotation_and_index_sub.md`](./research_scratch/07_key_rotation_and_index_sub.md) — Q2 + Q3
> - [`08_orf_and_ledger_usd.md`](./research_scratch/08_orf_and_ledger_usd.md) — Q5 + Q6
> - [`09_cpapi_combo_orders.md`](./research_scratch/09_cpapi_combo_orders.md) — Q7 (combos)
> - [`10_cpapi_streaming.md`](./research_scratch/10_cpapi_streaming.md) — Q8 (streaming)
> - [`11_cpapi_margin_account.md`](./research_scratch/11_cpapi_margin_account.md) — Q9 + Q10 (margin / portfolio_summary throttling)
> - [`12_ibind_errors_lifecycle.md`](./research_scratch/12_ibind_errors_lifecycle.md) — error-class catalogue + breaker policy
> - [`13_invalid_consumer_diagnosis.md`](./research_scratch/13_invalid_consumer_diagnosis.md) — registration / activation latency

---

## TL;DR — the 10 answers

| # | Question | Answer | Migration impact |
|---|---|---|---|
| 1 | Gateway-free OAuth 2.0 for retail live trading? | **No for OAuth 2.0** (institutional/vendor only; no ETA for retail). **YES for OAuth 1.0a "Extended" first-party.** | **Architecture change**: drop Gateway/IBC, use `ibind` + OAuth 1.0a |
| 2 | Public-key rotation cadence | **Not documented** — keys are valid until manually rotated. Multiple keys per `client_id` via `kid`. Self-impose **12-month rotation** via Message Center tickets. | Runbook item, not code |
| 3 | Cboe Streaming Market Indexes monthly fee | **~$1.50/mo non-pro for VIX only.** **SPX is a SEPARATE subscription** (CME S&P Indexes, ~$1.50-3/mo). **Total ~$3-5/mo for SPX + VIX live.** | Cost model + data-sub list correction |
| 4 | Unattended week+ session for retail live? | **TWS/Gateway path: NO** (weekly Sunday IBKR Mobile tap is enforced, no documented bypass). **OAuth 1.0a path: YES** (long-lived tokens, SDK rotates the 24h live-session-token cryptographically — no phone, no browser). | Drives the architecture choice in Q1 |
| 5 | 2026 ORF on Cboe for SPX, sell-side | **$0.0023/contract**, effective Jan 2 – Jun 30 2026. Reverts to $0.0017 on Jul 1 unless extended. Section 31 = $20.60/$M of notional sales (post-Apr 4 2026). **The dominant fee is the $0.45 CBOE SPX Index Option Surcharge (both sides)** — ~200× the ORF. | Cost-model line items locked in |
| 6 | EUR-base account, live USD-tradable | **SUPERSEDED — see Q10.** Original answer (now obsolete) recommended `reqAccountSummaryAsync` with a 3-minute throttle. Under CP API + ibind we use `portfolio_summary` + `get_ledger` with **no 3-minute throttle** (TWS API's restriction does NOT carry over). See Q10 for the current design. | Pre-trade BP gate design (see Q10) |

---

## Q1 + Q4 detail — OAuth 1.0a is the answer to both

### Verified facts (with direct source citations)

**OAuth 2.0 for individual retail trading**: Per IBKR's own *Trading Web API* docs (current May 2026 wording, confirmed across 4 secondary citations including ibind wiki, codestudy.net, QuantConnect forum, PickMyTrade blog):

> "Retail clients are currently only approved to access the Web API using the Client Portal Gateway. While OAuth 1.0a is expected to firmly stay in the Institutional space, OAuth 2.0 is being considered for individual access in the future. However, there is no ETA at this time."

Pull request [`ibind#106`](https://github.com/Voyz/ibind/issues/102) (OAuth 2.0 SDK support) is **still open** as of May 2026, no maintainer commitment, no merge — because there's nothing for retail to wrap.

**OAuth 1.0a for individual retail trading**: Direct quote from the [`Voyz/ibind` wiki OAuth-1.0a page](https://github.com/Voyz/ibind/wiki/OAuth-1.0a), verified via `curl https://raw.githubusercontent.com/wiki/Voyz/ibind/OAuth-1.0a.md`:

> "Despite the website and some IBKR support agents claiming otherwise, indeed it seems to be possible to use OAuth 1.0a on individual accounts. Many individual account users reported successfully registering both live and paper credentials for OAuth 1.0a. This is what one API support agent said in this regard: 'I'm not aware of any technical limitations which would prevent an individual accountholder from accessing the OAuth 1.0a self-service portal (in either paper/live mode)'."

Verified directly from the README on GitHub:

> "IBind supports fully headless authentication using OAuth 1.0a. This means no longer needing to run any type software to communicate with IBKR API."

**Sunday phone tap status on TWS/Gateway path**: Per [IBKR's own guide page](https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm):

> "If you have elected to have your trading platform restart automatically on a daily basis, this procedure will require manual authentication once a week, the first time you log into the platform after the security tokens have been invalidated. **This security process occurs each Sunday at 1:00 am ET.**"

QuantConnect, PickMyTrade (2026 guide), IBC issue #161, ibeam issue #14 all confirm: no path to bypass this on TWS/Gateway. Enabling "IB Key only" doesn't disable the weekly tap — it only controls *how* you tap.

### Why OAuth 1.0a is unattended

When you authenticate via OAuth 1.0a, the **access token + access token secret** you stored at registration time *are* the credential. The library signs requests with your RSA private key to ask IBKR for a fresh **live session token** (~24h TTL). This handshake is pure cryptography — no phone, no browser, no human. The Saturday-night-into-Sunday server reset still happens server-side, but from the client's perspective the next OAuth handshake on Sunday morning just works.

> "The goal of the authorization flow is to establish automatically-expiring live session tokens without requiring user re-authorization."
> — [IBKR OAuth design PDF, 2018](https://www.interactivebrokers.com/webtradingapi/oauth.pdf) (still authoritative for 1.0a in 2026)

### The "pyCrypto catch" — verified safe 2026-05-14 (no action needed)

The ibind wiki page once warned that the OAuth 1.0a implementation relied on `pyCrypto` (unmaintained, known CVEs). **Verifying against ibind 0.1.23's actual package metadata flipped this**: `ibind[oauth]` explicitly requires `pycryptodome>=3.21` — pycryptodome is the maintained fork that exposes the same `Crypto.*` import namespace, so the wiki text was misleading. We verify at startup via `shared.ib_oauth.assert_safe_crypto_backend()` (asserts `Crypto.__version__` is the pycryptodome line, not legacy 2.x pyCrypto). No fork needed.

### Activation latency

The wiki notes (with linked user reports) that OAuth access can take **up to 2 weeks** to activate after registration. One user said: "consumer keys will only be activated after the server restart each weekend." **Add 2 weeks of slack to Phase 0** of the migration timeline.

### Other operational facts about OAuth 1.0a

- Setup is fully self-service via the [IBKR OAuth setup page](https://ndcdyn.interactivebrokers.com/sso/Login?action=OAUTH&RL=1&ip2loc=US) — pick a 9-character A-Z consumer key, upload PEM-encoded signing + encryption public keys + DH parameters, click "Generate Token", copy access token + secret.
- Access tokens are long-lived. Live session tokens auto-rotate every ~24h via the SDK.
- `/tickle` heartbeat required every ~60s (recommended) to keep the brokerage session warm. `ibind`'s `Tickler` thread does this.
- Brokerage session opened via `POST /iserver/auth/ssodh/init` after the OAuth handshake.
- **Use distinct keypairs for paper and live** — IBKR's docs explicitly require this.
- Use the **US IBKR domain** for OAuth setup if you live outside the US: `https://ndcdyn.interactivebrokers.com/sso/Login?action=OAUTH&RL=1&ip2loc=US`.

### Risk: IBKR could close the loophole

IBKR's official line is "OAuth 1.0a is institutional only." The operational reality (retail self-service works) is well-documented but not endorsed. **IBKR could change policy unilaterally**, e.g., closing new self-service OAuth registrations to non-institutional accounts. This is the main risk that makes us keep an IB Gateway + IBC + Watchdog fallback path documented in the migration plan, even if we run OAuth 1.0a primary.

---

## Q2 detail — OAuth 2.0 key rotation (for if/when we move)

Already mostly moot given the Q1 finding (we'll be on OAuth 1.0a, not 2.0). But for the future:

- IBKR does NOT publish a forced rotation cadence. Keys remain valid until manually rotated.
- Multiple keys per `client_id` are supported (IBKR Campus docs say "key(s)" plural). Each key has a `kid` (Client Key ID) used in the JWT header to identify which registered key signed the assertion.
- **Algorithm**: RS256 (RSA SHA-256), 2048-bit RSA minimum, 3072-bit recommended by `ibauth`. **No ECDSA support documented.**
- **Format**: PEM. No JWKS endpoint.
- Key management is **manual via Message Center tickets** — no programmatic upload, no auto-rotation API. Approval turnaround 1-3 business days.
- **Distinct keys for paper vs production** required by IBKR.
- **Compromise revocation**: no published SLA. Realistically business-hours-bound. Mitigate via IP allowlist (the `ip` claim in the 24h SSO JWT suggests this is enforced).

Two distinct JWT shapes in use, extracted verbatim from working `ibind` PR #106:

```python
# Shape 1 — for POST /oauth2/api/v1/token (60-second TTL)
{
    "iss": client_id, "sub": client_id, "aud": "/token",
    "exp": now + 60, "iat": now - 10,  # 10s clock-skew slack
}

# Shape 2 — for POST /gw/api/v1/sso-sessions (24-hour TTL)
{
    "ip": public_ip, "credential": ibkr_username,
    "iss": client_id, "exp": now + 86400, "iat": now,
}
```

Recommendation: **self-impose 12-month rotation** using the overlap procedure (register new key, deploy alongside old, verify, remove old via second Message Center ticket).

---

## Q3 detail — Market data subscriptions (CORRECTION to original plan)

### CRITICAL CORRECTION

The original [`INTERACTIVE_BROKERS_API_REFERENCE.md`](./INTERACTIVE_BROKERS_API_REFERENCE.md) implied a single "Cboe Streaming Market Indexes" subscription covered live SPX. **This is wrong.** That SKU only delivers **VIX** Level 1. SPX live data is on a **separate** subscription.

### Verified subscription list for live SPX + VIX

| Subscription (IBKR's name) | What it delivers | ~Cost non-pro |
|---|---|---|
| **CBOE Streaming Market Indexes** | VIX (Level 1 streaming) | ~$1.50/mo [unconfirmed exact] |
| **CME S&P Indexes (Level 1)** | SPX, NDX (Level 1 streaming) | ~$1.50-3.00/mo [unconfirmed exact] |
| **OPRA Top of Book** | Streaming bid/ask on all US options (covers SPXW chain) | $1.50/mo — **waived ≥$20/mo commissions** |
| **US Securities Snapshot Bundle** | Snapshot quotes (not streaming) — **NOT suitable for 0DTE** | $10/mo — waived ≥$30/mo commissions |

**Total monthly cost for live SPX + VIX + OPRA chain**: roughly **$3-6/mo non-pro**, before any commission waivers. After waivers kick in (>$30/mo commissions, easily exceeded), only the index SKUs remain — typically $3-5/mo.

The QuantConnect IBKR docs are the clearest third-party reference confirming the VIX/SPX SKU split: https://www.quantconnect.com/docs/v2/cloud-platform/datasets/interactive-brokers

### Pro vs non-pro

For CALYPSO purposes the operator qualifies as **non-pro** (individual, not employed in financial-services capacity, not registered with a regulator). Pro pricing is ~10x non-pro, irrelevant unless registration status changes.

### Action items

1. **Subscribe to BOTH** CBOE Streaming Market Indexes AND CME S&P Indexes during Phase 0.
2. **Do NOT rely on the US Securities Snapshot Bundle** — it's snapshot-only, not streaming.
3. **Verify "Non-Professional" status** is correctly set in Client Portal → Settings → Market Data Subscriptions → Subscriber Status **before subscribing**. Wrong status = 10-50× price difference and a common own-goal.
4. **Verify exact prices in the Client Portal Subscription Center** on day 1 of account access — IBKR's public pricing pages are login-gated for the precise current numbers.

---

## Q5 detail — 2026 fees on SPX

### Headline numbers (lock these into the cost model)

For one SPX contract:

| Fee | Rate | Sides | Source |
|---|---|---|---|
| **Cboe ORF (regulatory)** | **$0.0023/contract** | sell only | [Cboe Jan 2 2026 notice](https://cdn.cboe.com/resources/fee_schedule/2026/Cboe-Options-Exchanges-Regulatory-Fee-Update-Effective-January-2-2026.pdf) (effective Jan 2 - Jun 30 2026, reverts to $0.0017 on Jul 1 unless extended) |
| Cboe Trade Processing Service | $0.0025/contract | both | [Cboe Fee Schedule May 1 2026](https://cdn.cboe.com/resources/membership/Cboe_FeeSchedule.pdf) |
| **Cboe SPX Index Option Surcharge** | **$0.45/contract** | both | Same — **this is the dominant fee, dwarfs ORF by ~200×** |
| SEC Section 31 (post-Apr 4 2026) | $20.60/$M notional sales | sell only | [SEC Section 31 FY2026 advisory](https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2) |
| FINRA TAF | $0.00279/contract (capped $9.05/order) | sell only | [FINRA Schedule A](https://www.finra.org/rules-guidance/guidance/faqs/trading-activity-fee) |
| OCC Clearing | $0.025/contract (monthly cap $55/member) | both | [OCC Schedule of Fees](https://www.theocc.com/company-information/schedule-of-fees) |
| IBKR commission (Fixed) | $0.65/contract | both | [IBKR Options Commissions](https://www.interactivebrokers.com/en/pricing/commissions-options.php) |
| IBKR commission (Tiered, ≤10K/mo) | $0.65/contract → $0.25 above 50K/mo | both | Same |

### Total per-side execution cost for our 10c IC

**Opening 4 legs × 10 contracts (BUY side)**:
- IBKR commission: $0.65 × 40 = **$26.00**
- Cboe Trade Processing: $0.0025 × 40 = $0.10
- Cboe SPX Index Option Surcharge: $0.45 × 40 = **$18.00**
- OCC Clearing: $0.025 × 40 = $1.00
- *(no ORF/Section 31/TAF on buy side)*
- **Open total: ~$45.10**

**Closing 4 legs × 10 contracts (SELL side, premium $0.50/contract avg → $2,000 notional)**:
- IBKR commission: $0.65 × 40 = $26.00
- Cboe Trade Processing: $0.0025 × 40 = $0.10
- Cboe SPX Index Option Surcharge: $0.45 × 40 = $18.00
- OCC Clearing: $0.025 × 40 = $1.00
- ORF: $0.0023 × 40 = $0.09
- FINRA TAF: $0.00279 × 40 = $0.11
- Section 31: $20.60 × $2,000 / $1,000,000 = $0.04
- **Close total: ~$45.34**

**Round-trip per 10-lot IC: ~$90-94.** Dominated by IBKR commission ($26 × 2 = $52) and Cboe SPX Index Option Surcharge ($18 × 2 = $36).

### IBKR markup policy (verbatim from IBKR docs)

Regulatory fees (ORF, Section 31, TAF, OCC) **passed through at cost** under industry convention. Non-regulatory exchange fees (Trade Processing Service, Execution Surcharge, maker/taker rebates) are **NOT a direct pass-through** under IBKR's Tiered plan:

> "IBKR's Tiered commission models are not intended to be a direct pass-through of exchange and third-party fees and rebates, and costs passed on to clients in IBKR's Tiered commission schedule may be greater than the costs paid by IBKR to the relevant exchange, regulator, clearinghouse or third party."
> — [IBKR Other Fees](https://www.interactivebrokers.com/en/pricing/other-fees.php)

For 0DTE iron condors at current volume (<10K contracts/mo), **Fixed plan is cheaper than Tiered** because Tiered's rebate structure assumes liquidity provision that 0DTE entries don't typically achieve. Re-evaluate if monthly volume exceeds 10K contracts.

### Quarterly re-check needed

The ORF rate filing expires June 30, 2026. A new methodology is under SEC review for July 1, 2026 onwards. **Re-verify the rate at the end of June** before the next quarterly cost-model refresh.

---

## Q6 detail — EUR base, USD options, live tradable

> ⚠️ **SUPERSEDED — see Q10 (2026-05-14 update at bottom).** The TWS-API-based code in this section (ib_async / `reqAccountSummaryAsync` / 3-minute throttle) describes the OLD architecture before the migration pivot to CP API + ibind. Under the current design, throttle is gone and the call surface is `portfolio_summary` + `get_ledger`. Kept here for the historical reasoning only; do not implement against this code.

### The hard truth

**There is no single `accountSummary` tag that returns the live USD-tradable amount directly.** The TWS data model exposes:

- **Summary tags** (`AvailableFunds`, `BuyingPower`, `ExcessLiquidity`, `MaintMarginReq`, `NetLiquidation`, etc.): **always in the account's base currency** (EUR for our case). There is no `BuyingPower@USD` variant.
- **`$LEDGER` pseudo-tag**: per-currency cash-ledger view. Three flavors:
  - `$LEDGER` — base currency row only
  - `$LEDGER:USD` — USD row only (with `CashBalance`, `ExchangeRate`, etc.)
  - `$LEDGER:ALL` — every currency the account has activity in

### The computation

```python
USD_tradable = EUR_AvailableFunds × ExchangeRate(USD per EUR)
             + USD_CashBalance (from $LEDGER:USD if any)
```

IBKR internally does this conversion when checking margin. The bot needs to mirror it.

### Working code (`ib_async` 2.1.0)

```python
import asyncio
from ib_async import IB

ASYNC_TAGS = (
    "AvailableFunds,BuyingPower,ExcessLiquidity,FullAvailableFunds,"
    "MaintMarginReq,NetLiquidation,$LEDGER:USD"
)

async def live_usd_tradable(ib: IB) -> dict:
    """Live USD-tradable buying power for an EUR-base account.

    Updates every 3 MINUTES (TWS-enforced, cannot be changed).
    Caller must reserve margin client-side for in-flight orders.
    """
    rows = await ib.reqAccountSummaryAsync(group="All", tags=ASYNC_TAGS)

    def get(tag, currency=None, default=None):
        for v in rows:
            if v.tag != tag:
                continue
            if currency is None or v.currency == currency:
                return v.value
        return default

    eur_avail = float(get("AvailableFunds", default="0") or 0)
    usd_per_eur = float(get("ExchangeRate", "USD", default="1.0") or 1.0)
    usd_cash = float(get("CashBalance", "USD", default="0") or 0)
    return {
        "usd_tradable": eur_avail * usd_per_eur + usd_cash,
        "eur_available_funds": eur_avail,
        "usd_per_eur_rate": usd_per_eur,
        "usd_cash_balance": usd_cash,
        "maint_margin_eur": float(get("MaintMarginReq", default="0") or 0),
    }
```

### THE BIG GOTCHA — 3-minute update cadence

Per IBKR's [TWS API docs](https://interactivebrokers.github.io/tws-api/account_summary.html):

> "Every three minutes those values which have changed will be returned. The update frequency of 3 minutes is the same as the TWS Account Window and cannot be changed."

**`AvailableFunds` does NOT update on every fill.** A burst of 0DTE fills can blow through your real buying power, and `AvailableFunds` won't reflect it for up to 3 minutes. IBKR's *server-side* margin engine sees fills instantly and will reject orders that exceed margin — but the client-side number you read is stale up to 3 minutes.

### Mitigations the bot MUST implement

1. **Subtract reserved margin client-side**: after submitting each order, decrement the cached `usd_buying_power` by the order's margin requirement. Don't trust the API to report post-fill state in time.
2. **Use `reqPnL` + `reqPnLSingle` for real-time equity curve** — those are pushed on every fill. Use `accountSummary` for buying power gating; use `reqPnL` for live P&L display.
3. **Listen for `errorEvent` codes 201/202** — fires when an order is rejected for margin. Only definitive signal that the live server-side number was breached.
4. **Pre-trade `whatIfOrder`**: IBKR's `whatIf` flag on an order returns the margin impact without placing it. Use this as the gate, not `AvailableFunds`. It's the broker-authoritative number.

### `FullAvailableFunds` vs `AvailableFunds`

- `AvailableFunds` — current state, the right one for **intraday 0DTE gating**.
- `FullAvailableFunds` — post-SMA-recalc state used at the **start of the next trading day**. Irrelevant for our intraday close-by-EOD strategy.

### Sources

- [TWS API Account Summary docs](https://interactivebrokers.github.io/tws-api/account_summary.html) — authoritative on tags + 3-min cadence
- [ib_async 2.1.0 IB module source](https://ib-api-reloaded.github.io/ib_async/_modules/ib_async/ib.html) — confirms `$LEDGER:USD` syntax and dual sync/async pattern

---

## Implications for the migration plan — what changes

### Architecture (DRAMATIC simplification)

**OLD (pre-research)**:
```
GCE VM
 ├── ib-gateway-live (Docker, IBC, port 4001)
 ├── ib-gateway-paper (Docker, IBC, port 4002)
 └── Python bots → TCP socket → Gateway → IBKR
```

**NEW (post-research)**:
```
GCE VM
 └── Python bots → HTTPS REST/WebSocket → IBKR cloud (direct)
        ├── via ibind / OAuth 1.0a — fully headless
        └── (fallback) Docker IB Gateway + IBC if OAuth 1.0a access ever revoked
```

### Operations (huge wins)

| Item | Before (Gateway path) | After (OAuth 1.0a path) |
|---|---|---|
| Process on VM | Docker (paper + live), IBC | None — pure REST client |
| Weekly Sunday phone tap | Required | **Eliminated** |
| Daily auto-restart | Required (IBC) | Not applicable |
| Disconnect recovery | Watchdog + manual triage on fail | `/tickle` heartbeat + token re-handshake (automated) |
| Memory footprint | ~200-400 MB Gateway + Python | ~50 MB Python only |
| Latency | TCP-socket binary protocol (very fast) | HTTPS REST + WebSocket (still fast for 0DTE) |
| 2FA at runtime | Yes, weekly | None |

### Code changes in the plan

- **`shared/ib_client.py`** uses `ibind` (or a hand-rolled OAuth 1.0a client) instead of `ib_async`. The SDK choice flips.
- **`Voyz/ibind` v0.1.23 (April 2025)** is the canonical retail OAuth 1.0a SDK. **No fork needed** — its `[oauth]` extra already requires `pycryptodome>=3.21` (the maintained fork that exposes the same `Crypto.*` namespace). We assert this at startup via `assert_safe_crypto_backend()`. Earlier wiki warnings about pyCrypto were misleading and corrected 2026-05-14.
- All combo / order / market-data calls now go through `ibind.IbkrClient` REST methods, not `ib_async.IB` socket methods.
- **WebSocket streaming**: `ibind` has WebSocket support but is REST-first. For high-frequency monitoring (every 2-15s tick) over WebSocket, may need to extend it. For our typical cadence this is fine.

### Phase 0 changes

Add tasks:
- [ ] Generate two RSA 2048-bit keypairs (live + paper, distinct per IBKR requirement)
- [ ] Generate two DH parameter files (paper + live)
- [ ] Register first-party OAuth 1.0a via IBKR's [self-service portal](https://ndcdyn.interactivebrokers.com/sso/Login?action=OAUTH&RL=1&ip2loc=US)
- [ ] **Add 2 weeks of slack** to Phase 0 timeline for OAuth activation
- [ ] Confirm "Non-Professional" status set correctly in Client Portal → Settings → Market Data Subscriptions
- [ ] Subscribe to **both** CBOE Streaming Market Indexes (VIX) **and** CME S&P Indexes (SPX) — not just one
- [ ] Subscribe to OPRA Top of Book

Remove tasks:
- ~~Docker + IBC configuration for Gateway~~
- ~~systemd units for paper + live Gateway~~
- ~~Sunday phone-tap calendar reminder~~
- ~~Watchdog reconnect testing~~

### Risk register additions

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| IBKR revokes OAuth 1.0a retail self-service access | Low | High (forces re-architecture mid-flight) | Keep IB Gateway + IBC code path documented in `research_scratch/04_*.md` as fallback. Be prepared for a 2-week emergency rebuild. |
| ~~`pyCrypto` security vulnerability in `ibind`~~ — verified safe 2026-05-14 | n/a | n/a | ibind 0.1.23 already requires `pycryptodome>=3.21`. `assert_safe_crypto_backend()` is the startup tripwire if anything ever installs legacy pyCrypto instead. |
| OAuth 1.0a activation delay > 2 weeks | Medium | Medium (delays Phase 1 start) | Build 2 weeks of slack into the timeline. Start OAuth registration on day 1 of Phase 0. |
| `accountSummary` 3-min cadence missed in pre-trade gate | High at launch | High (over-leverage) | Reserve margin client-side after every order placement; use `whatIfOrder` as the authoritative pre-trade check. |
| IBKR closes OAuth 1.0a self-service to new individual accounts | Low (we already have an account in plan) | Medium | Register OAuth credentials in Phase 0, well before any policy change could affect us. |

### Cost model corrections

Old market-data line: $11.50/mo (single CBOE Streaming Market Indexes assumption). **CORRECTION**: $3-6/mo non-pro for *both* SPX and VIX subscriptions + OPRA. Most commission-waivable above $30/mo activity, so net cost approaches $3-5/mo.

Old commission line: $52/round-trip for 10c IC. **CONFIRMED**: $90-94/round-trip when including the Cboe SPX Index Option Surcharge that wasn't in the prior model. Saxo at $2.50/leg/contract bundled = $200/round-trip for the same 10c IC. **Net savings: ~$109.56 per IC round-trip (~55%), or ~$54.90 per IC on the worthless-expiry path.** Earlier "75% reduction" claim was wrong — that compared IB's bare commission line ($0.65/contract) against Saxo's bundled rate, ignoring the $0.45/contract Cboe Index Option Surcharge IB unbundles.

---

## What remains to email IBKR API support

After this research, only **2 items** genuinely need IBKR confirmation (down from 6):

1. **Maximum number of concurrently-registered public keys per OAuth 2.0 `client_id`** (matters only if we ever move from OAuth 1.0a to 2.0).
2. **Fast-revoke SLA on a compromised public key / access token** — is there a same-business-day disable path or only standard Message Center ticket?

The other 4 questions are now answered with confidence.

---

## Update process

This doc is a snapshot of May 2026 research. Re-validate before any major migration milestone:

- **Quarterly**: re-check ORF rate (it expires Jun 30 2026 with possible new methodology Jul 1).
- **On `ibind` version bump**: re-verify the pyCrypto replacement is still needed (track upstream issue).
- **On any IBKR Campus changelog entry mentioning retail OAuth**: re-evaluate the OAuth 1.0a vs 2.0 decision.

**Last compiled**: 2026-05-13 by 3 parallel research agents + direct primary-source verification (PyPI, GitHub raw, CBOE PDFs, SEC fee advisories).

---

## 2026-05-14 update — 4 more questions answered (CP API specifics)

After committing to the OAuth 1.0a + ibind architecture, four NEW technical questions surfaced. All resolved via parallel research (4 agents). Findings integrated into the rewritten migration plan; key items below for direct reference.

### Q7 — How exactly does a 4-leg SPX iron condor express on CP API?

**Single `POST /iserver/account/{accountId}/orders` with a `conidex` string.** Format:

```
28812380;;;{short_call_conid}/-1,{long_call_conid}/1,{short_put_conid}/-1,{long_put_conid}/1
```

- `28812380` is IBKR's **USD spread template conid** — universal for USD multi-leg combos
- Three semicolons are a literal grammar requirement
- Each leg's ratio sign carries direction: negative = SELL, positive = BUY

Order body for SHORT IC:
- `side: "SELL"`
- `orderType: "LMT"`
- `price: 0.30` — **POSITIVE** when SELLing-to-receive a credit (counter-intuitive but documented IBKR convention; see [TWS Notes on Combination Orders](https://www.ibkrguides.com/traderworkstation/notes-on-combination-orders.htm))
- `tif: "DAY"`
- `quantity: 10` — number of SPREADS, not legs

In ibind:
```python
client.place_order(OrderRequest(
    conid=None, conidex=conidex, sec_type="BAG",
    side="SELL", order_type="LMT", price=0.30,
    quantity=10, tif="DAY", acct_id=account_id,
), answers=DEFAULT_ANSWERS, account_id=account_id)
```

**Critical gap**: CP API has **no direct equivalent of TWS's `smartComboRoutingParams=[("NonGuaranteed", "1")]`**. Atomic-fill enforcement on stop-out closes (where we MUST avoid being left naked short) requires client-side monitoring via `sor` WebSocket + per-leg market-order fallbacks if 1-3 legs fill but the spread doesn't complete.

Source: `research_scratch/09_cpapi_combo_orders.md` (verified against ibind/examples/rest_06_options_chain.py).

### Q8 — Can we stream SPX + VIX + 30 option legs over CP API WebSocket?

**Yes — the "5 concurrent subscriptions" rumor was a myth.** Per ibind issue #100: the 5-subscription cap applies to HISTORICAL data (`hmds`), not real-time market data (`smd`). Real-time `smd` rides the standard 100-line IBKR market-data quota; 30 lines is comfortably inside.

ibind + OAuth 1.0a connects directly to `wss://api.ibkr.com/v1/api/ws?oauth_token=<TOK>` — no local CP Gateway needed.

**Critical gotcha**: `smd` topics **silently auto-terminate after ~15 minutes**. The WebSocket stays healthy; only that conid's ticks stop. ibind 0.1.23 does NOT auto-refresh. We must implement `umd→smd` rotation every ~13 min ourselves. Documented in migration plan §A.5 (`StreamingManager` class).

Field codes for our use case (from `ibind/client/ibkr_definitions.py`):
- 31 = last, 84 = bid, 86 = ask, 88 = bid_size, 85 = ask_size, 7635 = mark
- 7308 = delta, 7309 = gamma, 7310 = theta, 7311 = vega
- **7633 (NOT 7283) = IV per strike**
- 7638 = open interest
- 6509 = availability flag (`R`=real-time, `D`=delayed, `Z`=stale)

Source: `research_scratch/10_cpapi_streaming.md` (verified against ibind/client/ibkr_ws_client.py).

### Q9 — How do we do pre-trade margin check (`whatif` equivalent) on CP API?

**Endpoint**: `POST /iserver/account/{accountId}/orders/whatif`. Payload is identical to `place_order` but with `whatif: true`.

Response returns five blocks:
- `amount` — order size
- `equity` — equity (current / change / after)
- `initial` — initial margin (current / change / after)
- `maintenance` — maintenance margin (current / change / after)
- `position` — position size (current / change / after)

**All values returned in account base currency (EUR for us)**, as strings with embedded currency suffix like `"+4,500.00"`. Requires parsing.

No risk-warning prompts fire on `whatif`, so no `answers` parameter needed.

For our pre-trade BP gate (replaces SaxoClient's ORDER-004 check):
```python
result = await ib_client.what_if_order(ic_request)
required_margin_eur = parse_amount(result["initial"]["change"])
available_eur = await ib_client.get_balance(currency="EUR")
if required_margin_eur > available_eur["tradable"] * 0.95:  # 5% safety buffer
    return SKIP  # margin gate
```

Source: `research_scratch/11_cpapi_margin_account.md`.

### Q10 — Live USD-tradable on an EUR-base account via CP API?

**No single field**. Two-step computation:

```python
summary = await client.portfolio_summary(account_id)
ledger = await client.get_ledger(account_id)

eur_available = float(summary["availablefunds"]["amount"])  # in EUR
usd_row = ledger["USD"]
eur_per_usd = float(usd_row["exchangerate"])    # **direction needs first-call verification**
usd_cash = float(usd_row["cashbalance"])

usd_tradable = eur_available / eur_per_usd + usd_cash
```

**Caveat to verify on first paper trade**: the `exchangerate` direction (base-per-quote vs quote-per-base) is not unambiguously documented. Verify empirically on first live call.

**Update cadence — this is the BIG correction from prior TWS-based assumption**:

| Source | Cadence | Notes |
|---|---|---|
| TWS `reqAccountSummary` | 3 minutes (hard throttle) | Client-side cadence enforced by the API |
| **CP API `portfolio_summary` / `get_ledger`** | **No documented throttle** | Can poll at 1Hz if needed |
| CP API WebSocket `ssd` (summary), `sld` (ledger), `spl` (P&L) | **Sub-second push** on changes | Event-driven, much faster than polling |

The TWS 3-minute throttle DOES NOT carry over to CP API. The underlying risk engine still updates at ~3s, so polling faster than that is pointless — but the API itself doesn't throttle.

Recommended pattern: WebSocket for hot updates + HTTP poll every 10-30s as fail-safe re-sync.

Source: `research_scratch/11_cpapi_margin_account.md`.

---

## 2026-05-14 update — activation poller correctness audit

Per `research_scratch/12_ibind_errors_lifecycle.md`, the original poller had a partial-success blind spot. A successful `/oauth/live_session_token` response is **necessary but not sufficient** for "fully activated for trading".

**Corrected 3-step check** (now wired into `~/ibkr-oauth/poll/poll.py`):

1. **LST issued** — `POST /oauth/live_session_token` returns 200 with `diffie_hellman_response`, `live_session_token_signature`, `live_session_token_expiration`
2. **Brokerage session opens** — `POST /iserver/auth/ssodh/init` returns `authenticated: true, connected: true`
3. **Auth status confirms** — `GET /iserver/auth/status` returns `authenticated && connected && !competing`

Today's poll output (`id: 19030, error: invalid consumer`) failed at step 1, which is what we expected pre-activation. After Sunday's server reset, we expect step 1 to succeed; we'll then see steps 2 + 3 outcome separately.

The activation poller will be updated as part of Phase A.0 (before any other code work).

---

**Last updated**: 2026-05-14. Added Q7-Q10 (CP API specifics) + activation poller audit.
