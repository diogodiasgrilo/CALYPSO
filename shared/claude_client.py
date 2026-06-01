"""
Thin Claude API wrapper for CALYPSO agents.

Provides a consistent interface for agents to call the Claude API.
API key is loaded from: environment variable → Secret Manager → config.

Usage:
    from shared.claude_client import get_anthropic_client, ask_claude

    client = get_anthropic_client(config)
    response = ask_claude(
        client,
        system_prompt="You are a trading analyst.",
        user_prompt="Analyze today's P&L...",
    )
"""

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default model — best cost/quality balance for analysis tasks
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096

# Intra-process call pacing (2026-05-31). Agents like HOMER fire ~5 calls of
# ~13k input tokens back-to-back; that burst exceeds the account's per-minute
# input-token limit (ITPM — e.g. ~30k on Anthropic Tier 1 for Sonnet) → HTTP
# 429 (the SDK rides it out via max_retries below, so the agent still SUCCEEDS,
# but the 429 generates a rate-limit email).
# Default 35s spaces consecutive calls so that AT MOST 2 land in any 60s window
# (~26k input < Tier-1's ~30k ITPM) → no 429, no email, even on the lowest tier.
# HOMER's evening run is a batch job (not latency-sensitive), so ~3 min for its
# 5 calls is free. Lower CALYPSO_CLAUDE_MIN_INTERVAL_S if you raise your tier.
_MIN_CALL_INTERVAL_S = float(os.environ.get("CALYPSO_CLAUDE_MIN_INTERVAL_S", "35.0"))
_pace_lock = threading.Lock()
_last_call_at = 0.0


def get_anthropic_client(config: Optional[Dict[str, Any]] = None):
    """
    Create an Anthropic client with API key from env, Secret Manager, or config.

    Priority:
        1. ANTHROPIC_API_KEY environment variable
        2. Secret Manager (calypso-anthropic-api-key) — on GCP
        3. config["anthropic"]["api_key"] — local dev fallback

    Args:
        config: Optional config dict with anthropic.api_key.

    Returns:
        anthropic.Anthropic client, or None if no API key found.
    """
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package not installed (pip install anthropic)")
        return None

    # 1. Environment variable
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    # 2. Secret Manager (GCP)
    if not api_key:
        try:
            from shared.secret_manager import get_secret, is_running_on_gcp

            if is_running_on_gcp():
                api_key = get_secret("calypso-anthropic-api-key")
                if api_key:
                    logger.debug("Using API key from Secret Manager")
        except ImportError:
            pass

    # 3. Config fallback
    if not api_key and config:
        api_key = config.get("anthropic", {}).get("api_key")
        if api_key:
            logger.debug("Using API key from config")

    if not api_key:
        logger.error(
            "No Anthropic API key found. Set ANTHROPIC_API_KEY env var, "
            "store in Secret Manager, or add to config."
        )
        return None

    # max_retries above the SDK default (2) so a transient 429/overload is
    # ridden out with exponential backoff rather than surfacing as a failed
    # agent report.
    return anthropic.Anthropic(api_key=api_key, timeout=120.0, max_retries=5)


def ask_claude(
    client,
    system_prompt: str,
    user_prompt: str,
    model: str = None,
    max_tokens: int = None,
) -> Optional[str]:
    """
    Send a prompt to Claude and return the text response.

    Args:
        client: Anthropic client from get_anthropic_client().
        system_prompt: System instructions for Claude.
        user_prompt: The user message / data to analyze.
        model: Model ID (default: claude-sonnet-4-6).
        max_tokens: Max output tokens (default: 4096).

    Returns:
        Claude's text response, or None on error.
    """
    if client is None:
        logger.error("Cannot call Claude: client is None")
        return None

    if not model:
        model = DEFAULT_MODEL
    if max_tokens is None:
        max_tokens = DEFAULT_MAX_TOKENS

    # Pace consecutive calls within this process so a multi-call agent run
    # (e.g. HOMER's ~5 back-to-back narrative calls) does not burst past the
    # account's per-minute token limit and trigger a 429 + rate-limit email.
    global _last_call_at
    if _MIN_CALL_INTERVAL_S > 0:
        with _pace_lock:
            wait = _last_call_at + _MIN_CALL_INTERVAL_S - time.monotonic()
            if wait > 0:
                logger.debug("ask_claude: pacing %.1fs to stay under rate limit", wait)
                time.sleep(wait)
            _last_call_at = time.monotonic()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        if response.content and len(response.content) > 0:
            text = response.content[0].text
            logger.info(
                f"Claude response: {len(text)} chars, "
                f"input={response.usage.input_tokens}, "
                f"output={response.usage.output_tokens}"
            )
            return text

        logger.warning("Claude returned empty response")
        return None

    except Exception as e:
        logger.error(f"Claude API call failed: {e}")
        return None
