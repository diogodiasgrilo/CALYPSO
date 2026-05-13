# 06 — IBKR OAuth 2.0 retail + unattended 2FA — research findings (May 2026)

**Audience:** Saxo→IB migration plan author for CALYPSO single-trader retail bot.
**Brief:** Two specific blockers — (Q1) gateway-free OAuth 2.0 for individual live trading, and (Q4) weekly-cold-login 2FA for unattended algorithmic operation.
**Bottom line:** Q1 is still **NO** for OAuth 2.0 specifically — but the question is the wrong question. The real "no-gateway, no-weekly-tap" path that does exist and is used in production by retail traders today is **OAuth 1.0a "Extended" self-service**. Q4 is "Sunday tap is gone if (and only if) you migrate to the Web API OAuth path; the TWS/IB Gateway path is still tap-on-Sunday-or-die."

---

## Question 1 — Gateway-free OAuth 2.0 for an individual IBKR Pro account, May 2026

### Direct answer

**No.** As of May 2026, **OAuth 2.0** (`private_key_jwt`, RFC 7521/7523) on the unified IBKR Web API is gated to **institutional / advisor / approved-third-party-vendor** accounts. An individual retail IBKR Pro user **cannot** self-register an OAuth 2.0 client and reach the trading (`/iserver/*`) endpoints from a cloud host without a local Client Portal Gateway.

IBKR's own positioning (from the unified Web API onboarding docs and confirmed by community summaries of conversations with IBKR support):

> "Retail clients are currently only approved to access the Web API using the Client Portal Gateway. While OAuth 1.0a is expected to firmly stay in the Institutional space, OAuth 2.0 is being considered for individual access in the future. However, there is no ETA at this time."
> — IBKR Campus, *Trading Web API* documentation, as summarized by community search (page is 403-blocked to direct fetch; quote stable across multiple secondary citations in May 2026).
> https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/

IBKR is consolidating *all* web-based products (Client Portal Web API, Digital Account Management, Flex Web Service) under one OAuth 2.0 umbrella — that consolidation is underway but the *retail trading slice* has not flipped. The Nov 18, 2025 changelog item is the deprecation of `/hmds/history` in favour of `/iserver/marketdata/history`; nothing in 2025 or in the first half of 2026 announces retail OAuth 2.0 trading. [unconfirmed for May 2026 — no public roadmap date]
- https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-changelog/

### But — there *is* a gateway-free, retail-usable path. It's OAuth **1.0a**, not 2.0.

This is the critical nuance the brief asks you to flush out. IBKR's *public-facing* line is "OAuth 1.0a is institutional" — but the **operational reality** in the community as of May 2026 is that the OAuth 1.0a self-service portal accepts individual IBKR Pro account registrations, and several thousand retail algo traders are running this in production. The unofficial-but-most-active retail SDK, `Voyz/ibind`, documents it explicitly:

> "Despite the website and some IBKR support agents claiming otherwise, indeed it seems to be possible to use OAuth 1.0a on individual accounts. Many individual account users have successfully registered both live and paper credentials."
> — `Voyz/ibind` wiki, *OAuth 1.0a* page, last edited Apr 11, 2026.
> https://github.com/Voyz/ibind/wiki/OAuth-1.0a

> "IBind supports fully headless authentication using OAuth 1.0a. This means no longer needing to run any type of software to communicate with IBKR API."
> — `Voyz/ibind` README, master branch, May 2026.
> https://github.com/Voyz/ibind

The library shipped first-party gateway-free OAuth 1.0a support in **v0.1.23, April 21, 2025**; a Feb 2026 community report on the same page says users have been able to "retire their IB Gateway container" after the migration.
- https://github.com/Voyz/ibind/releases

### First-party vs third-party — what we'd actually use

- **First-party OAuth 1.0a** — you accessing your own account. **This is the relevant flow.** Self-service registration at IBKR's OAuth portal: generate keypair, pick a 9-character A–Z consumer key, upload public signing + encryption keys + DH parameters, enable OAuth toggle in account settings, retrieve access token + access-token-secret. Tokens are long-lived (do not need to be regenerated weekly). Source for the step list: ibind wiki and IBKR Campus "OAuth 1.0A Extended" page (`/campus/ibkr-api-page/oauth-1-0a-extended/`, 403-blocked; mirrored content).
- **Third-party OAuth 1.0a** — a vendor (TradersPost, etc.) acting on behalf of a customer. Requires `webapionboarding@interactivebrokers.com`, Compliance review, Legal agreement; ~2–3 weeks onboarding plus public-key + callback-URL config. Not relevant for a single-trader bot. Source: IBKR Campus *Third Party Connections*, as summarised in search results May 2026.

The known "register yourself as your own vendor" hack referenced in the brief is essentially what first-party OAuth 1.0a *is* — you don't need a vendor relationship, you just use the self-service portal. The hack is real, it's documented, it works for retail.

### What "gateway-free" actually buys you

With first-party OAuth 1.0a from a cloud host (no local CP Gateway, no IBeam, no IB Gateway):
- All **read** endpoints in the Portal session (`/portfolio/*`, `/iserver/marketdata/*`, account snapshots, historical).
- All **trade** endpoints in `/iserver/*` — you call `POST /iserver/auth/ssodh/init` after the OAuth handshake to *open* the brokerage session, then place orders normally. This is documented; it's the same two-tier session model the CP Gateway uses, just driven over the OAuth-authenticated REST surface instead of the local Java daemon.
  - https://www.interactivebrokers.com/campus/traders-insight/authenticating-with-the-ibkr-client-portal-rest-api/
- A `/tickle` heartbeat is required every ~60 s (recommended) and at minimum every 5 min to keep the brokerage session warm; ibind provides a `Tickler` thread that does this. https://github.com/Voyz/ibind/wiki/API-Reference-%E2%80%90-IbkrClient
- The **live session token** issued by OAuth 1.0a is valid ~24 h and is rotated **automatically by the client library** via the OAuth signing flow — *no human interaction is required for that rotation*.

### Roadmap / timeline

- IBKR explicitly says OAuth 2.0 for retail "is being considered… no ETA." No press release, no Campus blog post, no Changelog entry has changed that as of May 2026.
- ibind issue #102 (May 7, 2025) — feature request "Support for IBKR OAuth 2.0" — **still open** in May 2026, no maintainer commitment, no merged PR. Because OAuth 2.0 is not retail-available, there is nothing for the SDK to wrap.
  - https://github.com/Voyz/ibind/issues/102

**Recommendation for the migration plan:** target **first-party OAuth 1.0a** as the production auth surface. It is gateway-free, retail-usable, well-documented in the community SDK, and the operational pattern (live-session-token rotation + `/tickle` heartbeat) is solved code in ibind. Treat OAuth 2.0 as "watch the changelog, but don't plan around it."

---

## Question 4 — Unattended-week-plus session for retail live algo trading, May 2026

### Direct answer — which path you take determines the answer

There are **two distinct authentication surfaces** and the "weekly Sunday tap" only applies to one:

| Path | Weekly 2FA tap required? | Truly unattended week+? |
|---|---|---|
| **TWS / IB Gateway** (including IBC, ibeam, Dockerized gateway, QuantConnect-hosted) | **YES — every Sunday after 01:00 ET** | No — needs IB Key push approval on the phone |
| **Web API via first-party OAuth 1.0a** (ibind-style, no gateway) | **NO** — long-lived access tokens, no weekly browser-tap | Yes — token + `/tickle` heartbeat is enough |
| Web API via OAuth 1.0a *through* `clientportal.gw` (hybrid) | Effectively NO weekly tap — OAuth pre-auth bypasses the browser login on the gateway, but the gateway itself is still a moving part | Yes, with caveats |

### Canonical IBKR quote on the Sunday reset (TWS/Gateway path)

> "If you have elected to have your trading platform restart automatically on a daily basis, this procedure will require manual authentication once a week, the first time you log into the platform after the security tokens have been invalidated. **This security process occurs each Sunday at 1:00 am ET.**"
> — IBKR Guides, *TWS Auto Restart Considerations*, accessed May 2026.
> https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm

And from the supporting FAQ:

> "For security reasons, IBKR does not allow the ability to disable the Secure Login System for Client Portal access or for the trading platforms."
> — IBKR Guides, *Two-Factor Authentication FAQ*, May 2026.
> https://ibkrguides.com/securelogin/sls/faq.htm

So enabling **only** "IB Key Security via IBKR Mobile" does **not** drop the weekly tap — it just controls *how* you tap (push vs challenge-response). Both the QuantConnect docs and the QuantConnect community forum confirm the Sunday phone tap is still required in 2026:

> "Every Sunday or early Monday, IBKR's IB Gateway forces a manual 2FA login to refresh the soft token. If you're asleep or away when this happens, your bot will miss every alert from early Monday morning until you manually intervene."
> — *TradingView to Interactive Brokers Automation: The 2026 Guide*, PickMyTrade blog, 2026.
> https://blog.pickmytrade.io/tradingview-interactive-brokers-automation-2026/

> "If you have IBKR 2FA enabled, you will receive a notification on your IB Key device every Sunday to re-authenticate the connection between IB and your live algorithm."
> — QuantConnect Lean docs, *Interactive Brokers*, May 2026.
> https://www.quantconnect.com/docs/v2/lean-cli/live-trading/brokerages/interactive-brokers

### 2024–2026 announcements that change this — none material

- No IBKR Campus article, blog post, or Changelog entry between Jan 2024 and May 2026 announces a new automated-2FA path, certificate-based auth replacing IB Key, OAuth-replaces-2FA, or any "headless 2FA" for the TWS/Gateway path. [exhaustively searched]
- The IBC GitHub project (`IbcAlpha/IBC`) carries the long-running issue #161 ("Is there a way to automate the 2FA?") — answer remains **no** in 2026.
- ibeam (`Voyz/ibeam`) issue #14 ("2FA with IBKey") has been open since March 17, 2021. The proposed approach (Android emulator + Appium driving the IBKey app) is documented but not merged; **no implementation has landed**. https://github.com/Voyz/ibeam/issues/14
- ibeam PR #277 (2025) added support for *selecting between* 2FA methods (IB Key vs Mobile Authenticator App) at the login screen, but does not bypass the tap itself. https://github.com/Voyz/ibeam/pull/277
- The "DSC+" hardware security card is **no longer offered** to new accounts in 2026 (replaced by IBKR Mobile / IB Key); legacy holders still use it but it cannot be programmatically replicated.

### Why OAuth 1.0a actually does what the Sunday-tap doesn't

When you authenticate via OAuth 1.0a, the **access token + access-token-secret** you stored at registration time *are* the credential. The library uses them to sign a request that asks IBKR for a fresh **live session token** (~24 h lifetime). That handshake is pure cryptographic exchange — no phone, no browser, no human. The Saturday-night-into-Sunday server reset still happens server-side, but from the client's perspective the next OAuth handshake on Sunday morning *just works*; the SDK regenerates the live session token and reopens the brokerage session via `/iserver/auth/ssodh/init`. Quote from the IBKR OAuth design doc:

> "The goal of the authorization flow is to establish automatically-expiring live session tokens without requiring user re-authorization."
> — IBKR OAuth design PDF, 2018 (still authoritative for the 1.0a flow in 2026).
> https://www.interactivebrokers.com/webtradingapi/oauth.pdf

This is the central reason the migration plan should prefer OAuth 1.0a over IBC/ibeam.

### What real prop traders / quants actually do, May 2026

Three patterns dominate the field; pick the one whose tradeoffs match your operational tolerance:

1. **OAuth 1.0a + ibind on a cloud VM** — used by the long tail of solo retail quants in 2025–2026. Pros: no gateway process, no daily restart, no Sunday tap, runs in a small container, ~one screen of Python. Cons: REST-only (no native streaming for market data — WebSocket exists but is more involved), order book is `/iserver`-shaped, **OAuth 1.0a for retail is officially "not supported" so IBKR can change policy unilaterally**. This is the recommended path for CALYPSO based on this research.
2. **Dockerized IB Gateway + IBC + ibeam + Android-emulator 2FA helper** — used by people who need the full TWS API (streaming, market depth, complex order types). The Sunday tap is "solved" only via the emulator+Appium hack (community-maintained, not merged upstream). Brittle, but it does work. Most prop shops with infrastructure budget run this on a dedicated VM with a paid uptime SLA.
3. **Managed relay (TradersPost, PickMyTrade, QuantConnect-hosted)** — third party holds the IB Gateway session, handles the Sunday tap on their side, and exposes a webhook/REST surface to your strategy. Adds latency (PickMyTrade quotes 34 s median end-to-end TradingView→IBKR in March 2026) and a monthly fee, but eliminates the operational headache entirely. https://pickmytrade.io/broker/tradingview-to-ib/ib_faq

### If "Sunday phone tap" is still the answer for the TWS/Gateway path — confirmed, explicitly

Yes. As of May 2026, for any deployment that uses TWS or IB Gateway (with or without IBC/ibeam), the **weekly 2FA push approval on the IBKR Mobile app every Sunday after 01:00 ET is still required** and cannot be disabled. Direct quote from IBKR's own documentation, reproduced above:

> "This procedure will require manual authentication once a week, the first time you log into the platform after the security tokens have been invalidated. This security process occurs each Sunday at 1:00 am ET."
> — https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm

This is consistent across IBKR's official 2FA FAQ, the QuantConnect docs, the PickMyTrade 2026 guide, and the GitHub issue history of IBC and ibeam. There is no documented bypass for retail Pro accounts using the TWS/Gateway path. **The only documented escape is to leave the TWS/Gateway path entirely and use OAuth 1.0a Web API instead.**

---

## Sources (consolidated, with access dates)

All accessed May 13, 2026 unless otherwise noted. IBKR Campus pages return HTTP 403 to direct fetch; their content is reconstructed from search-engine snippets and from secondary SDK / community documentation that quotes them verbatim.

- IBKR Campus, Trading Web API — https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/ [via search]
- IBKR Campus, OAuth 1.0A Extended — https://www.interactivebrokers.com/campus/ibkr-api-page/oauth-1-0a-extended/ [via search + ibind wiki mirror]
- IBKR Campus, Web API Changelog — https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-changelog/ [via search]
- IBKR Campus, Authenticating with the Client Portal REST API podcast/article — https://www.interactivebrokers.com/campus/traders-insight/authenticating-with-the-ibkr-client-portal-rest-api/
- IBKR OAuth design PDF, 2018 (still authoritative for 1.0a in 2026) — https://www.interactivebrokers.com/webtradingapi/oauth.pdf
- IBKR Guides, TWS Auto Restart Considerations — https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm
- IBKR Guides, 2FA FAQ — https://ibkrguides.com/securelogin/sls/faq.htm
- `Voyz/ibind` repo (README + wiki + releases), v0.1.23 shipped Apr 21, 2025; wiki edited Apr 11, 2026 — https://github.com/Voyz/ibind
- `Voyz/ibind` wiki, OAuth 1.0a page — https://github.com/Voyz/ibind/wiki/OAuth-1.0a
- `Voyz/ibind` issue #102 (OAuth 2.0 feature request, still open) — https://github.com/Voyz/ibind/issues/102
- `Voyz/ibind` issue #113 (Zero wait time on token regeneration, Jun 10, 2025) — https://github.com/Voyz/ibind/issues/113
- `Voyz/ibeam` issue #14 (2FA with IBKey, open since 2021) — https://github.com/Voyz/ibeam/issues/14
- `Voyz/ibeam` PR #277 (2FA method selection, 2025) — https://github.com/Voyz/ibeam/pull/277
- `IbcAlpha/IBC` issue #161 (Is there a way to automate the 2FA?) — https://github.com/IbcAlpha/IBC/issues/161
- QuantConnect docs, Interactive Brokers brokerage — https://www.quantconnect.com/docs/v2/lean-cli/live-trading/brokerages/interactive-brokers
- QuantConnect forum, *Why not use Web API with OAuth for IBKR live trade?* — https://www.quantconnect.com/forum/discussion/19458/why-not-use-web-api-with-oatuh-for-ibkr-living-trade/
- PickMyTrade blog, *TradingView to Interactive Brokers Automation: The 2026 Guide* — https://blog.pickmytrade.io/tradingview-interactive-brokers-automation-2026/
- codestudy.net, *How to Get a Consumer Key from the IBKR Registration API*, Nov 30, 2025 — https://www.codestudy.net/blog/interactive-brokers-how-to-get-a-consumer-key-from-registration-api/

### Items flagged as uncertain

- The exact IBKR Campus phrasing on retail OAuth 2.0 timeline ("no ETA at this time") cannot be directly fetched in May 2026 — both interactivebrokers.com and web.archive.org refused the fetch. The quote is consistent across at least four secondary citations (PickMyTrade, codestudy.net, ibind wiki, QuantConnect forum) so we treat it as authoritative, but flag it `[unconfirmed by direct fetch]`.
- The Feb 2026 community report that ibind users have "retired their IB Gateway container" is from a search-result snippet, not a direct comment URL — `[unconfirmed at primary source]` but consistent with the v0.1.23 release notes and the wiki claim.
- IBKR's officially-stated position ("OAuth 1.0a is for institutional") versus the operational reality ("self-service portal accepts individual registrations and the flow works") is a genuine contradiction. Both are sourced; the operational reality is more authoritative for the migration decision because it is what the bot will actually experience at the API. The risk is that IBKR closes the loophole; that risk is the main reason to keep an IB Gateway-based fallback path documented even if you primarily run OAuth 1.0a.
