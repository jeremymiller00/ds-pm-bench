"""The one registry of providers: the Candidate tools each provider's models go by.

It answers two questions. Which provider does a Candidate's tool name (a Candidate with
no settings.provider)? And which Candidate tools count as a judge model's family for the
self-preference bias flag (a judge model naming no family)?
"""

from __future__ import annotations

from collections.abc import Mapping

from bench.core.kinds import Provider

# Lower-case Candidate tool names that belong to each provider, the provider's own first.
PROVIDER_TOOLS: Mapping[Provider, tuple[str, ...]] = {
    Provider.ANTHROPIC: ("anthropic", "claude"),
    Provider.OPENAI: ("openai", "chatgpt", "gpt", "codex"),
    Provider.GOOGLE: ("google", "gemini"),
}


def provider_of_tool(tool: str) -> Provider | None:
    """The provider a Candidate tool names, or None when it names none."""
    name = tool.strip().lower()
    return next((p for p, tools in PROVIDER_TOOLS.items() if name in tools), None)


def family_of(provider: Provider) -> tuple[str, ...]:
    """The Candidate tools in the provider's family."""
    return PROVIDER_TOOLS[provider]
