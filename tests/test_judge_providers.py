"""The provider JudgeModels against recorded SDK responses: JSON output, parsing, errors."""

import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import openai
import pytest

from bench import JudgeReplyError, JudgeUnavailable, MissingSecret, Workspace
from bench.core.judging import JudgeRequest, parse_reply
from bench.core.providers import AnthropicJudge, OpenAIJudge
from bench.core.rubric import Criterion
from test_providers import FakeEndpoint
from test_workspace_judging import recorded_run, write_judge_setup
from test_workspace_judging import ws_dir as ws_dir  # noqa: F401  (fixture)

CRITERIA = (
    Criterion(id="correctness", name="Correctness", description="No invented facts."),
    Criterion(id="clarity", name="Clarity", description="Easy to follow."),
)
REQUEST = JudgeRequest(
    prompt="Score it.", input="Churn rose 4%.", criteria=CRITERIA,
    gold_answer="Predict churn.", output="Predict churn 60 days ahead.",
)
REPLY = json.dumps({"scores": {
    "correctness": {"score": 2, "reason": "Invents a 10% target."},
    "clarity": {"score": 4, "reason": "Clear sections."},
}})
SCHEMA = {
    "type": "object",
    "properties": {"scores": {
        "type": "object",
        "properties": {
            cid: {
                "type": "object",
                "properties": {
                    "score": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                    "reason": {"type": "string"},
                },
                "required": ["score", "reason"],
                "additionalProperties": False,
            }
            for cid in ("correctness", "clarity")
        },
        "required": ["correctness", "clarity"],
        "additionalProperties": False,
    }},
    "required": ["scores"],
    "additionalProperties": False,
}


def openai_response(text: str = REPLY, *, status: str = "completed"):
    """A recorded Responses API reply, trimmed to the fields the Judge reads."""
    return SimpleNamespace(output_text=text, status=status, incomplete_details=None)


def test_the_openai_judge_asks_for_strict_json_scoring_every_criterion():
    endpoint = FakeEndpoint(openai_response())
    judge = OpenAIJudge(SimpleNamespace(responses=endpoint), "some-gpt-model")

    reply = judge.judge(REQUEST)

    assert endpoint.calls == [{
        "model": "some-gpt-model",
        "input": REQUEST.render(),
        "text": {"format": {
            "type": "json_schema", "name": "judge_scores", "schema": SCHEMA, "strict": True,
        }},
    }]
    scores = parse_reply(reply, CRITERIA, "Judge openai/some-gpt-model")
    assert {cid: (j.score, j.reason) for cid, j in scores.items()} == {
        "correctness": (2, "Invents a 10% target."),
        "clarity": (4, "Clear sections."),
    }


def anthropic_message(text: str = REPLY, *, stop_reason: str = "end_turn"):
    """A recorded Messages API reply, trimmed to the fields the Judge reads."""
    blocks = [SimpleNamespace(type="text", text=text)] if text else []
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def test_the_anthropic_judge_asks_for_json_matching_the_schema():
    endpoint = FakeEndpoint(anthropic_message())
    judge = AnthropicJudge(SimpleNamespace(messages=endpoint), "some-claude-model")

    reply = judge.judge(REQUEST)

    assert endpoint.calls == [{
        "model": "some-claude-model",
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": REQUEST.render()}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
    }]
    scores = parse_reply(reply, CRITERIA, "Judge anthropic/some-claude-model")
    assert scores["correctness"].score == 2 and scores["clarity"].reason == "Clear sections."


@pytest.mark.parametrize(
    ("make", "endpoint", "result", "match"),
    [
        (OpenAIJudge, "responses", openai_response(status="incomplete"), "incomplete"),
        (OpenAIJudge, "responses", openai_response(""), "no text"),
        (AnthropicJudge, "messages", anthropic_message(stop_reason="max_tokens"),
         "max_tokens"),
        (AnthropicJudge, "messages", anthropic_message(stop_reason="refusal"), "refusal"),
        (AnthropicJudge, "messages", anthropic_message(""), "no text"),
    ],
)
def test_a_cut_off_refused_or_empty_reply_is_a_judge_reply_error(make, endpoint, result,
                                                                 match):
    judge = make(SimpleNamespace(**{endpoint: FakeEndpoint(result)}), "m")
    with pytest.raises(JudgeReplyError, match=match):
        judge.judge(REQUEST)


@pytest.mark.parametrize(
    ("make", "endpoint", "error"),
    [
        (OpenAIJudge, "responses", openai.APIConnectionError(request=None)),
        (AnthropicJudge, "messages", anthropic.APIConnectionError(request=None)),
    ],
)
def test_an_sdk_api_error_makes_the_judge_unavailable(make, endpoint, error):
    judge = make(SimpleNamespace(**{endpoint: FakeEndpoint(error)}), "m")
    with pytest.raises(JudgeUnavailable, match="APIConnectionError"):
        judge.judge(REQUEST)


def test_by_default_the_workspace_makes_each_judge_from_its_provider_and_key(
    workspace_dir: Path, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-a")
    (workspace_dir / ".env").write_text("OPENAI_API_KEY=sk-test-o\n")
    write_judge_setup(workspace_dir, mode="panel", models=[
        {"provider": "openai", "model": "some-gpt-model"},
        {"provider": "anthropic", "model": "some-claude-model"},
    ])
    ws = Workspace.open(workspace_dir)
    gpt, claude = (ws.judges(spec) for spec in ws.judge_setup.models)

    assert isinstance(gpt, OpenAIJudge) and gpt.model == "some-gpt-model"
    assert gpt.client.api_key == "sk-test-o" and gpt.client.max_retries == 0
    assert isinstance(claude, AnthropicJudge) and claude.model == "some-claude-model"
    assert claude.client.api_key == "sk-test-a" and claude.client.max_retries == 0


def test_judge_yaml_names_the_key_variable_and_it_is_not_part_of_the_hash(
    workspace_dir: Path, monkeypatch
):
    write_judge_setup(workspace_dir)
    before = Workspace.open(workspace_dir).judge_setup.hash
    monkeypatch.setenv("MY_JUDGE_KEY", "sk-test-m")
    write_judge_setup(workspace_dir, models=[
        {"provider": "openai", "model": "gpt-6", "env_var": "MY_JUDGE_KEY"},
    ])
    ws = Workspace.open(workspace_dir)
    [spec] = ws.judge_setup.models

    assert spec.env_var == "MY_JUDGE_KEY"
    assert ws.judges(spec).client.api_key == "sk-test-m"
    assert ws.judge_setup.hash == before


def test_a_missing_judge_key_names_the_variable_before_anything_is_logged(
    ws_dir: Path, monkeypatch  # noqa: F811
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ws = Workspace.open(ws_dir)
    run = recorded_run(ws)

    with pytest.raises(MissingSecret, match="OPENAI_API_KEY"):
        ws.judge_run(run, allow_uncertified=True)

    assert ws.decision_log() == ()
    assert run.unjudged == ("T1.1",)


def test_a_provider_with_no_judge_model_is_unavailable(workspace_dir: Path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    write_judge_setup(workspace_dir, models=[{"provider": "google", "model": "gemini-4"}])
    ws = Workspace.open(workspace_dir)

    with pytest.raises(JudgeUnavailable, match="google"):
        ws.judges(ws.judge_setup.models[0])
