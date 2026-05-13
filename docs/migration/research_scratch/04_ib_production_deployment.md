# Interactive Brokers — Production Deployment on a Linux VM (May 2026)

**Audience:** Operator of a 0DTE SPX iron-condor bot running as a Python
systemd service on a Debian Google Compute Engine VM. Migrating from Saxo
(stateless OAuth REST) to IB (stateful gateway + persistent session).

**TL;DR.** Moving to IB means moving from a stateless OAuth REST integration
to a stateful Java gateway that must be up 24×7, automatically re-logged-in
after the **Sunday 01:00 ET token reset**, and watchdogged from inside your
Python process. The two viable headless stacks in 2026 are:

1. **IB Gateway + IBC + ib_async** (TWS binary protocol on port 4001/4002)
2. **clientportal.gw + IBeam** (Client Portal Web API on port 5000)

For a low-frequency 0DTE bot, **option 1 is the better default** — lower
latency, simpler order/options surface, mature watchdog ecosystem. Option 2 is
for cases where a REST/WebSocket facade matters more than latency.

Either way, **2FA on a headless server is the operationally painful part**, and
in 2026 there is still no fully automated bypass — you log in once on Sunday
night with IBKR Mobile push approval, IBC's `AutoRestartTime` keeps the
weekly token alive across daily restarts, and the rest of the week is
unattended.

---

## 1. Gateway options

### 1.1 IB Gateway (TWS-lite)

IB Gateway is the stripped-down JVM application that exposes the TWS binary
API. It is **the production-recommended endpoint** — TWS itself is heavier
(full GUI, watchlists, charts) and IBKR explicitly recommends Gateway for
unattended automation ([Holowczak tutorial][holowczak]).

- **Latest stable / latest channels (May 2026)** — IB Gateway **10.45.1e**
  (stable) and **10.46.1f** (latest), per the `gnzsnz/ib-gateway-docker`
  release tags ([gnzsnz releases][gnzsnz-releases]). Direct downloads at
  [IB Gateway Latest][ibgw-latest].
- **Critical:** IBC requires the **offline** (also called "standalone") version
  of the installer, **not** the self-updating one ([IBC user guide][ibc-userguide]).
  The self-updating build silently replaces binaries and breaks IBC's window
  hooks.
- **Memory.** Field reports put a fixed `-Xmx512m` heap as "fine for a week
  at a time on Linux," with typical RSS in the 120-200 MB range
  ([Elite Trader thread][et-memory]). IBKR's own guidance for full TWS caps
  the recommendation at 2 GB to avoid degrading the host; Gateway sits well
  below that ([IBKR — Increase TWS Memory Size][ibkr-memsize]).
- **CPU.** Negligible at idle (sub-1% of one vCPU on `e2-small`). Spikes only
  during market open / large historical data requests.
- **Linux installer** (`ibgateway-stable-standalone-linux-x64.sh`) is a
  self-extracting shell archive that drops the JVM, the Gateway jars, and a
  `jts.ini` template into `~/Jts/ibgateway/<version>/`. Run non-interactively
  with the `-q` flag.
- **Headless mode.** Gateway is a Swing app and **does need an X server** —
  there is no documented `-J-Dcom.ib.gateway.headless=true` toggle. The
  standard production trick is to wrap it in `Xvfb` (X Virtual Framebuffer)
  so it draws to an in-memory framebuffer, then optionally attach `x11vnc`
  for emergency manual interaction. This is exactly what the gnzsnz Docker
  image does internally ([gnzsnz README][gnzsnz-readme]).
- **Ports.** `4001` = live API, `4002` = paper API. (TWS uses `7496` live /
  `7497` paper.) These are bound to **localhost only by default** — see §7
  for why that matters on a public GCE IP.

### 1.2 Client Portal Gateway (`clientportal.gw`)

Standalone Java app that exposes the IBKR Client Portal Web API — REST over
HTTPS + a WebSocket channel for streaming.

- **Download.** `clientportal.gw.zip` from
  `https://download2.interactivebrokers.com/portal/clientportal.gw.zip`
  ([IB Campus — Gateway Install][ibkr-cpgw-install]).
- **Install.** Unzip, then `bin/run.sh root/conf.yaml` (Linux/macOS) or
  `bin\run.bat root\conf.yaml` (Windows). Requires a modern Java runtime
  on `PATH`.
- **Port.** TLS on `5000` by default — health check at
  `GET https://localhost:5000/v1/api/iserver/auth/status` ([IBeam wiki —
  Installation][ibeam-install]).
- **Why people use it.** REST/WebSocket is friendlier for polyglot stacks
  and serverless callers than the proprietary TWS binary protocol.
- **Why people don't.** Sessions die after roughly 24 hours of inactivity
  and must be re-authenticated; you cannot avoid the IBeam-or-equivalent
  shim if you want unattended operation.

### 1.3 TWS (Trader Workstation) — skip

Full Swing GUI. ~1 GB RSS. No upside over Gateway for a headless bot. Use
Gateway.

---

## 2. IBC (IB Controller)

### 2.1 What it is, who maintains it

**[IbcAlpha/IBC][ibc-repo]** — Java program that drives the Gateway/TWS
window via the Java AWT event queue: types your username/password into the
Login dialog, clicks Login, dismisses the daily nags, schedules restarts.
Forked in 2018 from the now-defunct `ib-controller/ib-controller`; the
IbcAlpha fork is **actively maintained** (last commit early May 2026 per
[search results][ibc-search]). Runs on Linux, macOS, Windows.

### 2.2 What it solves

- **Auto-login** after every Gateway start — types `IbLoginId` / `IbPassword`
  into the dialog within `LoginDialogDisplayTimeout` seconds.
- **Daily restart** without 2FA. IBC issues the "restart" command (introduced
  in TWS 974+) so the gateway tears down its JVM at `AutoRestartTime` and
  comes back with the same weekly token — **no 2FA challenge** until the
  Sunday reset. This is the lynchpin of headless IB operations.
- **Suppress UI dialogs** — paid-for-bid warnings, account-type nags,
  exit confirmations.
- **API auto-accept** — `AcceptIncomingConnectionAction=accept` so your
  Python client doesn't have to click a dialog every time it reconnects.

### 2.3 Key `config.ini` entries

Defaults from [IBC's bundled config.ini][ibc-configini]:

```ini
# Credentials
IbLoginId=                      # your IBKR username
IbPassword=                     # your IBKR password
TradingMode=paper               # paper | live

# Session management
ExistingSessionDetectedAction=manual            # manual | primary | secondary
ReloginAfterSecondFactorAuthenticationTimeout=no
SecondFactorAuthenticationTimeout=180

# Daily restart — survives the weekly token
AutoRestartTime=                # e.g. 11:45 PM in the host's TZ
AutoLogoffTime=                 # leave empty if using AutoRestartTime
ColdRestartTime=                # full process restart (re-auth required)
ClosedownAt=                    # tidy shutdown after Friday close

# API
AcceptIncomingConnectionAction=manual   # set to "accept" for unattended
AllowBlindTrading=no                    # set to "yes" for naked options
TrustedTwsApiClientIPs=                 # e.g. 127.0.0.1
ReadOnlyLogin=no                        # see §4.4
ReadOnlyApi=                            # leave unset to inherit Gateway setting

# Diagnostic
LogStructureWhen=never
LogStructureScope=known
```

**Behavior split** that confuses everyone first time:

| Setting | Survives weekly token? | Triggers 2FA? |
|---|---|---|
| `AutoRestartTime` (TWS 974+) | yes | no |
| `AutoLogoffTime` | no | yes |
| `ColdRestartTime` | no | yes |

Set `AutoRestartTime`. Leave the others empty. Per IBKR's auto-restart docs
([Auto Restart Considerations][ibkr-autorestart]), the **security token is
invalidated each Sunday at 01:00 ET**, so the only mandatory human-attended
login is Sunday evening before the Asia open.

### 2.4 2FA — the operational reality

**There is no fully unattended 2FA path for live IBKR accounts in 2026.**
Confirmed across the [IBC user guide][ibc-userguide] ("IBC cannot
automatically complete login if Interactive Brokers have given you a card
or device that you must use during login"), the [IBKR Secure Login FAQ][ibkr-2fa-faq],
and multiple recent Elite Trader threads cited in our search results.

What real automated traders do:

1. **The "Sunday evening tap"** — most common. Friday close → Gateway runs
   through Sunday 01:00 ET → token expires → Sunday ~16:00 ET (before Forex
   open) the operator opens IBKR Mobile, taps the IB Key push notification,
   approves. IBC catches the login window and proceeds. **Approving the
   push notification is the entire weekly maintenance burden.** Everything
   else (Mon–Fri daily restarts) happens via `AutoRestartTime` with no
   challenge.
2. **`ReloginAfterSecondFactorAuthenticationTimeout=yes`** — if the operator
   misses the 3-minute push window, IBC will retry the login automatically,
   issuing a fresh push. Saves the trip back to the desk on Sunday night.
3. **`ReadOnlyLogin=yes` + a phone call to IBKR** — IBKR will, **on request**,
   relax the 2FA requirement on accounts flagged read-only at the cost of
   "certain guarantees should you suffer losses." This works for
   market-data-only sidecars (e.g. quoting servers, dashboards) but **not
   for a bot that places orders.** It is not a "bypass" — IBKR is granting
   a documented relaxation.
4. **`IBEAM_TWO_FA_HANDLER=PYOTP`** — only useful if you've enrolled IBKR's
   "Mobile Authenticator" (HOTP/TOTP) **and** you're on the Client Portal
   path. On the TWS path this is moot; IBC has no equivalent OTP injector.
5. **Hardware Security Card / DSC+** — IBC explicitly cannot type these in.
   Don't enroll one if you want IBC to work.

The Elite Trader / Reddit consensus across 2024–2026 is: live with the
Sunday tap, set `ReloginAfterSecondFactorAuthenticationTimeout=yes`, run
`AutoRestartTime=23:45` in the box's TZ, and move on.

### 2.5 Docker landscape (May 2026)

| Image | Maintainer state | Notes |
|---|---|---|
| **`ghcr.io/gnzsnz/ib-gateway`** | Active; stable 10.45.1e + latest 10.46.1f tags as of May 2026 ([releases][gnzsnz-releases]) | Bundles Gateway + IBC 3.23.0 + Xvfb + x11vnc + socat. Supports paper-and-live simultaneously, `TWS_USERID_PAPER` for the second account. Multi-arch (amd64 + experimental aarch64). |
| **`voyz/ibeam`** | Active; 0.5.12 released April 2026 ([Voyz/ibeam][ibeam-repo]) | Client Portal Gateway only — different stack. |
| **`manhinhang/ib-gateway-docker`** | Unmaintained since 2022 | Skip. |

**Recommendation:** `ghcr.io/gnzsnz/ib-gateway:stable` pinned by the exact
patch tag (e.g. `10.45.1e`). Pinning matters — IB silently changes the
binary protocol between minors and your `ib_async` pin needs to keep step.

---

## 3. IBeam — Client Portal Gateway companion

[Voyz/ibeam][ibeam-repo] is the IBC-of-the-CP-world: a Python supervisor
that drives a headless Chromium via Selenium to type credentials into the
gateway's login page, watch the session, re-authenticate when the cookie
expires.

- **Latest release.** 0.5.12, April 21 2026.
- **Image.** `docker pull voyz/ibeam`.
- **What it does, mechanically.**
  1. Starts `clientportal.gw` on port 5000 inside the container.
  2. Spawns headless Chrome, navigates to the login page.
  3. Types `IBEAM_ACCOUNT` / `IBEAM_PASSWORD`.
  4. If the page presents a 2FA prompt, invokes `IBEAM_TWO_FA_HANDLER`
     (`PYOTP` for TOTP secret, `GOOGLE_MSG` for Android Messages SMS scraping,
     `EXTERNAL_REQUEST` to call an HTTP webhook, or `CUSTOM_HANDLER` to load
     your own Python class).
  5. Every `IBEAM_MAINTENANCE_INTERVAL` seconds (default **60**) hits
     `/v1/api/tickle` to keep the session alive and
     `/v1/api/iserver/auth/status` to verify authentication.
  6. On failure: up to `IBEAM_MAX_REAUTHENTICATE_RETRIES` (default 3)
     soft re-auths, then restarts the Gateway process if
     `IBEAM_RESTART_FAILED_SESSIONS=True`.

- **Key env vars** (full table in our search results, abbreviated here;
  see [IBeam Configuration wiki][ibeam-config]):

  ```
  IBEAM_ACCOUNT                  # username
  IBEAM_PASSWORD                 # password
  IBEAM_TWO_FA_HANDLER           # PYOTP | GOOGLE_MSG | EXTERNAL_REQUEST | CUSTOM_HANDLER
  IBEAM_PYOTP_SECRET             # TOTP shared secret if using PYOTP
  IBEAM_MAINTENANCE_INTERVAL=60
  IBEAM_REQUEST_TIMEOUT=15
  IBEAM_RESTART_FAILED_SESSIONS=True
  IBEAM_MAX_FAILED_AUTH=5
  IBEAM_HEALTH_SERVER_PORT=5001
  IBEAM_GATEWAY_BASE_URL=https://localhost:5000
  ```

- **Session lifetime.** A live CP session expires after **24 hours**
  regardless of activity; IBeam's reauthenticate route
  (`/v1/portal/iserver/reauthenticate?force=true`) refreshes it transparently.
  The weekly Sunday 01:00 ET reset still applies and will force a hard
  re-login.

- **Security note from the IBeam README** — credentials live in env vars
  by default; anyone with `docker inspect` or shell access can read them.
  Use Docker secrets or GCP Secret Manager → CSI driver in real
  deployments.

---

## 4. 2FA on a headless server — the matrix

| Method | Works with IBC? | Works with IBeam? | Sunday-tap-free? |
|---|---|---|---|
| **IB Key (IBKR Mobile push)** | yes — operator approves on phone, IBC catches login | yes via `IBEAM_AUTHENTICATION_STRATEGY=B` and tapping the push | no — still needs a phone tap each Sunday |
| **Mobile Authenticator (TOTP/HOTP)** | no — IBC has no OTP injector | yes — `IBEAM_TWO_FA_HANDLER=PYOTP` + `IBEAM_PYOTP_SECRET` | **yes** — fully programmatic |
| **SMS** | partially — IBC can use the `SecondFactor*` config to wait | yes — `IBEAM_TWO_FA_HANDLER=GOOGLE_MSG` scrapes Android Messages | maybe — depends on scraper |
| **Email** | no | possible via `CUSTOM_HANDLER` | yes if you build it |
| **Digital Security Card+ (DSC+)** | **no — IBC documents it cannot type the code** | no | no |
| **`ReadOnlyLogin=yes` + IBKR relaxation** | yes | yes | yes, but **no trading** |

**Bottom line for a 0DTE order-placing bot:** IB Key + Sunday tap is the
only sane path on the TWS/Gateway side. On the CP/IBeam side you can get
fully unattended **only** if you migrate your account to Mobile
Authenticator (TOTP) and disable IB Key. IBKR allows that switch from
Client Portal → Settings → User Settings → Two-Factor Authentication.

---

## 5. Weekly server reset and session lifecycle

Per the [IBKR Auto Restart Considerations doc][ibkr-autorestart] plus the
search results aggregated above:

- **Security tokens invalidated weekly:** Sunday **01:00 ET**.
- **Server maintenance window:** Friday 23:00 ET → Sunday 16:00 ET (one
  hour before Forex open). API connections may be rejected during much of
  this window; auth definitely fails.
- **Daily reset:** Gateway/TWS internally restart at `AutoRestartTime` (or
  the IBKR-set default, **23:45 in the host TZ** if you don't override).
  Required by IBKR — leaving the JVM running > 24 h without a restart
  leads to memory leaks and connectivity weirdness.
- **`AutoRestartTime` preserves the weekly token.** This is the magic. The
  process exits and re-execs with the cached auth cookie; no 2FA.
- **Client Portal:** the underlying `iserver` session is **separately**
  capped at ~24 h regardless of platform restart. IBeam's
  `IBEAM_MAINTENANCE_INTERVAL=60` tickles the session every minute to keep
  it pinned within that window; the **weekly reset still applies** because
  IBKR forces a fresh login when the auth token is invalidated.

**Cron pattern for the Sunday tap reminder** (run on the GCE VM, sends a
notification to your phone):

```cron
# 15 min before IBKR Sunday relogin window opens
0 15 * * 0   /usr/local/bin/notify-pushover "Tap IBKR Mobile in 15 min"
0 16 * * 0   /usr/local/bin/notify-pushover "Tap IBKR Mobile NOW"
```

If you forgo this and let IBC's
`ReloginAfterSecondFactorAuthenticationTimeout=yes` retry, you'll just get
re-pushed every ~3 minutes until you tap. Annoying but recoverable.

---

## 6. Docker deployment

### 6.1 `docker-compose.yml` — IB Gateway + IBC (TWS path)

Adapted from the official [gnzsnz docker-compose][gnzsnz-compose] with
hardening:

```yaml
services:
  ib-gateway:
    image: ghcr.io/gnzsnz/ib-gateway:10.45.1e   # pin to exact stable
    container_name: ibgw
    restart: always
    environment:
      TWS_USERID_FILE: /run/secrets/tws_userid
      TWS_PASSWORD_FILE: /run/secrets/tws_password
      TRADING_MODE: live                # live | paper | both
      TWS_SETTINGS_PATH: /home/ibgateway/Jts
      VNC_SERVER_PASSWORD_FILE: /run/secrets/vnc_password
      READ_ONLY_API: "no"
      TWOFA_TIMEOUT_ACTION: restart     # restart container on 2FA stall
      TWOFA_EXIT_INTERVAL: "60"
      AUTO_RESTART_TIME: "23:45 PM"
      RELOGIN_AFTER_2FA_TIMEOUT: "yes"
      TIME_ZONE: America/New_York       # match IBKR's reset clock
      JAVA_HEAP_SIZE: "768"             # MB, -Xmx
    ports:
      # Bind to loopback ONLY — never expose 4001/4002 to public internet.
      - "127.0.0.1:4001:4003"           # live API
      - "127.0.0.1:4002:4004"           # paper API
      - "127.0.0.1:5900:5900"           # VNC, for emergencies
    volumes:
      - ./tws_settings:/home/ibgateway/Jts
      - ./ibc/config.ini:/home/ibgateway/ibc/config.ini:ro
    secrets:
      - tws_userid
      - tws_password
      - vnc_password
    healthcheck:
      test: ["CMD-SHELL", "nc -z localhost 4003 || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 90s

secrets:
  tws_userid:   { file: ./secrets/tws_userid }
  tws_password: { file: ./secrets/tws_password }
  vnc_password: { file: ./secrets/vnc_password }
```

Important detail per the gnzsnz README: the **internal** Gateway ports
are 4001/4002 but the container republishes them at 4003/4004 via `socat`
so non-loopback connections work. On a single-tenant GCE VM the loopback
binding (`127.0.0.1:4001:4003`) is what you want.

### 6.2 `docker-compose.yml` — CP Gateway + IBeam (REST path)

```yaml
services:
  ibeam:
    image: voyz/ibeam:0.5.12
    container_name: ibeam
    restart: always
    environment:
      IBEAM_ACCOUNT_FILE: /run/secrets/ibkr_user
      IBEAM_PASSWORD_FILE: /run/secrets/ibkr_pass
      IBEAM_TWO_FA_HANDLER: PYOTP
      IBEAM_PYOTP_SECRET_FILE: /run/secrets/totp_secret
      IBEAM_MAINTENANCE_INTERVAL: "60"
      IBEAM_RESTART_FAILED_SESSIONS: "True"
      IBEAM_LOG_LEVEL: INFO
    ports:
      - "127.0.0.1:5000:5000"        # CP Gateway HTTPS
      - "127.0.0.1:5001:5001"        # IBeam health server
    secrets: [ibkr_user, ibkr_pass, totp_secret]
    healthcheck:
      test: ["CMD-SHELL",
             "curl -sk https://localhost:5000/v1/api/iserver/auth/status | grep -q authenticated"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 120s

secrets:
  ibkr_user:    { file: ./secrets/ibkr_user }
  ibkr_pass:    { file: ./secrets/ibkr_pass }
  totp_secret:  { file: ./secrets/totp_secret }
```

### 6.3 Volumes / persistence

- `~/Jts/` — Gateway settings, login cookies, jts.ini. **Persist this.**
  Losing it triggers IBKR's "new device" 2FA challenge on next login,
  which is a different, more painful UX than the standard IB Key push.
- `ibc/config.ini` — mount read-only.
- `~/Jts/launcher.log` and `~/Jts/ibgateway/*/launcher.log` — primary
  diagnostics. Ship to Cloud Logging via the docker logging driver
  (`--log-driver=gcplogs`).

### 6.4 Network posture

**Bind 4001/4002/5000 to `127.0.0.1` only.** Public exposure of the TWS API
port is a documented attack surface — there is no authentication on a TWS
API socket beyond the Gateway's "accept incoming connection" dialog, which
IBC pre-clicks. If your bot is on the same VM, loopback is sufficient. If
the bot is on a separate VM, use a Wireguard / Tailscale tunnel; **do not**
open the port to a CIDR.

---

## 7. GCE VM specifics

### 7.1 Sizing

- **`e2-small` (2 vCPU shared, 2 GB RAM)** is the practical floor for IB
  Gateway alone. ~150 MB RSS at idle, spikes to ~400 MB during heavy
  historical data requests; Xvfb adds ~30 MB.
- **`e2-medium` (1 vCPU dedicated, 4 GB RAM)** if Python bot + Gateway
  co-tenant. This is the sweet spot for a 0DTE iron-condor bot.
- **`n2-standard-2`** if you also colocate Cloud Logging agent +
  Prometheus exporter + dev shell. Overkill for the bot itself.

### 7.2 Firewall

```bash
# Default-deny everything except SSH (35.235.240.0/20 = IAP) and outbound.
gcloud compute firewall-rules create allow-iap-ssh \
  --network=default --direction=INGRESS \
  --source-ranges=35.235.240.0/20 --rules=tcp:22 --action=ALLOW

# No inbound rule for 4001/4002/5000 — they stay on loopback.
```

Verify with `ss -tlnp` on the VM that the Gateway port is bound to
`127.0.0.1:4001`, never `0.0.0.0:4001`.

### 7.3 systemd ordering

Gateway must be ready **before** the bot tries to connect. Use
`Requires=` + `After=` + a port-ready waiter:

```ini
# /etc/systemd/system/ib-gateway.service
[Unit]
Description=IB Gateway (Docker)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/ibgw
ExecStart=/usr/bin/docker compose up
ExecStop=/usr/bin/docker compose down
Restart=always
RestartSec=15s

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/calypso-bot.service
[Unit]
Description=Calypso 0DTE iron-condor bot
Requires=ib-gateway.service
After=ib-gateway.service

[Service]
Type=notify
WorkingDirectory=/opt/calypso
EnvironmentFile=/etc/calypso/env
# Wait for Gateway port before exec — fails fast if Gateway didn't come up
ExecStartPre=/usr/bin/timeout 120 bash -c 'until (echo > /dev/tcp/127.0.0.1/4001) 2>/dev/null; do sleep 2; done'
ExecStart=/opt/calypso/.venv/bin/python -m calypso.bot
Restart=on-failure
RestartSec=30s
WatchdogSec=120

[Install]
WantedBy=multi-user.target
```

The `ExecStartPre` TCP-readiness probe is the production trick — `After=`
alone only guarantees unit ordering, not "the JVM finished booting and
opened a socket." The Gateway takes 30-90 s from container start to API
ready; without the waiter the bot crashes on first `IB.connect()`.

### 7.4 Logs

- Gateway: `~/Jts/launcher.log`, `~/Jts/ibgateway/<ver>/launcher.log`,
  `~/Jts/api.<port>.log` (API frames if `LogLevel=detail` in jts.ini).
- IBC: writes to stdout (captured by Docker → Cloud Logging via
  `gcplogs` driver).
- IBeam: writes to stdout and `IBEAM_OUTPUTS_DIR`. Mount that directory if
  you want persistent failure screenshots
  (`IBEAM_ERROR_SCREENSHOTS=True`).

---

## 8. Disconnect / reconnect handling in the Python client

### 8.1 Use `ib_async`, not `ib_insync`

`ib_insync` (erdewit) has been quietly unmaintained since late 2023. The
community fork **[ib-api-reloaded/ib_async][ib-async-repo]** is the active
project (latest release **2.1.0**, December 2025 per PyPI; works on
Python 3.13, Python 3.14 issues open). API surface is a near drop-in:
rename the import, keep the rest.

### 8.2 The `Watchdog` pattern

`ib_async` ships a `Watchdog` class (port of `ib_insync.Watchdog`) for
exactly this scenario:

```python
from ib_async import IB, IBC, Watchdog

ibc = IBC(
    twsVersion=1045,        # major-minor, e.g. 1045 for 10.45.x
    gateway=True,
    tradingMode='live',
    twsPath='/opt/ibc/IBC',
    twsSettingsPath='/home/ibgateway/Jts',
)

ib = IB()

def on_connected():
    # Re-subscribe to market data, re-arm option chains, etc.
    log.info("IB connected, re-arming subscriptions")

def on_disconnected():
    log.warning("IB disconnected — Watchdog will restart")

ib.connectedEvent    += on_connected
ib.disconnectedEvent += on_disconnected

wd = Watchdog(
    ibc, ib,
    host='127.0.0.1', port=4001,
    appStartupTime=45,     # seconds to wait for Gateway boot
    appTimeout=20,         # idle-timeout that triggers liveness probe
    retryDelay=5,
)
wd.start()
ib.run()                  # blocks; Watchdog runs in same event loop
```

Per the [ib_insync API docs][ibinsync-api]: Watchdog issues a small
historical-data probe whenever inbound traffic falls silent for
`appTimeout` seconds. Probe succeeds → continue. Probe fails →
`hardTimeoutEvent` fires, Watchdog calls `IBC.terminate()` and respawns
the Gateway, then reconnects `IB` automatically. **Your code only sees the
`connectedEvent` again.**

### 8.3 Open-orders persistence at disconnect

Orders are **broker-side** state. If your TCP connection drops while an
order is in flight, IBKR's order book is unaffected — the order continues
its lifecycle on IB's servers. On reconnect, ask for open orders with
`ib.reqAllOpenOrders()` and reconcile against your local state. This is
the model that lets the Sunday reset work at all.

**Caveat:** Orders submitted with `clientId=0` are visible to **all**
client IDs (including manual TWS); orders with `clientId>0` are visible
only to that client ID after reconnect. Pick a fixed `clientId` for the
bot and stick with it — `clientId=7` is a common convention.

### 8.4 API-silence heartbeat

If you trade thinly liquid expiries you can go minutes without an inbound
tick. Add an explicit heartbeat:

```python
async def heartbeat():
    while True:
        await asyncio.sleep(30)
        # currentTime() is a cheap server roundtrip
        try:
            await asyncio.wait_for(ib.reqCurrentTimeAsync(), timeout=5)
        except (asyncio.TimeoutError, Exception):
            log.error("Heartbeat failed — Watchdog will handle")
            # Don't disconnect manually; let Watchdog see the appTimeout
```

---

## 9. Paper + live on the same VM

Yes — and during the Saxo→IB migration you want this. Two patterns:

**Pattern A (recommended): two IBC containers, two accounts.**
`gnzsnz/ib-gateway` natively supports `TRADING_MODE=both` with
`TWS_USERID_PAPER` / `TWS_PASSWORD_PAPER`; it runs two Gateway JVMs in one
container, paper on 4002 and live on 4001 ([gnzsnz README][gnzsnz-readme]).

**Pattern B: separate compose stacks** on different host ports, each with
its own `~/Jts` volume. Cleaner blast radius if one Gateway misbehaves.

Either way, the Python bot picks an instance via host:port + a distinct
`clientId`:

```python
PAPER = ('127.0.0.1', 4002, 7)
LIVE  = ('127.0.0.1', 4001, 7)
ib.connect(*PAPER)  # or LIVE
```

Run the migration dry-run on paper for at least one full week — including
a Sunday reset — before flipping live.

---

## 10. Comparison summary — Saxo vs IB Gateway vs CP Gateway

| Axis | **Saxo OpenAPI** | **IB Gateway + TWS API** | **CP Gateway + Web API** |
|---|---|---|---|
| Transport | HTTPS REST + WebSocket | Proprietary TCP binary on 4001/4002 | HTTPS REST + WebSocket on 5000 |
| Auth | OAuth2 refresh tokens (server-side) | Username/password + 2FA via IB Key push | Username/password + 2FA, session cookie |
| Sidecar process needed? | **No** | Yes — Gateway JVM | Yes — `clientportal.gw` JVM |
| 2FA cadence | None (refresh token rotates) | **Weekly Sunday tap** on phone | **Weekly Sunday tap** + 24h CP session refresh |
| Unattended weekday operation | yes | yes (via IBC `AutoRestartTime`) | yes (via IBeam tickle + reauthenticate) |
| Order placement latency (ms, GCE → exchange) | ~60-120 (REST) | ~5-15 (binary TCP) | ~60-120 (REST) |
| Options chain ergonomics | Decent | Best in class (`reqSecDefOptParams`, `qualifyContracts`) | Good (`/iserver/secdef/info`) |
| Reconnect-on-disconnect | App-level retry | `Watchdog` does it in-process | `IBeam` does it in-process |
| Ops burden / week | ~0 minutes | **~1 minute** (Sunday tap) + occasional Gateway crash recovery | ~1 minute (Sunday tap) + occasional CP cookie nonsense |
| Cost | bundled | bundled | bundled |
| Python lib in 2026 | `saxo-openapi` | **`ib_async` 2.x** (drop-in for `ib_insync`) | `ibind`, raw `requests` |

**For a 0DTE iron-condor bot on a Debian GCE VM, the recommendation is
unambiguous:** IB Gateway + IBC (via `gnzsnz/ib-gateway:stable`) + `ib_async`
+ `Watchdog`. You buy ~50ms of order latency back vs CP REST, get the
mature `qualifyContracts` flow for SPX options, and the only weekly burden
is one tap on your phone.

---

## 11. Migration acceptance checklist (CALYPSO-specific)

- [ ] Pin `ghcr.io/gnzsnz/ib-gateway:10.45.1e` in compose.
- [ ] Move IBKR creds to GCP Secret Manager; mount via `--secret` not env.
- [ ] Bind 4001/4002 to `127.0.0.1` only; verify with `ss -tlnp`.
- [ ] `AutoRestartTime=23:45 PM` in `TIME_ZONE=America/New_York`.
- [ ] `ReloginAfterSecondFactorAuthenticationTimeout=yes`.
- [ ] `ReadOnlyApi=no`, `AcceptIncomingConnectionAction=accept`,
      `TrustedTwsApiClientIPs=127.0.0.1`.
- [ ] systemd `ExecStartPre` TCP probe on 127.0.0.1:4001.
- [ ] Pushover/Telegram cron at Sunday 16:00 ET local for the tap reminder.
- [ ] `ib_async.Watchdog` wrapping `IB`, with `appTimeout=20`, `appStartupTime=45`.
- [ ] Reconciliation routine on `connectedEvent` calling `reqAllOpenOrders`.
- [ ] One full Sunday-reset cycle on paper before live cutover.

---

## Sources

- [IbcAlpha/IBC repository (active fork, May 2026)][ibc-repo]
- [IBC User Guide][ibc-userguide]
- [IBC config.ini defaults][ibc-configini]
- [Voyz/ibeam repository (v0.5.12, April 2026)][ibeam-repo]
- [IBeam Installation wiki][ibeam-install]
- [IBeam Configuration wiki (full env var list)][ibeam-config]
- [IBeam Two-Factor Authentication wiki][ibeam-2fa]
- [gnzsnz/ib-gateway-docker][gnzsnz-readme]
- [gnzsnz releases — 10.45.1e stable, 10.46.1f latest as of May 2026][gnzsnz-releases]
- [gnzsnz docker-compose.yml][gnzsnz-compose]
- [ib-api-reloaded/ib_async — successor to ib_insync][ib-async-repo]
- [ib_insync API docs (Watchdog class still authoritative)][ibinsync-api]
- [IBKR Auto Restart Considerations][ibkr-autorestart]
- [IBKR Two-Factor Authentication FAQ][ibkr-2fa-faq]
- [IBKR Two-Factor Authentication Methods][ibkr-2fa-methods]
- [IB Gateway Latest download][ibgw-latest]
- [IBKR — Increase TWS Memory Size][ibkr-memsize]
- [IB Campus — Client Portal Gateway install][ibkr-cpgw-install]
- [Holowczak — Installing IB Gateway on Linux][holowczak]
- [QuantConnect/IBAutomater (alternative to IBC, C#)][ibautomater]
- [Elite Trader — 2FA with Automated Trading thread (referenced)][et-2fa]
- [Elite Trader — IB TWS memory thread (referenced)][et-memory]

[ibc-repo]: https://github.com/IbcAlpha/IBC
[ibc-userguide]: https://github.com/IbcAlpha/IBC/blob/master/userguide.md
[ibc-configini]: https://github.com/IbcAlpha/IBC/blob/master/resources/config.ini
[ibc-search]: https://github.com/IbcAlpha/IBC/commits/master
[ibeam-repo]: https://github.com/Voyz/ibeam
[ibeam-install]: https://github.com/Voyz/ibeam/wiki/Installation-and-startup
[ibeam-config]: https://github.com/Voyz/ibeam/wiki/IBeam-Configuration
[ibeam-2fa]: https://github.com/Voyz/ibeam/wiki/Two-Factor-Authentication
[gnzsnz-readme]: https://github.com/gnzsnz/ib-gateway-docker
[gnzsnz-releases]: https://github.com/gnzsnz/ib-gateway-docker/releases
[gnzsnz-compose]: https://github.com/gnzsnz/ib-gateway-docker/blob/master/docker-compose.yml
[ib-async-repo]: https://github.com/ib-api-reloaded/ib_async
[ibinsync-api]: https://ib-insync.readthedocs.io/api.html
[ibkr-autorestart]: https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm
[ibkr-2fa-faq]: https://ibkrguides.com/securelogin/sls/faq.htm
[ibkr-2fa-methods]: https://ibkrguides.com/securelogin/sls/twofactorauth.htm
[ibgw-latest]: https://www.interactivebrokers.com/en/trading/ibgateway-latest.php
[ibkr-memsize]: https://www.ibkrguides.com/traderworkstation/increase-tws-memory-size.htm
[ibkr-cpgw-install]: https://www.interactivebrokers.com/campus/ibkr-quant-news/interactive-brokers-gateway-install-setup/
[holowczak]: https://holowczak.com/installing-ib-gateway-for-linux/
[ibautomater]: https://github.com/QuantConnect/IBAutomater
[et-2fa]: https://www.elitetrader.com/et/threads/2-factor-auth-with-automated-trading.368494/
[et-memory]: https://www.elitetrader.com/et/threads/ib-tws-taking-a-lot-of-memory.254591/
