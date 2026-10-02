"""Pure, fail-closed immediate scoring. Never invoke game callbacks or RNG.

Supports an explicit subset of vanilla effects. Unsupported effects return a
reason, not zero, an invented win rate, or a guarantee based on partial maths.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor, isfinite
from core.state_schema import State

BACKS = {"b_red", "b_blue", "b_yellow", "b_green", "b_black", "b_magic", "b_nebula", "b_ghost", "b_abandoned", "b_checkered", "b_zodiac", "b_painted", "b_anaglyph", "b_plasma", "b_erratic"}
TYPED = {"j_jolly", "j_zany", "j_mad", "j_crazy", "j_droll", "j_sly", "j_wily", "j_clever", "j_devious", "j_crafty", "j_duo", "j_trio", "j_family", "j_order", "j_tribe"}
SUPPORTED = TYPED | {"j_joker", "j_half", "j_abstract", "j_acrobat", "j_mystic_summit", "j_banner", "j_stuntman", "j_supernova", "j_blue_joker", "j_blackboard", "j_egg", "j_credit_card", "j_green_joker", "j_ride_the_bus", "j_trousers", "j_popcorn", "j_ice_cream", "j_ramen", "j_gros_michel", "j_cavendish", "j_flash"}
ENHANCEMENTS = {None, "", "m_bonus", "m_mult", "m_glass", "m_steel", "m_gold"}
EDITIONS = {None, "", "foil", "holo", "holographic", "polychrome", "negative", "e_foil", "e_holo", "e_polychrome", "e_negative"}
SUIT_JOKERS = {"j_greedy_joker", "j_lusty_joker", "j_wrathful_joker", "j_gluttenous_joker"}
SUPPORTED |= SUIT_JOKERS | {"j_smiley", "j_scary_face", "j_fibonacci", "j_even_steven", "j_odd_todd", "j_scholar", "j_photograph", "j_hanging_chad", "j_hack", "j_sock_and_buskin", "j_splash", "j_constellation", "j_runner", "j_onyx_agate", "j_arrowhead"}
# These vanilla Bosses only change target/resources, card/Joker debuff flags,
# visibility or money. Those are exported; no supported Joker scores on money.
SNAPSHOT_BOSSES = {"bl_club", "bl_goad", "bl_window", "bl_head", "bl_plant", "bl_wall", "bl_water", "bl_manacle", "bl_needle", "bl_house", "bl_mark", "bl_final_leaf", "bl_final_heart", "bl_ox", "bl_tooth", "bl_pillar", "bl_psychic", "bl_eye", "bl_mouth", "bl_serpent", "bl_flint", "bl_final_vessel", "bl_fish", "bl_wheel"}
SNAPSHOT_BOSSES |= {'bl_arm','bl_hook'}
LEVEL_INCREMENTS={'High Card':(10,1),'Pair':(15,1),'Two Pair':(20,1),'Three of a Kind':(20,2),'Straight':(30,3),'Flush':(15,2),'Full House':(25,2),'Four of a Kind':(30,3),'Straight Flush':(40,4),'Five of a Kind':(35,3),'Flush House':(40,4),'Flush Five':(50,3)}


@dataclass(frozen=True)
class Score:
    value: int | None
    reason: str = ""
    certified: bool = False


def number(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a score")
    n = float(value)
    if not isfinite(n) or abs(n) > 2**53:
        raise ValueError("Score outside safe numeric boundary")
    return n


def score_hand(state: State, indices: list[int], category: str) -> Score:
    from core.calculation import GAME_NAMES, scoring_codes, nominal
    from core.probability import hand_events
    extra = state.model_extra or {}
    snapshot = extra.get("scoring_rules")
    # Older exporters did not include back/modifier metadata: show a conditional
    # estimate but never use it to certify a finish or bypass model analysis.
    certified = isinstance(snapshot, dict) and snapshot.get("version") == 2
    if certified:
        back = snapshot.get("back_key")
        if back not in BACKS or snapshot.get("challenge") or snapshot.get("other_mods"):
            return Score(None, "特殊牌组、挑战或额外模组尚未支持")
        modifiers = snapshot.get("modifiers") or {}
        non_scoring = {"no_extra_hand_money", "no_interest", "money_per_hand", "money_per_discard", "no_blind_reward", "booster_ante_scaling", "enable_eternals_in_shop", "enable_perishables_in_shop", "enable_rentals_in_shop", "inflation"}
        if not isinstance(modifiers, dict) or any(v and k not in non_scoring for k, v in modifiers.items()):
            return Score(None, "特殊规则尚未支持")
    else:
        back = None
    if any(j.get('key') in {'j_ramen','j_constellation'} and not j.get('debuffed') for j in state.jokers) and (not snapshot or snapshot.get('numeric_precision')!='binary64'):
        certified=False  # Old Lua tostring rounds mutable fractional factors.
    if state.blind.boss and not state.blind.disabled and state.blind.key not in SNAPSHOT_BOSSES:
        return Score(None, "Boss 计分限制尚未纳入验算")
    if any(c.get("edition") for c in extra.get("consumables", [])):
        return Score(None, "消耗牌版本的计分效果尚未支持")
    vouchers = extra.get("vouchers") or {}
    if isinstance(vouchers, dict) and vouchers.get("v_observatory") and extra.get("consumables"):
        return Score(None, "天文台与保留行星的效果尚未支持")
    if not state.poker_hands or category not in GAME_NAMES:
        return Score(None, "缺少牌型计分数据")
    if any(j.get("key") not in SUPPORTED and not j.get("debuffed", False) for j in state.jokers):
        return Score(None, "有尚未支持的小丑效果")
    if state.blind.boss and not state.blind.disabled and state.blind.key=='bl_hook':
        if any(j.get('key') in {'j_blackboard','j_green_joker','j_ramen'} and not j.get('debuffed') for j in state.jokers) or any(c.enhancement=='m_steel' for c in state.hand):
            return Score(None,'The Hook 随机弃牌会影响持牌或小丑效果')
    if any(c.enhancement not in ENHANCEMENTS or c.edition not in EDITIONS or c.seal not in {None, "", "Red", "Blue", "Gold", "Purple"} for c in state.hand):
        return Score(None, "有随机或尚未支持的手牌效果")
    if any("permanent_bonus" not in (c.model_extra or {}) or "base_bonus" not in (c.model_extra or {}) for c in state.hand):
        return Score(None, "缺少手牌附加筹码")
    try:
        selected = [state.hand[i] for i in indices]
        codes = [c.code for c in selected]
        scoring = scoring_codes(codes, category)
        if any(j.get('key')=='j_splash' and not j.get('debuffed') for j in state.jokers): scoring=codes
        first_scoring=next((c for c in selected if c.code in scoring),None)
        first_face=next((c for c in selected if c.code in scoring and c.code[0] in 'JQK'),None)
        level = state.poker_hands[GAME_NAMES[category]]
        chips, mult = number(level["chips"]), number(level["mult"])
        if state.blind.boss and not state.blind.disabled and state.blind.key=='bl_arm' and number(level['level'])>1:
            dc,dm=LEVEL_INCREMENTS[GAME_NAMES[category]]
            chips,mult=max(0,chips-dc),max(1,mult-dm)
        if state.blind.boss and not state.blind.disabled and state.blind.key == "bl_flint":
            chips, mult = max(floor(chips * .5 + .5), 0), max(floor(mult * .5 + .5), 1)
        # Match scoring membership by rank for partial hands, not set(code),
        # so legitimate duplicate physical cards each score once.
        for card in selected:
            if card.code not in scoring or card.debuffed:
                continue
            repetitions=2 if card.seal=='Red' else 1
            for joker in state.jokers:
                if joker.get('debuffed'):continue
                key,ability=joker['key'],joker.get('ability') or {}
                if (key=='j_hanging_chad' and card is first_scoring) or (key=='j_hack' and card.code[0] in '2345') or (key=='j_sock_and_buskin' and card.code[0] in 'JQK'):
                    repetitions+=int(number(ability['extra']))
            for _ in range(repetitions):
                fields = card.model_extra or {}
                chips += nominal(card.code) + number(fields["base_bonus"]) + number(fields["permanent_bonus"])
                if card.enhancement == "m_mult":
                    mult += 4
                if card.enhancement == "m_glass":
                    mult *= 2
                edition = (card.edition or "").removeprefix("e_")
                if edition == "foil":
                    chips += 50
                elif edition in {"holo", "holographic"}:
                    mult += 10
                elif edition == "polychrome":
                    mult *= 1.5
                for joker in state.jokers:
                    if joker.get('debuffed'):continue
                    key,ability=joker['key'],joker.get('ability') or {}
                    x=ability.get('extra')
                    suit={'S':'Spades','H':'Hearts','C':'Clubs','D':'Diamonds'}[card.code[1]]
                    if key in SUIT_JOKERS and suit==x['suit']:mult+=number(x['s_mult'])
                    elif key=='j_smiley' and card.code[0] in 'JQK':mult+=number(x)
                    elif key=='j_scary_face' and card.code[0] in 'JQK':chips+=number(x)
                    elif key=='j_fibonacci' and card.code[0] in 'A2358':mult+=number(x)
                    elif key=='j_even_steven' and card.code[0] in '2468T':mult+=number(x)
                    elif key=='j_odd_todd' and card.code[0] in 'A3579':chips+=number(x)
                    elif key=='j_scholar' and card.code[0]=='A':chips+=number(x['chips']);mult+=number(x['mult'])
                    elif key=='j_photograph' and card is first_face:mult*=number(x)
                    elif key=='j_onyx_agate' and card.code[1]=='C':mult+=number(x)
                    elif key=='j_arrowhead' and card.code[1]=='S':chips+=number(x)
        held = [c for i, c in enumerate(state.hand) if i not in indices]
        for c in held:
            if c.enhancement == "m_steel" and not c.debuffed:
                mult *= 1.5 ** (2 if c.seal == "Red" else 1)
        events = {GAME_NAMES[e] for e in hand_events(codes)} | {GAME_NAMES[category]}
        for joker in state.jokers:
            if joker.get("debuffed", False):
                continue
            key, ability = joker["key"], joker.get("ability")
            if not isinstance(ability, dict):
                return Score(None, "缺少小丑效果参数")
            edition = (joker.get("edition") or "").removeprefix("e_")
            if edition not in {"", "foil", "holo", "holographic", "polychrome", "negative"}:
                return Score(None, "小丑版本效果尚未支持")
            if edition == "foil":
                chips += 50
            elif edition in {"holo", "holographic"}:
                mult += 10
            x = ability.get("extra")
            if key in TYPED:
                if ability.get("type") in events:
                    # Vanilla main-effect branches are mutually exclusive.
                    if number(ability.get("x_mult", 1)) > 1:
                        mult *= number(ability["x_mult"])
                    elif number(ability.get("t_mult", 0)) > 0:
                        mult += number(ability["t_mult"])
                    elif number(ability.get("t_chips", 0)) > 0:
                        chips += number(ability["t_chips"])
            elif key == "j_joker":
                mult += number(ability["mult"])
            elif key == "j_half" and len(indices) <= number(x["size"]):
                mult += number(x["mult"])
            elif key == "j_abstract":
                mult += len(state.jokers) * number(x)
            elif key == "j_acrobat" and state.hands_left == 1:
                mult *= number(x)
            elif key == "j_mystic_summit" and state.discards_left == number(x["d_remaining"]):
                mult += number(x["mult"])
            elif key == "j_banner":
                chips += number(state.discards_left) * number(x)
            elif key == "j_stuntman":
                chips += number(x["chip_mod"])
            elif key == "j_supernova":
                mult += number(level["played"]) + 1
            elif key == "j_blue_joker":
                chips += len(state.deck_remaining) * number(x)
            elif key == "j_blackboard" and all(c.code[1] in "SC" for c in held):
                mult *= number(x)
            elif key == "j_green_joker":
                mult += number(ability["mult"]) + number(x["hand_add"])
            elif key == "j_ride_the_bus":
                mult += 0 if any(c[0] in "JQK" for c in scoring) else number(ability["mult"]) + number(x)
            elif key == "j_trousers":
                mult += number(ability["mult"]) + (number(x) if "Two Pair" in events or category == "full_house" else 0)
            elif key in {"j_popcorn", "j_flash"}:
                mult += number(ability["mult"])
            elif key == "j_ice_cream":
                chips += number(x["chips"])
            elif key == "j_ramen":
                mult *= number(ability["x_mult"])
            elif key == "j_gros_michel":
                mult += number(x["mult"])
            elif key == "j_cavendish":
                mult *= number(x["Xmult"])
            elif key == 'j_constellation':
                mult *= number(ability['x_mult'])
            elif key == 'j_runner':
                chips += number(x['chips']) + (number(x['chip_mod']) if 'Straight' in events else 0)
            if edition == "polychrome":
                mult *= 1.5
        value = floor((floor((chips + mult) / 2))**2 if back == "b_plasma" else chips * mult)
        number(value)
        return Score(value, "" if certified else "旧导出缺少牌组规则，分数仅为条件估算", certified)
    except (KeyError, ValueError, TypeError, IndexError, OverflowError):
        return Score(None, "效果参数不完整或数值超出验算范围")
