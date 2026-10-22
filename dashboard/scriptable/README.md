# HYDRA iOS widget (Scriptable)

Home-screen tiles for the HYDRA fleet — read-only, scoped to two API endpoints.

| size | shows | endpoint |
|---|---|---|
| **Small** | the live seat alone: today's P&L, entry dots, lifetime | `/api/widget` |
| **Medium** | each comparability group's leader | `/api/widget/fleet` |
| **Large** | all 9 strategies, grouped | `/api/widget/fleet` |

Large is the biggest size iOS offers on iPhone (`extraLarge` is iPad-only and
falls through to the same fleet layout).

## Why the fleet view is grouped and has no global #1

Ranking all nine on one axis would be wrong four ways, and the live data shows
each one:

1. **Cross-group ranking is the comparison this system exists to prevent.** A
   credit iron condor, a net-debit calendar and a naked strangle share no P&L
   axis — hence `comparable` on `GroupMeta` and the group-scoping of every
   `/compare`.
2. **Raw dollars rank by SIZE, not edge.** A is 1 contract at 75pt; bl/b/c are
   7 at 5pt; H trades SPY at ~1/70th of bl's notional. Over the same 55 days A
   returned −$23.55 against B-A's −$432.10 — size scaled the loss, not the edge.
3. **Lifetimes cover different eras.** Measured 2026-10-22: bl has **9 days** of
   history against c's **94**. Every row therefore carries its day count.
4. **Eight of the nine are simulated.** Only the live seat places real orders,
   so it gets its own highlighted line rather than a rank among simulations.

A global leaderboard would currently crown **H at +$139.80** — the only green
number — despite H being ~1/70th the size and having *zero* observations under
the rules it now runs (its range-expansion filter landed after both its entries).

## Why this needs its own credential

Everything else on the dashboard is behind a real account (username + bcrypt
password + TOTP 2FA, issued a revocable session cookie). Scriptable cannot
perform that login. When the **2026-08-03** migration retired the old shared
`DASHBOARD_API_KEY`, nothing replaced the widget's credential — so from that
date until **2026-10-22** every poll received `401 Not authenticated` and the
widget rendered it as "HYDRA Offline". It had almost certainly never been set
up on a phone, since the committed script still carried its placeholder
`http://YOUR_VM_IP:8080` and an empty key.

## Setup

1. **Issue a token** (shown once — only its hash is stored):

   ```bash
   gcloud compute ssh calypso-bot --zone=us-east1-b --project=calypso-trading-bot \
     --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m \
       scripts.manage_dashboard_users widget-token issue <username> --label iphone'"
   ```

2. **Install Scriptable** from the App Store.
3. **New script**, paste `HYDRA_Widget.js`, name it `HYDRA`.
4. Set `WIDGET_TOKEN` to the token from step 1. `DASHBOARD_URL` already points
   at `https://domytrade.com`.
5. **Run it once inside Scriptable.** It prints `source`, `problem`, the ET
   date and the full payload — so a bad token, a stale payload or a network
   failure each name themselves. The widget shows the same states with less
   room.
6. Long-press the home screen → **+** → Scriptable → **Small** → tap the
   widget → **Script: HYDRA**. Tapping it opens the dashboard.

Manage tokens:

```bash
... scripts.manage_dashboard_users widget-token list              # hash prefixes
... scripts.manage_dashboard_users widget-token revoke <prefix>   # instant
```

Revoking the token **or** disabling the owning account stops it on the next
request. Tokens do not expire, deliberately — a home-screen widget must keep
working for months, which is why its lifetime rules live apart from a browser
session's hours-long idle timeout.

## Security properties

| property | how it is enforced |
|---|---|
| Scoped to `/api/widget` | its own `widget_tokens` table + a dependency attached to that router alone. Pinned across 7 other endpoints by `tests/test_widget_token_2026_10_22.py`. |
| Read-only | the widget router has no non-GET route, asserted — so adding one is a test failure, not a silent privilege escalation. |
| Revocable instantly | DB-backed, like sessions. Both revoke-token and disable-user are tested. |
| Not written to logs | sent as an `X-Widget-Token` **header**. nginx's combined format records the full request line, so a `?api_key=` query parameter would persist the credential to disk in plaintext on a public host on every poll. |
| Not recoverable from the DB | only the hash is stored (asserted by scanning the raw DB file for the plaintext). |

## Tests

Scriptable does not exist off-device, so the widget runs against stubbed
Scriptable globals:

```bash
node dashboard/scriptable/test/widget.test.mjs    # 25 assertions
python -m pytest tests/test_ios_widget_render_2026_10_22.py   # CI bridge
```

Seven mutation controls accompany them, one per defect found when the script
was first actually exercised on 2026-10-22:

| defect | symptom |
|---|---|
| `pnl >= 0 ? "+" : ""` over `Math.abs(pnl)` | **a $500 loss rendered as `$500`** — only the colour distinguished it from a gain |
| `data.date` never read | a cached figure from Tuesday displayed on Thursday as current |
| 401 shared one `catch` with network errors | a revoked token read as "Offline" — this is why it died unnoticed for 10 weeks |
| `skipped` missing from the colour map | fell through to the pending grey, so six skips at 15:00 looked like a day that had not started |
| `cumulative_pnl` ignored | the backend rebases it expressly so the widget matches the web card (they disagreed by ~$1,966 until 2026-07-22) |
| `contracts_per_entry` ignored | the backend exposes it "so iOS widget can show a [Nc] badge"; the badge was never built |

Freshness is judged in **exchange time**, not the phone's: the operator is in
Lisbon (5h ahead of ET), so at 01:00 local it is still the previous ET trading
day — comparing against the phone's date would mark a perfectly fresh payload
stale every single evening. That case has its own test and its own control.
