"""
Shared utilities for CALYPSO Claude API agents (APOLLO, HERMES, HOMER, CLIO).

Provides a single source of truth for HYDRA strategy context, loaded from
`services/hydra_strategy_context.md`. When strategy/config changes, update
that one file — all agents automatically pick up changes on next run.
"""

from pathlib import Path

_STRATEGY_CONTEXT_PATH = Path(__file__).parent / "hydra_strategy_context.md"


def load_hydra_strategy_context() -> str:
    """Return the current HYDRA strategy context as a string.

    Reads `services/hydra_strategy_context.md` fresh on each call (so
    updates to the file are picked up without agent restart for tools
    that call this per-request).

    Returns empty string if file is missing — agent still functions,
    just without the shared context block. This prevents a missing/renamed
    file from breaking all 4 agents simultaneously.
    """
    try:
        return _STRATEGY_CONTEXT_PATH.read_text()
    except (FileNotFoundError, OSError):
        return ""


def inject_strategy_context(prompt_template: str,
                             placeholder: str = "{STRATEGY_CONTEXT}") -> str:
    """Replace a placeholder in a prompt template with the shared strategy context.

    Use this pattern in agent system prompts to keep strategy info in one place:

        _PROMPT = '''You are AGENT_NAME...

        {STRATEGY_CONTEXT}

        ## Agent-specific guidance...
        '''

        SYSTEM_PROMPT = inject_strategy_context(_PROMPT)

    If the placeholder isn't in the template, returns the template unchanged.
    """
    context = load_hydra_strategy_context()
    return prompt_template.replace(placeholder, context)
