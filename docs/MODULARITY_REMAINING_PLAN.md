# Modularity — remaining work (strangle-driven completion)

> The plan to take the codebase from **~6.5 → 8+** (a true "pick legos off a shelf" state for new strategies). Produced 2026-06-08 after items 1, 2, 4 + item-3 pure kernels shipped (PR #1, PR #2). Companion to [MODULARITY_AUDIT.md](MODULARITY_AUDIT.md).

## Why a concrete strategy drives it

The cheap, unambiguous extractions are done. What remains — extracting `StrikeSelector`/`ExitRule` and generalizing leg-count + safety — is an **architectural refactor of the safety-critical live entry/stop/settlement core**. The *shape* of those abstractions should be validated against a real second consumer, not guessed. The chosen driver is a **0DTE SPX short strangle**: same instrument/expiry (lowest external risk), but it inverts the core assumptions (2 naked legs, no protective wings, a non-IC exit), so it forces every remaining abstraction to be correct and general.

**Key safety framing:** the strangle is built as a **new, dry-run, registered strategy** (the item-4a registry makes that one line). HYDRA's live A/B/C path is only touched where a change is *behavior-preserving for the iron condor* (every new strategy-policy flag defaults to today's IC behavior). The strangle never goes live without a separate, explicit operator flip.

## The core obstacle (why this can't be a single burst)

`_execute_entry` (`base_strategy.py:2168`) is built around *"buy protection first, hedge-pair every short"* and treats any unhedged short as a **CRITICAL naked-short emergency** (`_handle_naked_short`, 3 call sites: `base_strategy.py:2362`, `strategy.py:6033`, `strategy.py:6180`). A short strangle is *intentionally* all-naked — so this exact safety path must become **strategy-pluggable** before a strangle can run. A subtle error here = a real naked position or mis-fill. This is done as small, individually-tested, paper-verified steps — not a big-bang.

## Sequenced plan (each step: behavior-identical for the IC, tested, independently revertible)

| Step | Work | Risk | Unblocks |
|---|---|---|---|
| **S1 — Pluggable naked-short safety (item 6a)** | Add `requires_protective_wings = True` class attr on `MEICStrategy`. Gate `_handle_naked_short` (single chokepoint) + the 3 detection sites so an undefined-risk strategy doesn't treat its shorts as anomalies. IC keeps flag `True` → byte-identical. | **Low–Med** (touches the safety path, but default preserves it) | strangle entry not self-aborting |
| **S2 — Broker-authoritative margin (item 6b)** | Wire the existing-but-dead `what_if_order` (`ib_client.py:3398`) into `_check_buying_power`; **add it to the broker `ALLOWED_METHODS`** (gap N3) so it works in deployed broker mode. Gate behind a flag; IC keeps the defined-risk floor. | **Low** | correct strangle margin (defined-risk floor understates naked ~10×) |
| **S3 — Leg-count-agnostic execution (item 1 tail)** | Drive `_execute_entry` + `_unwind_partial_entry` + the hedge-pairing off `entry.legs` / `LEG_NAMES` instead of the 4 hardcoded blocks. Behavior-identical for the 4-leg IC (same order: longs→shorts). | **Med–High** (the safety-critical core) | any non-4-leg structure (strangle = 2 legs) |
| **S4 — `StrikeSelector` extraction (item 3)** | Extract HYDRA's strike selection into a `StrikeSelector` (chain + config → strikes); HYDRA's `_calculate_strikes` delegates to `IronCondorSelector`. The strangle ships a `StrangleSelector`. Two consumers prove the interface. | **Med** | strike logic composable, not IC-shaped |
| **S5 — `ExitRule` extraction (item 3)** | Extract an `ExitRule`/`StopPolicy` protocol from `_check_stop_losses`; HYDRA's credit+buffer stop becomes one rule. Brandon's existing pure exit legos (take_profit/breach/overlay) already fit this shape — adopt them as the proof. The strangle ships a naked-short stop rule. | **Med–High** (live stop dispatch) | exit logic composable per-strategy |
| **S6 — The strangle** | Assemble: register `StrangleStrategy` (`requires_protective_wings=False`, `StrangleSelector`, naked-short `ExitRule`), config, dry-run. End-to-end on the now-generalized machinery. | **Low** (additive, dry-run) | **proves the shelf works** |
| **S7 — Settlement/temporal generalization (item 5)** | Only if a multi-day strategy follows: persisted `expiry` field, split "new day" from "position lifecycle", holiday-aware expiry. Not needed for the 0DTE strangle. | **High** | multi-day strategies |

Steps **S1→S6** deliver a working second strategy and, with it, the genuine `StrikeSelector`/`ExitRule`/safety legos — the 8+ "compose a new strategy" state. S7 is deferred until a multi-day strategy is actually wanted.

## Execution discipline (the world-class part)

1. **Merge PR #1 + PR #2 first** — lock in the safe foundation (items 1/2/4 + kernels) before refactoring the core on top of it.
2. **One step per PR**, each: full suite green + a new capability test, behavior-identical for the IC, independently revertible.
3. **Paper-verify** after S1 and S3 (the safety-path steps) — confirm a real IC entry still places longs-first and a partial fill still unwinds correctly — before proceeding.
4. The strangle stays **dry-run**; going live is a separate, explicit operator decision (RB-8-style flip), never bundled into a refactor.

## Status
- ✅ Items 1, 2, 4, item-3 kernels (PR #1, PR #2) — modularity ~6.5.
- ▶ Next: **S1** (pluggable naked-short safety) — bounded, behavior-preserving, the first real strangle enabler.
