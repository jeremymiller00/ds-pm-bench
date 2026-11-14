"""The real Candidate Adapters: Anthropic and OpenAI through their official Python SDKs.

A Candidate's provider is settings.provider, else read from its tool (Claude or
Anthropic; OpenAI, ChatGPT or GPT). Its model id is settings.model, else its version;
nothing here names a model. settings.max_tokens and settings.temperature are passed on
when set. The API key is read from the first of the Candidate's env_vars, in the
environment or else the git-ignored .env at the Workspace root (ADR-0005); a key that
is missing raises MissingSecret naming the variable, never a value.

The SDK clients are made with max_retries=0: the one retry the protocol allows is the
Workspace's (story 22), so an SDK must not add its own.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import anthropic
import openai

from bench.core.adapters import CandidateAdapter, CandidateReply
from bench.core.automated import model_for
from bench.core.candidates import CANDIDATES_FOLDER, Candidate
from bench.core.errors import CandidateToolError, MissingSecret
from bench.core.fields import FieldReader

ANTHROPIC = "anthropic"
OPENAI = "openai"
PROVIDERS = (ANTHROPIC, OPENAI)
_TOOL_PROVIDERS = {"claude": ANTHROPIC, "anthropic": ANTHROPIC,
                   "openai": OPENAI, "chatgpt": OPENAI, "gpt": OPENAI}
DEFAULT_MAX_TOKENS = 8192  # Anthropic requires a cap; used only when settings has none
ENV_FILE = ".env"


class AnthropicAdapter:
    """Sends text as one user message to the Messages API."""

    def __init__(self, client: anthropic.Anthropic, model: str, **options: Any) -> None:
        self.client = client
        self.model = model
        self.options = {"max_tokens": DEFAULT_MAX_TOKENS, **options}

    def send(self, text: str) -> CandidateReply:
        try:
            response = self.client.messages.create(
                model=self.model,
                messages=[{"role": "user", "content": text}],
                **self.options,
            )
        except anthropic.APIError as exc:
            raise CandidateToolError(f"Anthropic {type(exc).__name__}: {exc}") from exc
        output = "".join(b.text for b in response.content if b.type == "text")
        if not output:
            raise CandidateToolError(
                f"Anthropic returned no text (stop_reason {response.stop_reason})"
            )
        return CandidateReply(
            text=output,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class OpenAIAdapter:
    """Sends text as the input of one Responses API call."""

    def __init__(self, client: openai.OpenAI, model: str, **options: Any) -> None:
        self.client = client
        self.model = model
        self.options = options
        if "max_tokens" in self.options:
            self.options["max_output_tokens"] = self.options.pop("max_tokens")

    def send(self, text: str) -> CandidateReply:
        try:
            response = self.client.responses.create(
                model=self.model, input=text, **self.options
            )
        except openai.APIError as exc:
            raise CandidateToolError(f"OpenAI {type(exc).__name__}: {exc}") from exc
        if not response.output_text:
            raise CandidateToolError(f"OpenAI returned no text (status {response.status})")
        if response.usage is None:
            raise CandidateToolError("OpenAI returned no token usage")
        return CandidateReply(
            text=response.output_text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


def provider_for(candidate: Candidate) -> str:
    f = _reader(candidate)
    if "provider" in candidate.settings:
        return f.choice(candidate.settings["provider"], "settings.provider", PROVIDERS)
    provider = _TOOL_PROVIDERS.get(candidate.tool.strip().lower())
    if provider is None:
        raise f.fail(
            "settings.provider",
            f"is missing and tool {candidate.tool!r} names no known provider; "
            f"set one of {', '.join(PROVIDERS)}",
        )
    return provider


def read_secret(name: str, root: Path) -> str:
    """The value of an environment variable, else of its line in root/.env."""
    value = os.environ.get(name) or _env_file(root).get(name)
    if not value:
        raise MissingSecret(
            f"{name} is not set; export it or add it to {root / ENV_FILE} (git-ignored)"
        )
    return value


def candidate_adapter(candidate: Candidate, root: Path) -> CandidateAdapter:
    """The SDK Adapter for an automated Candidate, its API key read per ADR-0005."""
    provider = provider_for(candidate)
    if not candidate.env_vars:
        raise _reader(candidate).fail(
            "env_vars", "must name the environment variable holding the API key"
        )
    key = read_secret(candidate.env_vars[0], root)
    options = {k: candidate.settings[k] for k in ("max_tokens", "temperature")
               if k in candidate.settings}
    model = model_for(candidate)
    if provider == ANTHROPIC:
        return AnthropicAdapter(anthropic.Anthropic(api_key=key, max_retries=0), model,
                                **options)
    return OpenAIAdapter(openai.OpenAI(api_key=key, max_retries=0), model, **options)


def _reader(candidate: Candidate) -> FieldReader:
    return FieldReader(f"{CANDIDATES_FOLDER}/{candidate.id}.yaml")


def _env_file(root: Path) -> dict[str, str]:
    """NAME=value lines of root/.env; blank lines, comments and `export ` are allowed."""
    path = root / ENV_FILE
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.removeprefix("export ").partition("=")
        values[name.strip()] = value.strip().strip("'\"")
    return values
