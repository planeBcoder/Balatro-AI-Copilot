import json
from pathlib import Path
import pytest

from core.calculation import candidates, classify
from core.scoring import score_hand
from core.decision_policy import local_decision
from core.presentation import document, probability_label
from core.state_schema import parse_state
from ai.schemas import validate_decision
from core.game_hud import GameHUD
from core.hud_install import upgrade_scoring_snapshot

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def finish_state():
    # Real exported hand and levels. The new rule metadata is a controlled
    # standard-deck test assumption, NOT falsely attributed to the old export.
    s = parse_state((ROOT / "tests/fixtures/observed_finish_state.json").read_bytes())
    s.__pydantic_extra__["scoring_rules"] = {"version": 2, "back_key": "b_red", "challenge": False, "other_mods": [], "modifiers": {}}
    return s


def test_real_hand_220_of_300_finish_without_api(finish_state, monkeypatch):
    calc = candidates(finish_state, 500)
    assert calc["shortfall"] == 80
    assert len(calc["candidates"]) == 1
    c = calc["candidates"][0]
    assert c["cards"] == ["KS", "KH", "7H", "7D"] and c["expected_score"] == 108
    assert c["score_certified"] and calc["immediate_finish_ids"] == [c["id"]]
    decision = local_decision(calc)
    assert decision.recommendations[0].action == "play"
    monkeypatch.setattr("main.StateRefresher.refresh", lambda _: finish_state)
    monkeypatch.setattr("main.DeepSeekClient.decide", lambda *_: pytest.fail("Already-winning hand must bypass API"))
    from main import Worker
    from config.settings import Settings
    results, errors = [], []
    worker = Worker("unit-test-key", Settings())
    worker.result.connect(results.append)
    worker.error.connect(errors.append)
    worker.run()
    assert len(results) == 1 and not errors


def test_finish_with_discards_left_never_wastes_them(finish_state):
    finish_state.discards_left = 3
    calc = candidates(finish_state)
    assert all(c["action"] == "play" for c in calc["candidates"])


def test_old_export_not_certified(finish_state):
    finish_state.__pydantic_extra__.pop("scoring_rules")
    calc = candidates(finish_state, 500)
    assert not calc["immediate_finish_ids"]
    assert calc["score_limitations"]
    assert not any(c.get("score_certified") for c in calc["candidates"])


@pytest.mark.parametrize("joker,expected", [
    ({"key":"j_joker", "ability":{"mult":4}}, 75),
    ({"key":"j_half", "ability":{"extra":{"size":3,"mult":20}}}, 315),
    ({"key":"j_mystic_summit", "ability":{"extra":{"d_remaining":0,"mult":15}}}, 240),
    ({"key":"j_banner", "ability":{"extra":30}}, 15),
    ({"key":"j_abstract", "ability":{"extra":3}}, 60),
    ({"key":"j_egg", "ability":{"extra":3}}, 15),
])
def test_supported_joker_scores(finish_state, joker, expected):
    finish_state.jokers = [joker]
    # KS as high card: (5+10)*1 before Joker effects.
    score = score_hand(finish_state, [0], "high_card")
    assert score.value == expected and score.certified


def test_joker_order_changes_score(finish_state):
    a = {"key":"j_joker", "ability":{"mult":4}}
    b = {"key":"j_acrobat", "ability":{"extra":3}}
    finish_state.hands_left = 1
    finish_state.jokers = [a,b]
    assert score_hand(finish_state, [0], "high_card").value == 225
    finish_state.jokers = [b,a]
    assert score_hand(finish_state, [0], "high_card").value == 105


def test_unplayed_deck_modifiers_do_not_disable_current_score(finish_state):
    finish_state.deck_remaining[0].enhancement = "m_lucky"
    finish_state.deck_remaining[0].__pydantic_extra__.pop("base_bonus")
    assert score_hand(finish_state, [0], "high_card").value == 15


def test_passive_boss_and_exported_debuffs(finish_state):
    finish_state.blind.boss = True
    finish_state.blind.key = "bl_plant"
    finish_state.hand[0].debuffed = True
    assert score_hand(finish_state, [0], "high_card").value == 5
    finish_state.blind.key = "bl_wall"
    finish_state.hand[0].debuffed = False
    assert score_hand(finish_state, [0], "high_card").value == 15
    calc = candidates(finish_state)
    assert calc["immediate_finish_ids"]


def test_observatory_not_ignored(finish_state):
    finish_state.__pydantic_extra__.update(vouchers={"v_observatory":True}, consumables=[{"key":"c_pluto"}])
    assert score_hand(finish_state, [0], "high_card").value is None


@pytest.mark.parametrize("mutation", ["unknown_joker", "lucky_card", "boss", "other_mod", "challenge", "unknown_modifier", "edition_consumable", "missing_back"])
def test_unsupported_effects_never_certify_finish(finish_state, mutation):
    if mutation == "unknown_joker": finish_state.jokers = [{"key":"j_blueprint", "ability":{}}]
    if mutation == "lucky_card": finish_state.hand[0].enhancement = "m_lucky"
    if mutation == "boss": finish_state.blind.boss = True
    if mutation == "other_mod": finish_state.scoring_rules["other_mods"] = ["custom_scoring"]
    if mutation == "challenge": finish_state.scoring_rules["challenge"] = True
    if mutation == "unknown_modifier": finish_state.scoring_rules["modifiers"] = {"new_scoring_effect":True}
    if mutation == "edition_consumable": finish_state.__pydantic_extra__["consumables"] = [{"edition":"foil"}]
    if mutation == "missing_back": finish_state.scoring_rules.pop("back_key")
    assert score_hand(finish_state, [0,1,2,6], "two_pair").value is None
    assert not candidates(finish_state, 500)["immediate_finish_ids"]


def test_higher_level_high_card_not_excluded_by_category(finish_state):
    finish_state.poker_hands["High Card"].update(chips=300, mult=10)
    calc = candidates(finish_state)
    c = calc["candidates"][0]
    assert c["hand_type"] == "high_card" and len(c["cards"]) == 1


def test_partial_hand_retains_steel_and_red_seal(finish_state):
    finish_state.hand[2].enhancement = "m_steel"
    finish_state.hand[2].seal = "Red"
    assert score_hand(finish_state, [0], "high_card").value == 33  # floor(15*2.25)
    finish_state.hand[0].seal = "Red"
    assert score_hand(finish_state, [0], "high_card").value == 56  # floor(25*2.25)


def test_holographic_exporter_spelling(finish_state):
    finish_state.hand[0].edition = "holographic"
    assert score_hand(finish_state, [0], "high_card").value == 165


def test_plasma_not_mistaken_for_standard_deck(finish_state):
    finish_state.scoring_rules["back_key"] = "b_plasma"
    assert score_hand(finish_state, [0], "high_card").value == 64
    assert score_hand(finish_state, [5], "high_card").value == 49  # 13 chips +1 mult
    finish_state.hand[5].__pydantic_extra__["permanent_bonus"] = 1
    assert score_hand(finish_state, [5], "high_card").value == 49  # floor(15/2)^2


def test_two_simple_hands_without_hoped_for_draws(finish_state):
    finish_state.score = 0
    finish_state.blind.target = 120
    calc = candidates(finish_state)
    plan = calc["two_hand_plan"]
    assert plan["certified"] and plan["total_score"] >= 120
    decision = local_decision(calc)
    assert decision and decision.recommendations[0].candidate_id == plan["first_id"]


def test_single_discard_compares_score_not_category(finish_state):
    finish_state.score = 0
    finish_state.blind.target = 300
    finish_state.discards_left = 2
    calc = candidates(finish_state, 500)
    ds = [c for c in calc["candidates"] if c["action"] == "discard"]
    assert ds and all("next_play" in c for c in ds)
    assert any(set(c["keep_indices"]) >= {0,1,2,6} for c in ds)
    assert all(c["win_probability"] is None for c in ds)
    for c in ds:
        assert c["next_play"]["sample_count"] in {0,96}
        assert "straight_flush" not in c["all_hand_events"]


def test_no_tiny_straight_flush_in_presentation():
    assert probability_label({"probability":{"event":"straight_flush", "value":.001, "method":"exact"}}) == ""


def test_styled_document_has_clear_blocks_not_model_markup(finish_state):
    calc = candidates(finish_state)
    decision = local_decision(calc)
    decision.summary = "one\nA\tinjected"
    blocks, plain, hud = document(decision, calc, finish_state, .15)
    assert "暂无法精确计算" not in plain
    assert "本手 108 分 / 还差 80" in plain
    assert hud.startswith("BACP_BLOCKS_V1\nM\t")
    assert "\nA\tinjected" not in hud
    assert [tag for tag,_ in blocks] == ["M","A","P","R","S","F"]


def test_provider_cannot_overrule_finish(finish_state):
    calc = candidates(finish_state)
    decision = local_decision(calc)
    calc["candidates"].append({"id":"d99", "action":"discard", "cards":["KS"]})
    raw = decision.model_dump()
    raw["recommendations"][0].update(candidate_id="d99", action="discard", cards=["KS"])
    with pytest.raises(ValueError): validate_decision(json.dumps(raw), calc)


def test_hud_transient_file_lock_recovers(tmp_path, monkeypatch):
    h = GameHUD(tmp_path)
    h.enabled = True
    import core.game_hud as module
    replace = module.os.replace
    tries = []
    def locked(a,b):
        tries.append(True)
        if len(tries) < 3: raise PermissionError("Reader holding file")
        return replace(a,b)
    monkeypatch.setattr(module.os, "replace", locked)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    h.message("idle", "ready")
    assert len(tries) == 3 and h.enabled


def test_old_and_new_renderer_capability_handshake(tmp_path):
    h = GameHUD(tmp_path, lambda: 100)
    ready = tmp_path / "balatro_copilot_hud_ready.txt"
    ready.write_text("BACP_READY_V1|100|10|0|1|ok")
    assert h.ready() and not h.supports_blocks
    ready.write_text("BACP_READY_V1|100|11|0|1|ok|BACP_BLOCKS_V1")
    assert h.ready() and h.supports_blocks
    ready.write_text("BACP_READY_V1|100|11|0|1|ok|execute")
    assert h.ready() is None


def test_snapshot_install_preserves_original_and_is_idempotent(tmp_path):
    path = tmp_path / "Mods/BalatroStateExporter/main.lua"
    path.parent.mkdir(parents=True)
    src = '-- BALATRO_COPILOT_BRIDGE_V1\n        state.request_id = token\n'
    path.write_text(src)
    assert upgrade_scoring_snapshot(tmp_path, ROOT / "assets") == "installed"
    assert path.with_name("main.lua.pre-strategy-v2.bak").read_text() == src
    assert upgrade_scoring_snapshot(tmp_path, ROOT / "assets") == "ready"
    assert "evaluate_play(" not in path.read_text()
