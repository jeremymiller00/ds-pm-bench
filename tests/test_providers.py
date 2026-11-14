"""The provider SDK Adapters against fake SDK clients: call shape, tokens, errors, keys."""

from pathlib import Path
from types import SimpleNamespace

import anthropic
import openai
import pytest

from bench import CandidateReply, CandidateToolError, ConfigError, MissingSecret, Workspace
from bench.core.providers import AnthropicAdapter, OpenAIAdapter, candidate_adapter
from test_workspace_candidates import candidate, write_candidate

REQUEST = None  # the SDK errors only keep the request for display


class FakeEndpoint:
    """Stands in for client.messages or client.responses: records the call, replays."""

    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def anthropic_message(text: str = "Hello", *, stop_reason: str = "end_turn"):
    blocks = [SimpleNamespace(type="text", text=text)] if text else []
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=12, output_tokens=34),
    )


def openai_response(text: str = "Hello"):
    return SimpleNamespace(
        output_text=text, status="completed",
        usage=SimpleNamespace(input_tokens=56, output_tokens=78),
    )


def test_the_anthropic_adapter_sends_one_user_message_and_reads_its_tokens():
    endpoint = FakeEndpoint(anthropic_message("A framed problem."))
    adapter = AnthropicAdapter(SimpleNamespace(messages=endpoint), "some-claude-model",
                               max_tokens=4000, temperature=0.2)

    assert adapter.send("prompt\n\ninput") == CandidateReply("A framed problem.", 12, 34)
    assert endpoint.calls == [{
        "model": "some-claude-model",
        "messages": [{"role": "user", "content": "prompt\n\ninput"}],
        "max_tokens": 4000,
        "temperature": 0.2,
    }]


def test_the_openai_adapter_sends_the_text_as_input_and_reads_its_tokens():
    endpoint = FakeEndpoint(openai_response("A framed problem."))
    adapter = OpenAIAdapter(SimpleNamespace(responses=endpoint), "some-gpt-model",
                            max_tokens=4000)

    assert adapter.send("prompt\n\ninput") == CandidateReply("A framed problem.", 56, 78)
    assert endpoint.calls == [
        {"model": "some-gpt-model", "input": "prompt\n\ninput", "max_output_tokens": 4000}
    ]


@pytest.mark.parametrize(
    ("make", "client", "error"),
    [
        (AnthropicAdapter, "messages", anthropic.APIConnectionError(request=REQUEST)),
        (OpenAIAdapter, "responses", openai.APIConnectionError(request=REQUEST)),
    ],
)
def test_an_sdk_api_error_is_a_tool_error(make, client, error):
    adapter = make(SimpleNamespace(**{client: FakeEndpoint(error)}), "m")

    with pytest.raises(CandidateToolError, match="APIConnectionError"):
        adapter.send("x")


@pytest.mark.parametrize(
    ("make", "client", "empty"),
    [
        (AnthropicAdapter, "messages", anthropic_message("", stop_reason="refusal")),
        (OpenAIAdapter, "responses", openai_response("")),
    ],
)
def test_a_reply_with_no_text_is_a_tool_error(make, client, empty):
    adapter = make(SimpleNamespace(**{client: FakeEndpoint(empty)}), "m")

    with pytest.raises(CandidateToolError, match="no text"):
        adapter.send("x")


def load(workspace_dir: Path, **overrides):
    write_candidate(workspace_dir, "c@1", candidate(baseline=True, **overrides))
    return Workspace.open(workspace_dir).candidate("c@1")


def test_the_key_falls_back_to_the_env_file_and_the_model_to_the_version(
    workspace_dir: Path, monkeypatch
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (workspace_dir / ".env").write_text("# secrets\nexport ANTHROPIC_API_KEY='sk-test-1'\n")
    c = load(workspace_dir)  # tool Claude, version claude-opus-5-5

    adapter = candidate_adapter(c, workspace_dir)

    assert isinstance(adapter, AnthropicAdapter)
    assert adapter.model == "claude-opus-5-5"
    assert adapter.client.api_key == "sk-test-1"
    assert adapter.client.max_retries == 0  # the Workspace's one retry is the only one


def test_settings_name_the_provider_and_model(workspace_dir: Path, monkeypatch):
    monkeypatch.setenv("MY_OPENAI_KEY", "sk-test-2")
    c = load(workspace_dir, tool="Some Tool", env_vars=["MY_OPENAI_KEY"],
             settings={"provider": "openai", "model": "some-gpt-model"})

    adapter = candidate_adapter(c, workspace_dir)

    assert isinstance(adapter, OpenAIAdapter)
    assert adapter.model == "some-gpt-model"
    assert adapter.client.max_retries == 0


def test_an_unknown_provider_names_the_setting(workspace_dir: Path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-3")
    c = load(workspace_dir, tool="Mystery")

    with pytest.raises(ConfigError, match="settings.provider"):
        candidate_adapter(c, workspace_dir)


def test_a_missing_key_names_the_variable_never_a_value(workspace_dir: Path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (workspace_dir / ".env").write_text("OTHER_KEY=sk-secret-value\n")
    c = load(workspace_dir)

    with pytest.raises(MissingSecret) as raised:
        candidate_adapter(c, workspace_dir)

    assert "ANTHROPIC_API_KEY" in str(raised.value)
    assert "sk-secret-value" not in str(raised.value)
