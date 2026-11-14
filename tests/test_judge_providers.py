"""The provider JudgeModels against recorded SDK responses: JSON output, parsing, errors."""

import json
from types import SimpleNamespace

import anthropic
import openai
import pytest

from bench import JudgeReplyError, JudgeUnavailable
from bench.core.judging import JudgeRequest, parse_reply
from bench.core.providers import AnthropicJudge, OpenAIJudge
from bench.core.rubric import Criterion
from test_providers import FakeEndpoint

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
