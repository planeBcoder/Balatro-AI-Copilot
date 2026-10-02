import json
from pathlib import Path

import pytest

from ai.schemas import validate_decision
from ai.deepseek_client import DeepSeekClient, TransportError
from core.errors import CopilotError

PROMPT = Path(__file__).resolve().parents[1] / "prompts/decision_system.md"
CALC = {"candidates": [{"id": "p0", "action": "play", "cards": ["AS"]}]}


def response_content(value):
    return {"choices": [{"message": {"content": value}, "finish_reason": "stop"}]}


def valid():
    return {"recommendations": [{"rank": 1, "candidate_id": "p0", "action": "play", "cards": ["AS"], "win_probability": None, "probability_method": "unavailable", "reason": "优先打高牌，但得分有限。"}], "recommended_rank": 1, "summary": "按推荐操作。"}


def test_valid_schema():
    assert validate_decision(json.dumps(valid()), CALC).recommendations[0].cards == ["AS"]


@pytest.mark.parametrize("field,value", [("win_probability", 0.99), ("probability_method", "exact"), ("cards", ["KH"]), ("candidate_id", "not_real"), ("action", "buy"), ("rank", 3)])
def test_model_cannot_invent_probability_or_action(field, value):
    data = valid()
    data["recommendations"][0][field] = value
    with pytest.raises(ValueError):
        validate_decision(json.dumps(data), CALC)


def test_free_text_percent_not_displayed():
    data = valid()
    data["summary"] = "胜率99.9%"
    data["recommendations"][0]["reason"] = "胜率99%"
    d = validate_decision(json.dumps(data), CALC)
    assert "99" not in d.summary and "%" not in d.recommendations[0].reason


@pytest.mark.parametrize("claim", ["肯定能过关", "一定获胜", "必胜", "稳赢", "保证过关"])
def test_model_authored_guarantees_are_not_displayed(claim):
    data = valid()
    data["summary"] = claim
    assert claim not in validate_decision(json.dumps(data), CALC).summary


def test_invalid_then_repaired_and_official_payload():
    calls = []
    def transport(payload, key, timeout):
        calls.append(payload)
        return response_content("{" if len(calls) == 1 else json.dumps(valid()))
    client = DeepSeekClient("test-only", "deepseek-flash", PROMPT, transport)
    assert client.decide({}, CALC).recommended_rank == 1
    assert len(calls) == 2
    assert calls[0]["thinking"] == {"type": "disabled"}
    assert calls[0]["model"] == "deepseek-flash"
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["reasoning_effort"] == "none"


def test_invalid_twice_fails_without_crash():
    calls = []
    def transport(*args):
        calls.append(1)
        return response_content("not-json")
    with pytest.raises(CopilotError, match="格式"):
        DeepSeekClient("test-only", "deepseek-flash", PROMPT, transport).decide({}, CALC)
    assert len(calls) == 2


@pytest.mark.parametrize("status,count,match", [(401, 1, "Key"), (402, 1, "余额"), (429, 2, "繁忙"), (500, 2, "服务"), (None, 2, "网络")])
def test_api_errors_bounded_and_friendly(status, count, match, monkeypatch):
    calls = []
    monkeypatch.setattr("ai.deepseek_client.time.sleep", lambda _: None)
    def transport(*args):
        calls.append(1)
        raise TransportError(status)
    with pytest.raises(CopilotError, match=match):
        DeepSeekClient("test-only", "deepseek-flash", PROMPT, transport).decide({}, CALC)
    assert len(calls) == count


def test_automatic_api_smoke():
    calls = []
    def transport(payload, *_):
        calls.append(payload)
        return response_content('{"ok": true}')
    DeepSeekClient("test-only", "deepseek-flash", PROMPT, transport).smoke()
    assert len(calls) == 1 and calls[0]["max_tokens"] == 64
