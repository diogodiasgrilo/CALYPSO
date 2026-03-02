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
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default model — best cost/quality balance for analysis tasks
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096


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

    return anthropic.Anthropic(api_key=api_key, timeout=120.0)


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
