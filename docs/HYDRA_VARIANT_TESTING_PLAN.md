# HYDRA Variant Testing — Design Plan (superseded)

**Status:** **SUPERSEDED.** This was the original April 2026 design doc for running multiple HYDRA
variants in parallel (written against the **Saxo** broker, pre-IBKR-migration). It proposed a
`VariantOrchestrator` + YAML config DSL (`bots/hydra/variants/registry.yaml`) with a hardcoded N=5
variant lineup (`A`–`E` as "Baseline / Tight Tammy / Wider call buffers / Aggressive entries /
EMA-driven"). That specific design was **not** what got built.

**What actually shipped instead:** each variant (A/B/C/D/E) runs as its own systemd service/process
(not a single shared-process orchestrator), and cross-variant identity/comparability is handled by
the **strategy taxonomy** — `shared/strategy_taxonomy.py` (`StrategyMeta`/`GroupMeta`, the single
source of truth for which variants exist, their comparability group, and status) plus
`bots/hydra/registry.py` (name → strategy-class mapping). This replaced the old hardcoded
`_VARIANT_IDS` list model entirely. See:

- [`docs/STRATEGY_GROUPING_REDESIGN.md`](STRATEGY_GROUPING_REDESIGN.md) — the design + as-shipped
  state of the taxonomy/group system (current, IBKR-era).
- [`CLAUDE.md`](../CLAUDE.md) §"Variant Comparison (Dry-Run Head-to-Head)" — the current operator-facing
  description of the A–E variant scheme.
- [`docs/NEW_STRATEGY_PLAYBOOK.md`](NEW_STRATEGY_PLAYBOOK.md) — the current repeatable procedure for
  adding a new variant.

The original full content of this doc (architecture options considered, API-load math against Saxo,
phase-by-phase build plan, the YAML DSL sketch, and the risk/decision log) is preserved for historical
reference at [`docs/migration/archive/HYDRA_VARIANT_TESTING_PLAN.md`](migration/archive/HYDRA_VARIANT_TESTING_PLAN.md).
