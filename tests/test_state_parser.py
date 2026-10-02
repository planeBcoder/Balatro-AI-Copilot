import json

import pytest

from core.errors import CopilotError
from core.state_schema import parse_state


def test_observed_export(state_data):
    s = parse_state(json.dumps(state_data))
    assert [c.code for c in s.hand] == ["AH", "JS", "TH", "6S", "6H", "5C", "4H", "3C"]
    assert len(s.hand) == 8 and len(s.deck_remaining) == 44
    assert s.hands_left == 4 and s.discards_left == 4
    assert len(s.hand) + len(s.deck_remaining) == 52


@pytest.mark.parametrize("mutation", ["count", "card", "empty", "missing"])
def test_invalid_state(state_data, mutation):
    if mutation == "count":
        state_data["counts"]["deck_remaining"] -= 1
    elif mutation == "card":
        state_data["hand"][0]["code"] = "ZZ"
    elif mutation == "empty":
        state_data["hand"] = []
        state_data["counts"]["hand"] = 0
    else:
        del state_data["counts"]["deck_remaining"]
    with pytest.raises(CopilotError):
        parse_state(json.dumps(state_data))


@pytest.mark.parametrize("raw", ["{", "null", "[]", '{"game_state": "SELECTING_HAND"}'])
def test_partial_json(raw):
    with pytest.raises(CopilotError):
        parse_state(raw)


def test_modified_deck_duplicates_valid(state_data):
    state_data["deck_remaining"] = [state_data["deck_remaining"][0]] * 3
    state_data["counts"]["deck_remaining"] = 3
    assert len(parse_state(json.dumps(state_data)).deck_remaining) == 3


def test_menu_valid_but_not_playable(state_data):
    state_data["game_state"] = "SHOP"
    state_data["hand"] = []
    state_data["counts"]["hand"] = 0
    assert parse_state(json.dumps(state_data)).game_state == "SHOP"


def test_payload_does_not_predict_order(state_data):
    state_data["request_id"] = "test-id"
    data = parse_state(json.dumps(state_data)).ai_payload()
    assert "request_id" not in data
    assert [c["code"] for c in data["deck_remaining"]] == sorted(c["code"] for c in data["deck_remaining"])
