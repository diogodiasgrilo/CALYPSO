"""
HOMER narrative generator — uses Claude API for observations, market labels,
and improvement assessments. All other journal content is Python-generated
for accuracy.

Best Practice Compliance:
    - System prompt includes CRITICAL RULES, domain knowledge, output format
    - User prompts wrap data in <data> XML tags
    - Anti-hallucination guardrails in all prompts
    - Graceful degradation: returns empty strings on failure
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are HOMER, the automated trading journal writer for the HYDRA bot.
HYDRA trades SPX 0DTE iron condors — a FULLY AUTOMATED bot that makes all decisions algorithmically.

## CRITICAL RULES — Read These First

1. **ONLY use data that is explicitly provided in <data> tags.** Do NOT invent or estimate any numbers.
2. **If a metric is missing, say "not available"** — never guess or approximate.
3. **Quote specific numbers FIRST, then interpret.** Example: "$545 net P&L on 3 entries" not "good day."
4. **Do NOT hallucinate performance statistics.** Every number you cite must appear in the data.
5. **HYDRA is FULLY AUTOMATED** — do not give human trading advice. Comment on bot behavior and rules only.
6. **Do NOT repeat generic trading wisdom.** Every observation must be specific to THIS day's data.

## HYDRA Domain Knowledge (v1.19.0 — use these exact parameters)

- Entry times: 10:15, 10:45, 11:15 ET (3 base entries). Conditional E6 at 14:00 fires as up-day put-only when SPX rises >= 0.48% above open (Upday-035). E7 is DISABLED.
- Smart entry windows (MKT-031): DISABLED (v1.10.4). Enter at scheduled times only.
- VIX-scaled spread width (MKT-027): formula `round(VIX * 5.3 / 5) * 5`, floor 25pt, cap 83pt
- Min credit thresholds: $1.35 calls, $2.10 puts (MKT-011). MKT-029 graduated fallback for both sides: -$0.05, -$0.10 (call floor $0.75, put floor $2.07). Put-only when call non-viable AND VIX < 15 (MKT-032/MKT-039). Call-only when put non-viable (MKT-040, 89% WR).
- Stop formula: Asymmetric buffers — call: total_credit + $0.35 (call_stop_buffer), put: total_credit + $1.55 (put_stop_buffer). MKT-040 call-only (put non-viable): call + $2.60 theo put + call buffer. Put-only (MKT-039): credit + $1.55 put buffer. MKT-038 call-only: call + $2.60 theo put + call buffer.
- Stop confirmation (MKT-036): DISABLED. Code preserved but dormant.
- Stop close: BOTH LEGS closed via market order (default mode; configurable short_only_stop for MKT-025)
- Whipsaw filter: whipsaw_range_skip_mult = 1.50 — skip entry if SPX intraday range > 1.5x expected daily move.
- Down-day call-only (base entries): E1-E3 convert to call-only when SPX drops >= 0.57% from open.
- Up-day filter (Upday-035): E6 at 14:00 fires as put-only when SPX rises >= 0.48% above open. E7 is DISABLED.
- FOMC T+1 call-only (MKT-038): Day after FOMC announcement: all entries forced to call-only. T+1 = 66.7% down days, 23% more volatile.
- FOMC announcement skip (MKT-008): DISABLED (fomc_announcement_skip=false). HYDRA now trades on FOMC days.
- 2026 FOMC dates: Jan 27-28, Mar 17-18, Apr 28-29, Jun 16-17, Jul 28-29, Sep 15-16, Oct 27-28, Dec 8-9. Announcement = Day 2. T+1 = day after Day 2.
- Progressive tightening: MKT-020 (calls) and MKT-022 (puts) scan from wide OTM inward
- Early close (MKT-018): DISABLED (backtest showed hold-to-expiry beats all ROC thresholds)
- Base entries are full iron condors, put-only (MKT-011 override), or call-only (down-day >= 0.57% or MKT-038 FOMC T+1).
- EMA 20/40 trend signal is informational only (logged but doesn't drive entry type)
- Account: $35K margin, 1 contract per entry

## Tone

Analytical, concise, factual. Write like a professional trading journal — no emojis, no speculation.
Focus on what happened and why it matters for understanding HYDRA's behavior."""


def generate_day_narratives(
    client,
    day_data: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, str]:
    """
    Generate all narrative sections for a trading day.

    Args:
        client: Anthropic client from get_anthropic_client().
        day_data: Day-specific data from collect_day_data().
        config: Agent config.

    Returns:
        Dict with keys: "observations", "market_label", "assessment".
        Values are empty strings if generation fails (graceful degradation).
    """
    narratives = {
        "observations": "",
        "market_label": "",
        "assessment": "",
    }

    summary = day_data.get("summary", {})
    entries = day_data.get("entries", [])
    hermes_report = day_data.get("hermes_report")

    # Build XML-wrapped data context for Claude (context chaining: includes HERMES report)
    data_context = _build_data_context(summary, entries, hermes_report)

    model = config.get("homer", {}).get("claude_model", "claude-sonnet-4-6")

    # Generate observations (Section 3)
    narratives["observations"] = _generate_observations(client, data_context, model)

    # Generate market character label (Section 4)
    narratives["market_label"] = _generate_market_label(client, data_context, model)

    # Generate improvement assessment (Section 9)
    narratives["assessment"] = _generate_assessment(client, data_context, model)

    return narratives


def _build_data_context(summary: Dict, entries: list, hermes_report: str = None) -> str:
    """Build XML-wrapped data context for Claude prompts.

    Context chaining: if a HERMES daily report is available, it's included
    so Claude has richer analysis context for narrative generation.
    """
    lines = []

    # Daily summary in XML tags
    lines.append('<data source="daily_summary">')
    for key, val in summary.items():
        lines.append(f"  {key}: {val}")
    lines.append("</data>")

    # Entry detail in XML tags
    if entries:
        lines.append("")
        lines.append('<data source="entry_detail">')
        for i, entry in enumerate(entries):
            entry_num = entry.get("Entry #", i + 1)
            lines.append(f"  Entry #{entry_num}:")
            for key, val in entry.items():
                lines.append(f"    {key}: {val}")
        lines.append("</data>")

    # Context chaining: HERMES daily analysis report
    if hermes_report:
        lines.append("")
        lines.append('<data source="hermes_daily_report">')
        lines.append(hermes_report.strip())
        lines.append("</data>")

    return "\n".join(lines)


def _generate_observations(client, data_context: str, model: str) -> str:
    """Generate 3-5 bullet point observations for Section 3."""
    from shared.claude_client import ask_claude

    prompt = f"""Based on the HYDRA trading day data below, write 3-5 concise bullet point observations.

Focus on:
- Notable entry outcomes (which survived, which stopped, why)
- Stop patterns (timing, clustering, which sides)
- Credit quality and VIX impact on spread widths
- MKT rule behavior (credit gate MKT-011, tightening MKT-020/022, down-day MKT-035, FOMC T+1 MKT-038)
- Anything unusual or noteworthy about this specific day

Output format:
- Each bullet: 1 sentence, max 2
- Use exact numbers from the data (dollar amounts, times, percentages)
- Start each line with a dash (-)
- Output bullets ONLY — no preamble, no headers, no conclusion

{data_context}"""

    result = ask_claude(client, SYSTEM_PROMPT, prompt, model=model, max_tokens=512)
    if result:
        # Clean up — ensure each line starts with -
        cleaned = []
        for line in result.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("-"):
                line = f"- {line}"
            if line:
                cleaned.append(line)
        return "\n".join(cleaned)
    return ""


def _generate_market_label(client, data_context: str, model: str) -> str:
    """Generate a short market character label for Section 4."""
    from shared.claude_client import ask_claude

    prompt = f"""Based on the trading day data below, generate a SHORT market character label (2-5 words).

Examples of correct labels:
- "Range-bound, calm"
- "Strong downtrend"
- "V-shaped reversal"
- "Morning dip/recovery"
- "Wide-range whipsaw"
- "Flat, low volatility"
- "Sustained selloff"
- "Gap up, fade"

Rules:
- Output ONLY the label, nothing else — no quotes, no explanation
- 2-5 words maximum
- Describe what SPX did (price action), NOT the bot's P&L or performance
- Use SPX Open/Close/High/Low and range to determine the pattern

{data_context}"""

    result = ask_claude(client, SYSTEM_PROMPT, prompt, model=model, max_tokens=50)
    if result:
        # Take first line, strip quotes and whitespace
        label = result.strip().split("\n")[0].strip().strip('"').strip("'")
        return label
    return "See data"


def _generate_assessment(client, data_context: str, model: str) -> str:
    """Generate a 2-3 sentence improvement assessment for Section 9."""
    from shared.claude_client import ask_claude

    prompt = f"""Based on the HYDRA trading day data below, write a 2-3 sentence post-improvement assessment.

Focus on:
- Which active MKT rules triggered and their impact:
  - MKT-011 (credit gate): Did it skip any entries? Were skips justified?
  - MKT-020/022 (progressive tightening): How far did strikes tighten?
  - MKT-035 (down-day filter): Did it trigger call-only entries? Were conditional entries placed?
  - MKT-038 (FOMC T+1): Was today T+1 after FOMC? Were all entries forced call-only?
  - Stop close mode: Both legs closed (default). Were stops efficient?
- Was this a good or bad day for the current strategy configuration?

Rules:
- 2-3 sentences ONLY
- Use exact numbers from the data
- Reference specific MKT rules by tag when they triggered
- Output the assessment text directly — no prefix like "Assessment:" or "Summary:"

{data_context}"""

    result = ask_claude(client, SYSTEM_PROMPT, prompt, model=model, max_tokens=300)
    if result:
        return result.strip()
    return ""
