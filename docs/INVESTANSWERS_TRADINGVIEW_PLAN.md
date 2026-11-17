# Plan — InvestAnswers TradingView indicators → IBKR stock orders

**Status 2026-11-16:** the RECEIVER IS BUILT (`services/signal_ingest/`, 49 tests,
**not deployed** — see `docs/SIGNAL_INGEST_INTEGRATION.md`), and **§0-bis below is
WRONG and must not be quoted.** Written 2026-10-20 at the operator's request:
Ruben (dad) asked whether InvestAnswers' TradingView buy/sell indicators for
ordinary stocks (Tesla, Apple and similar) could drive real orders on Interactive
Brokers.

Sibling investigation: `memory/dad_signal_service_investigation.md` (the Patreon
"My Trading Journey" emails). **Read that first** — it ended on a single cheap
fact, and this plan is deliberately shaped the same way.

---

## 0-ter. 🔴 CORRECTION 2026-11-16 — §0-bis refuted a premise that is real

Re-researched at the operator's request. §0-bis below concluded "the premise does
not match the product … not possible, no such product". **That is false**, and the
two errors are worth more than the conclusion was:

1. **IADSS's own product page states its coverage is "All including Equities,
   Crypto, Commodities, FX and pairs."** §0-bis read the *catalogue blurbs* —
   where most products are crypto-flavoured — and never opened the product page
   of the one item it had itself named "the only real candidate". It then filed a
   verdict contradicting its own caveat.
2. **IADSS demonstrably emits webhook-capable alerts.** Two independent
   third-party receivers exist (`ballzac81/IADSS-Signal-Tracker`,
   `IADSS-Rotation-Tracker`). The first documents four alerts — **BUY/SELL Early
   Warning** (notification only) and **BUY/SELL Sequence Complete** (which it
   uses to execute) — states the indicator "handles signal sequencing
   internally", and creates them **"Once per bar close"**, the repainting-safe
   setting this plan asks for. Third-party, not first-party: InvestAnswers' own
   pages still say nothing about alerts or webhooks.

**What did NOT change: the drag, and the blockers.** Current sale prices are
IADSS **$149/mo** (list $175) and the Pro Bundle **$229/mo** (list $250), plus
TradingView itself — so ~$1,950–2,600/yr all-in, **~10–13% at $20k** and
~2–2.6% at $100k. ⚠️ "Pro+" no longer exists (Essential/Plus/Premium/Ultimate),
and on **Essential and Plus alerts EXPIRE AFTER TWO MONTHS** — an unattended
trigger that silently lapses is this project's recurring failure shape, so
budget Premium or build an expiry watchdog.

**Gate 2 is now a strong pass; gates 3 and 4 are the whole decision.** No source
anywhere gives a signal RATE, which is still the one free number that can end
this (≥ ~50/yr or nothing resolves in useful time), and no real-money IBKR
account exists for it — dad's user is read-only by design, ours is paper.

**Concrete gap, newly visible:** our contract carries one `action: buy|sell`
slot; IADSS emits four alerts, so the Early-Warning pair has nowhere to go.
Record them as a non-trading outcome or wire only the Sequence-Complete pair —
an unmade decision, not a defect.

---

## 0-bis. ⚠️ SUPERSEDED (2026-10-21) — see §0-ter above before reading this

The plan below was written from reasoning alone. Four facts gathered since, each
free, change the picture before any of its checks are reached.

### A. There are NO indicators for individual stocks

The ask was Tesla/Apple buy-sell. The catalogue is **crypto and macro**. The full
product list (investanswers.io/products):

| Product | Price/mo (annual) | Covers |
|---|---|---|
| IA Pro Bundle | **$250 ($229)** | everything, incl. IADSS |
| IADSS | **$175 ($149)** | "trading signals for active traders" |
| IA Rotation Model | $150 ($124) | capital reallocation among equities |
| IA Profit Taking Model | $94 ($74) | selling at target levels |
| IA Pair Trading (PTOS) | $99 ($69) | hedging with correlated assets |
| IA ATR Model | $89 ($59) | trading ranges, any asset |
| IA Layer In/Layer Out | $50 ($24) | entry + profit-taking guidance |
| IA Bitcoin Top & Bottom | $50 ($24) | Bitcoin on-chain |
| IA Arb Cloud | $50 ($24) | BTC/MSTR-style co-integration |
| IA DCA on Steroids | $40 ($30) | dollar-cost averaging |
| IA Macro Model | $40 ($19) | global macro |
| IA Solana Upside Model | **free** | Solana projections |

Nothing is per-ticker for equities. The closest, **IA Rotation Model**, is an
*allocation* model — which stocks to hold — not a buy/sell trigger on AAPL.

### B. The one script we can inspect is commentary, not signal

`IA-SOL-Upside-Model` is free and public, so it serves as a free proxy for
Check 2. It is an **`indicator()`, not a `strategy()`**, published
**closed-source**, with **no alerts, no `alertcondition`, no entries/exits**. Its
own description says it helps *"visualize the relative prices based on pricing
models and also gauge upside for profit taking"* — a projection overlay a human
reads.

⚠️ One script is not the whole line, and **IADSS is explicitly sold as "trading
signals"** — so this does not condemn the catalogue. But every product name in
the table is a *model* or *guidance* ("Upside", "Top & Bottom", "Layer In/Out",
"Profit Taking"), which is the vocabulary of judgement aids, not triggers.
**IADSS is the only candidate worth evaluating, and it is the one that cannot be
inspected without paying.**

### C. The subscription imposes a MINIMUM ACCOUNT SIZE — the new decisive bar

This is arithmetic, and it can be done before anything else:

| account | IADSS $1,788/yr | Pro Bundle $2,748/yr |
|---|---|---|
| $20,000 | **8.9% drag** | **13.7% drag** |
| $50,000 | 3.6% | 5.5% |
| $100,000 | 1.8% | 2.7% |
| $200,000 | 0.9% | 1.4% |

**The strategy must clear this hurdle before earning a cent.** At the ~$21k
scale seen in the sibling Patreon investigation, IADSS is a **~9%/yr headwind**.
For the data feed to be a <1% drag the account needs roughly **$180k (IADSS)** or
**$275k (bundle)**.

Combined with the measurability floor (~50 trades/yr), the bar is now **two**
independent conditions, not one — and both are checkable for free.

### D. The licence binds to the subscriber, which sharpens Check 3

The EULA does **not** forbid automated trading on the outputs ("You are solely
responsible for your own … trading decisions") — good news. But it does forbid
"copy, rent, sell, lease, distribute, pledge, assign, or otherwise transfer …
make it available to or share it … with anyone else".

So the scripts live on **dad's** TradingView account and the webhook must
originate there. The chain is then: dad's TradingView → our ingest service →
*which broker account?* His IBKR user is read-only by design, ours is paper.
**There is still no legal-and-technical endpoint that exists today.**

ℹ️ Separately, and stated as what it is: there are **unverified, user-submitted
complaints** about the business on a consumer-complaint site. Such reports are
not evidence of anything and are not a finding — but a ~$2,750/yr recurring
purchase deserves independent diligence.

### Revised recommendation

**The original question — "automate InvestAnswers' TSLA/AAPL buy-sell signals" —
has no matching product, so it is answered: not possible as asked.**

If the interest survives re-aiming at what does exist, the order is unchanged
and still free: ask what **IADSS** actually emits (discrete alerts, or a chart to
read?), then how many signals per ticker per year, then whether the account size
clears the subscription drag. Only if all three pass does any of the build below
become relevant.

---

## 0. The three checks that come before any code

Each is free or near-free, and **any one of them can end this**. Do them in
order; do not write code until all three pass.

### Check 1 — can a signal physically leave TradingView?

InvestAnswers' indicators will be **invite-only Pine scripts**. Two separate
things must both be true:

- the script must expose an **alert condition** (an `alertcondition()` or a
  `strategy()` entry/exit). A script that only *draws* something cannot fire an
  alert, and there is then nothing to automate.
- the TradingView account must be on a tier that supports **webhook URLs** on
  alerts (Pro+ and above at time of writing). Without webhooks the only outputs
  are email and app popups — which puts us back in the same email-parsing
  position as the Patreon service, and worse, because there is no structured
  body.

⚠️ Invite-only scripts are **source-hidden**. We can consume their signals; we
can never read, verify or reimplement the logic. Whatever it does stays a black
box, which is a permanent limit on how much trust is reasonable.

### Check 2 — is it a signal or is it commentary?

The exact trap the Patreon emails turned out to be: those were **diary entries**
("this reflects a trade that has already been executed"), not instructions. If
the indicator is a picture a human interprets — a band, a heat colour, a
"caution zone" — there is no defined event to trade and automation is not a
smaller version of the job, it is a different job.

The test: can you state, with no judgement, **exactly what fires, on what bar,
and what the resulting order is**? If that sentence needs a human, stop.

### Check 3 — whose IBKR account would place the order?

This is the real blocker and it is not a configuration question.

| account | can it trade? |
|---|---|
| Dad's `options-scanner-ruben` | **No — read-only BY DESIGN.** It exists so his scanner can never interfere with the bot's session. |
| CALYPSO's paper account | Yes, but it is **paper**, and it is the account the whole HYDRA fleet trades through. |
| A real-money account | Does not exist for this. Would need its own IBKR username. |

⚠️ **One brokerage session per IBKR username.** `calypso-broker` owns the paper
session for nine strategy processes; a second session on the same username
evicts it and crash-loops the fleet. So a stock trader is either (a) another
consumer of `calypso-broker`, inheriting its rate gate, or (b) its own username
and its own broker process — which is exactly the shape the live-money design
already uses (`docs/migration/LIVE_MONEY_ARCHITECTURE.md`: a second broker on
:8789, sibling credential directory, never nested).

---

## 1. Evaluate it before building anything — TradingView already has the tool

If the script is a `strategy()` rather than an `indicator()`, **TradingView's own
Strategy Tester gives the historical record for free**. No webhook, no server, no
IBKR. That is the cheapest possible evaluation and it comes first.

Treat its output sceptically, for reasons that apply to any vendor backtest:

- **repainting** — an indicator that uses the current (unclosed) bar will look
  clairvoyant in history and be useless live. Check whether signals fire on bar
  close only.
- **lookahead / `request.security` leakage** on higher timeframes.
- default commission and slippage are usually **zero**. Set them.
- the tester reports the strategy *as coded*, which may not be what the
  newsletter actually recommends.

### The frequency question — MEASURED 2026-11-17, no longer unknown

> ⚠️ **This section used to say the rate was unknowable without asking the vendor
> ("ask pre-sales how many signals it fires"). That was wrong — it is derivable
> from the published mechanism, and the answer changes the gate from a blocker
> into a configuration choice.**

Nobody publishes an IADSS signal rate: not the product page, not the two
third-party reviews, not either of the two third-party webhook receivers. But the
reviews describe the **mechanism** precisely enough to measure its base rate:

| component | described as |
|---|---|
| Confluence Model | arrows on a Bollinger-band **break followed by a re-integration** |
| Mean Reversion | dots at overbought / oversold extremes |
| BUY/SELL **Early Warning** | "MR + Confluence aligned" |
| BUY/SELL **Sequence Complete** | "all conditions met" (adds the Trend flip) |

Those are **nested**, so `count(re-integration) >= count(aligned) >= count(Sequence
Complete)`: each extra condition can only remove events. So a proxy of the two
loosest components gives a hard **upper bound** on the rate of the alert that
actually executes.

`scripts/measure_signal_base_rate.py` — 8 liquid tickers, 80 ticker-years of
daily bars, BB(20, 2) + RSI(14):

| | per ticker-year |
|---|---|
| band re-integration events | **16.0** |
| …with Mean Reversion aligned | **6.2** |

**On daily bars one ticker fires ~6 times a year.** Our own measurability finding
([`memory/measurability_scoring_inverts_the_backlog.md`](../memory)) puts the
resolution floor near **50 trades/yr**, so a daily-bar setup needs **~8 tickers**
to become measurable at all — and that is the *upper bound*, with the real
Sequence Complete rate lower still.

**But the rate is a timeframe choice, not a property of the product.** IADSS runs
"all timeframes (eg down to 1 second and up to 1 day or week)", and the rate
scales roughly with bar count (`scripts/measure_signal_base_rate_by_timeframe.py`,
SPY / NVDA, aligned events per ticker-year):

| timeframe | 1wk | 1d | 1h | 30m | 15m | 5m |
|---|---|---|---|---|---|---|
| SPY | 1 | 6 | **41** | 100 | 196 | 452 |
| NVDA | 2 | 6 | **47** | 91 | 174 | 448 |

So **hourly bars on one or two tickers is the cheapest configuration that clears
the 50/yr floor** — and that is the first concrete design answer this project has
had. Note what it costs: hourly-or-faster is exactly where repainting, alert
expiry and execution cost bite hardest, and where a 3-second webhook timeout
stops being generous.

Two consequences that follow from the rate, not from taste:

1. **Alert slots become binding on the daily path.** 8 tickers x 4 IADSS alerts =
   **32 alerts**, over Essential's 20. The hourly path needs 4–8 and fits any
   paid tier.
2. **Essential and Plus expire alerts after ~2 months.** An unattended trigger
   that silently lapses is precisely how variant H sat dead for a month and E for
   eighteen days. That argues for **Premium** (open-ended alerts) rather than the
   cheap tier — which raises the drag to ~$149 + ~$70 = **$219/mo ≈ $2,630/yr**,
   i.e. **~13% at $20k** and ~2.6% at $100k.

⚠️ **What this is and is not.** It is a measurement of the *published mechanism*
on real data, and it bounds the answer by construction. It is **not** the real
script: the actual thresholds, the Optimized Trend component and the Trend flip
are not public, and the proxy's own trend filter is reported as 0/yr because
"mean-revert against the trend" and "trend agrees" are near-disjoint as defined —
a definitional artefact, not a finding. Use the `aligned` column as the bound and
ignore the trend column.

---

## 2. If and only if all three checks pass: the build

### Shape

```
TradingView alert ──HTTPS POST──► signal-ingest service ──► IBKR order
   (webhook)                       (own process, own port)      (via a broker)
```

### Where it must NOT live

**Not in the dashboard.** `dashboard/` is documented and tested as *"100%
read-only — zero changes to the bot"* and *"Does NOT import IBClient or any
trading code"*. It already has nginx + a real certificate + accounts, so it is
tempting. Putting an order-placing route in it would delete the one invariant
that makes it safe to restart at any time. A separate service, separate port,
separate systemd unit.

### Non-negotiables for a public order-placing endpoint

1. **Shared secret in the alert body.** TradingView cannot be relied on for
   custom auth headers, so the secret travels in the JSON payload and the
   service rejects anything without it. Secret in Secret Manager, never in the
   Pine script's visible alert text if that text is shareable.
2. **IP allowlist.** TradingView publishes its webhook egress IPs; nginx should
   refuse everything else. Defence in depth — the secret alone is one leak away
   from an open order endpoint.
3. **Idempotency.** TradingView can resend. Every order carries a client order
   id derived from (ticker, side, signal timestamp), exactly as the existing
   entry path uses `coid` for server-side dedup. A replayed alert must be a
   no-op, not a second position.
4. **Hard caps, enforced server-side, not in Pine.** Max position per ticker,
   max total exposure, max orders per day, allowlist of tradable symbols. The
   indicator is a black box; the limits are ours.
5. **Kill switch.** `systemctl stop` must halt it, and there must be a config
   flag that makes it log-only without stopping it — the dry-run equivalent we
   use everywhere else.
6. **Market-hours gate.** An alert that fires at 03:00 must not place an order
   into a closed or illiquid book. `shared/market_hours.py` already answers this.
7. **Log-only first.** Run it for weeks writing what it *would* have done, and
   compare against the indicator's own claims before a single real order. This
   is the same dry-run discipline every HYDRA variant goes through, and the
   reason D and E have never risked a cent.

### What already exists and can be reused

- **Order placement is instrument-agnostic.** `IBClient.place_order(conid, side,
  quantity, ...)` is keyed on a contract id, not an options structure, and
  `qualify_contract(symbol, sec_type="STK")` resolves a stock (SPY STK is
  already in the conid reference table). So stocks need no new execution code.
- `place_and_wait_for_fill` — polling to a terminal state, with cOID dedup.
- The retry + per-family circuit breakers, and the rate gate.
- `shared/alert_service.py` for Telegram/email notification.
- `shared/market_hours.py` for the session gate.

### What must be written

The ingest service itself: endpoint, auth, payload validation, dedupe store,
risk caps, symbol allowlist, dry-run mode, a ledger of signals-vs-fills, and its
systemd unit. Deliberately small, and deliberately outside `bots/hydra/`.

---

## 3. Do NOT build

- **Anything on real money before a logged-only period** has reproduced the
  indicator's claims on our own fills.
- **Anything routed through dad's IBKR user.** It cannot place orders by design,
  and that design is load-bearing.
- **A second session on the CALYPSO paper username.** It evicts `calypso-broker`
  and crash-loops nine strategies.
- **An LLM that interprets indicator text.** Same verdict as the Patreon plan:
  if the signal needs a language model to decide what it means, it is not a
  signal yet.
- **A scraper of InvestAnswers content.** If a signal cannot arrive through a
  webhook the subscriber is entitled to use, the answer is no.

---

## 4. Order of work

1. Check 1 (alert + webhook tier) — free.
2. Check 2 (defined event, stated in one sentence) — free.
3. ~~Frequency count per ticker per year~~ — **DONE 2026-11-17 without a
   subscription**, by measuring the published mechanism: ~6/ticker-yr daily,
   ~44 hourly. See "The frequency question" above. It did not end the
   investigation; it sized it.
4. TradingView Strategy Tester with realistic commission/slippage, and a
   repainting check — free.
5. **Decide: manual allocation, automation, or neither.** Pre-register the two
   bars BEFORE seeing the numbers, as the Patreon plan does — "worth a manual
   allocation" and "worth automating" are different thresholds and the second is
   much higher.
6. Only then: the account question (Check 3) becomes a real decision, because it
   requires a new IBKR username and funding.
7. Only then: build the ingest service, dry-run for weeks, review, and go live
   on a size that cannot matter.

> ⚠️ **This used to read "the single cheapest fact that could end this
> investigation: how many signals it fires per year. **Ask that** first."** The
> instinct to compare it to the Patreon question was right; the instruction was
> not. That fact did not need a vendor, a subscription or an email — it was
> derivable from the mechanism the public reviews already describe, and it took
> one script against free daily bars (2026-11-17).
>
> **The lesson is the general one: before recording something as a question for
> someone else, check whether it is a measurement.** The Patreon question really
> was unanswerable from a desk, because a track record that is not published
> cannot be derived. A *signal rate* is not like that — a rule has a base rate,
> and a base rate can be counted.

**What remains genuinely un-derivable** (and so is a real question for the vendor
or for a one-month subscription): the *edge*, which no mechanism description
bounds, and whether the equity coverage runs the same scripts as the crypto
flagship. The account question (Check 3) stays blocked on the operator, not on
research.
