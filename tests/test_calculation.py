import json
from collections import Counter

import pytest

from core.calculation import candidates, plain_score
from core.state_schema import parse_state


def test_candidate_legality_and_math_provenance(state_data):
    s = parse_state(json.dumps(state_data))
    calc = candidates(s, 500)
    assert calc["candidates"]
    for c in calc["candidates"]:
        assert 1 <= len(c["indices"]) <= 5
        assert c["cards"] == [s.hand[i].code for i in c["indices"]]
        assert len(set(c["indices"])) == len(c["indices"])
        assert c["win_probability"] is None
        if c["action"] == "discard":
            assert set(c["indices"]) | set(c["keep_indices"]) == set(range(8))
            assert not set(c["indices"]) & set(c["keep_indices"])
            if c["probability"]:
                assert c["probability"]["method"] in {"exact", "monte_carlo"}


def test_no_discard_when_exhausted(state_data):
    state_data["discards_left"] = 0
    assert all(c["action"] == "play" for c in candidates(parse_state(json.dumps(state_data)), 500)["candidates"])


def test_missing_levels_and_unknown_jokers_do_not_invent_score(state_data):
    s = parse_state(json.dumps(state_data))
    assert plain_score(s, [3, 4], "pair") is None
    s.poker_hands = {"Pair": {"chips": 10, "mult": 2}}
    for c in [*s.hand, *s.deck_remaining]:
        c.__pydantic_extra__.update(permanent_bonus=0, base_bonus=0)
    # 6+6 playing chips; base pair 10 chips, x2 => 44.
    assert plain_score(s, [3, 4], "pair") == 44
    s.jokers = [{"key": "j_joker"}]
    assert plain_score(s, [3, 4], "pair") is None


def test_boss_and_modifiers_disable_score(state_data):
    s = parse_state(json.dumps(state_data))
    s.poker_hands = {"Pair": {"chips": 10, "mult": 2}}
    for c in [*s.hand, *s.deck_remaining]:
        c.__pydantic_extra__.update(permanent_bonus=0, base_bonus=0)
    s.blind.boss = True
    assert plain_score(s, [3, 4], "pair") is None
    s.blind.disabled = True
    s.hand[0].enhancement = "m_steel"
    assert plain_score(s, [3, 4], "pair") is None


def test_rule_changing_joker_disables_structure_probability(state_data):
    state_data["jokers"] = [{"key": "j_four_fingers"}]
    state_data["counts"]["jokers"] = 1
    calc = candidates(parse_state(json.dumps(state_data)), 500)
    for c in calc["candidates"]:
        if c["action"] == "discard":
            assert c["probability"] is None
            assert c["draw_events"]["rank_hits"]


def test_psychic_requires_five_cards(state_data):
    state_data["blind"]["key"] = "bl_psychic"
    state_data["blind"]["boss"] = True
    for c in candidates(parse_state(json.dumps(state_data)), 500)["candidates"]:
        if c["action"] == "play":
            assert len(c["cards"]) == 5


def test_serpent_draws_three(state_data):
    state_data["blind"]["key"] = "bl_serpent"
    for c in candidates(parse_state(json.dumps(state_data)), 500)["candidates"]:
        if c["action"] == "discard":
            assert c["draws"] == 3


def test_mouth_restricts_hand_type(state_data):
    state_data["blind"].update(key="bl_mouth", only_hand="Pair")
    for c in candidates(parse_state(json.dumps(state_data)), 500)["candidates"]:
        if c["action"] == "play":
            assert c["hand_type"] == "pair"


def test_eye_does_not_recommend_repeated_hand(state_data):
    state_data["blind"].update(key="bl_eye", played_hand_types={"Pair": True})
    for c in candidates(parse_state(json.dumps(state_data)), 500)["candidates"]:
        if c["action"] == "play":
            assert c["hand_type"] != "pair"
