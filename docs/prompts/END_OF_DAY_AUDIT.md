# End-of-Day HYDRA Audit Prompt

**Purpose:** Reusable prompt for a full end-of-day audit of HYDRA code, config, docs, agents, dashboard, logs, and Telegram after a day of changes.

**How to invoke:** Tell Claude `Read docs/prompts/END_OF_DAY_AUDIT.md and execute it exactly.`

---

## Task: End-of-Day HYDRA Audit — Execute Methodically

Create a TodoWrite list from the checklist below and execute each item sequentially. Do not skip, reorder, or batch steps. Mark each todo complete only after its verification passes.

---

## Pre-Audit: Establish Today's Change Set (MANDATORY FIRST STEP)

Before touching anything else, produce a **definitive, 100%-confirmed inventory** of everything changed today:

1. Run `git log --since="midnight" --oneline` and `git diff` against the day's starting commit
2. List every modified file with a one-line summary of what changed
3. Separately list any **VM-side config changes** made today (SSH to `calypso-bot` and diff `bots/hydra/config/config.json` against the repo template — flag drift)
4. Cross-reference with CLAUDE.md section 11 ("HYDRA bot") and recent memory entries to confirm nothing is missing
5. Print the final inventory back to the user before proceeding to step 1 — do not start the audit until this list is complete and verified

---

## Audit Checklist (execute in order)

### 1. Code Audit — Today's Changes
Review every code change from the pre-audit inventory. For each hunk:
- Verify logic correctness against the stated intent
- Check for off-by-one, wrong operator, wrong sign, wrong unit (fraction vs percent), wrong field name
- Verify attribute/key names against the actual class/dict definitions (ODYSSEUS Pass 1 check #6)
- Confirm config keys read by new code exist on the VM with correct values

### 2. Dead Code & Misspellings
- Scan changed + adjacent files for unused imports, unreferenced functions, orphaned variables, unreachable branches
- Check identifier spelling: function names, dict keys, config keys, string literals, log messages, constant names
- Flag any new code that is defined but never called

### 3. Stability & Hang Risk
- Every new `requests.*`, `thread.join`, `fcntl.flock`, `gspread.*`, subprocess call — verify explicit timeout
- Check for bare `except:`, silent exception swallowing, unsafe threading, infinite loops without break conditions
- Verify no regression in the "bot must never freeze" invariant (Fix #64, #68)
- Confirm no change breaks existing safety features (MKT-046, MKT-045, stop monitoring, settlement reconciliation)

### 4. Documentation Sync
- `CLAUDE.md` — HYDRA section reflects today's changes (entry times, VIX regime table, MKT rules, flag names)
- `docs/HYDRA_STRATEGY_SPECIFICATION.md` — decision flows and MKT rules current
- `bots/hydra/README.md` — matches deployed behavior
- `services/hydra_strategy_context.md` — current for agent consumption
- `shared/__init__.py` and `bots/hydra/__init__.py` — exports current, version bumped if applicable
- Docstrings on modified functions — describe new behavior
- Inline comments — explain WHY of non-obvious changes (not WHAT)
- "Last Updated" dates refreshed on every modified `.md`

### 5. HYDRA VM Logging
- Every log message, banner, startup summary, heartbeat line referencing:
  - Entry times
  - VIX regime thresholds
  - Credit thresholds
  - Conditional entry names (Upday-035, Downday-035, etc.)
  - Enabled/disabled flags (E6, E7, MKT-036, base-downday, etc.)
  must match today's code and VM config
- Check startup banner, daily summary, per-entry decision logs, skip reasons, stop event messages

### 6. Agent Alignment (APOLLO, HERMES, HOMER, CLIO, ARGUS)
- Each agent's prompt/context file under `services/*/` references the **current** strategy (entry count, entry times, MKT rules, VIX regime, conditional flags)
- HOMER journal sections reflect today's config
- HERMES post-trade analysis references correct flag names and expected behaviors
- APOLLO pre-market briefing references today's entry schedule
- No agent references removed/disabled features as if active

### 7. HYDRA Dashboard
- `dashboard/frontend/` — text copy, page headers, tooltips, card labels, formulas match today's config
- `dashboard/backend/` — API responses expose any new state fields; `/api/hydra/bot-config` reads correct flags
- E6/E7 UI conditional rendering logic matches current flag state
- Any displayed thresholds, multipliers, or schedule times — verify against config
- Scriptable widget (`dashboard/scriptable/HYDRA_Widget.js`) — entry count and labels current

### 8. Telegram Commands
- All 15 commands (`/status`, `/config`, `/entry N`, `/snapshot`, etc.) return values consistent with today's VM config
- `/set` accepts any new config keys introduced today
- Command help text, response formatting, and skip-reason messages reflect current strategy
- ENTRY_SKIPPED alert reasons map to every code path that can skip an entry

---

## Execution Rules

- After each step, print a one-line pass/fail with issue count (e.g., `Step 3: 2 issues found — missing timeout in X, bare except in Y`)
- Fix issues as they are found; do not defer
- After all 8 steps pass, run ODYSSEUS Pass 2 and Pass 3 from CLAUDE.md as final confirmation
- Do not commit or push until the user confirms the audit report
- Final deliverable: consolidated report listing files changed, issues found, issues fixed, and verification that all 8 areas are in sync
