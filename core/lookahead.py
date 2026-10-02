"""Bounded, one-discard plain-card lookahead and held-card two-hand baseline.

Not a complete run/Blind solver. Uses its own seeded RNG, no hidden draw order.
Unknown effects disable this planner instead of silently treating them as zero.
"""
from collections import Counter
from itertools import combinations
from math import comb, sqrt, floor
import random

from core.state_schema import State


def annotate_plans(state: State, result: dict, plays: list):
    from core.calculation import classify, scoring_codes, nominal, GAME_NAMES
    plain = (not state.jokers and (not state.blind.boss or state.blind.disabled) and all(not c.enhancement and not c.edition and not c.seal and not c.debuffed
             and (c.model_extra or {}).get("permanent_bonus") == 0 and (c.model_extra or {}).get("base_bonus") == 0
             for c in [*state.hand, *state.deck_remaining]))
    if not plain or not plays or not all(p[4] and p[3] is not None for p in plays):
        result["lookahead_status"] = "unsupported_effects"
        return
    rules = (state.model_extra or {}).get("scoring_rules") or {}
    plasma = rules.get("back_key") == "b_plasma"
    levels = state.poker_hands or {}
    shortfall = result["shortfall"]
    if shortfall is None or shortfall <= 0:
        return
    best_now = max(p[3] for p in plays)
    result["best_current_score"] = best_now
    # Certify only plain-card two-hand sequences from cards already held. No
    # hoped-for draws, duplicated cards or already-discarded cards in the proof.
    if (state.hands_left or 0) >= 2:
        pairs = []
        for first in plays:
            used = set(first[1])
            for second in plays:
                if used.isdisjoint(second[1]) and first[3] + second[3] >= shortfall:
                    pairs.append((len(first[1])+len(second[1]), -first[3], first, second))
        if pairs:
            _, _, first, second = min(pairs, key=lambda p: (p[0], p[1], p[2][1], p[3][1]))
            candidate = next((c for c in result["candidates"] if c["action"] == "play" and c["indices"] == first[1]), None)
            if candidate:
                result["two_hand_plan"] = {"first_id": candidate["id"], "first_score": first[3],
                    "second_cards": [state.hand[i].code for i in second[1]], "second_score": second[3],
                    "total_score": first[3]+second[3], "method": "held_cards_no_draws", "certified": True}
    cache = {}

    def best_score(codes):
        signature = tuple(sorted(codes))
        if signature in cache:
            return cache[signature]
        # Plain cards cannot benefit from non-scoring kickers. Enumerate all
        # five-card structures plus minimal rank sets, two pairs and singles.
        subsets = list(combinations(range(len(codes)), 5))
        groups = {r: [i for i, c in enumerate(codes) if c[0] == r] for r in Counter(c[0] for c in codes)}
        subsets.extend((i,) for i in range(len(codes)))
        for indices in groups.values():
            for n in range(2, min(4, len(indices))+1):
                subsets.append(tuple(indices[:n]))
        paired = [g for g in groups.values() if len(g) >= 2]
        for a, b in combinations(paired, 2):
            subsets.append(tuple(a[:2]+b[:2]))
        value = 0
        for indices in subsets:
            cards = [codes[i] for i in indices]
            kind = classify(cards)
            level = levels[GAME_NAMES[kind]]
            chips = float(level["chips"]) + sum(nominal(c) for c in scoring_codes(cards, kind))
            mult = float(level["mult"])
            score = floor((floor((chips+mult)/2))**2 if plasma else chips*mult)
            value = max(value, score)
        cache[signature] = value
        return value

    # Only annotate when every standard level exists; partial snapshots should
    # never cause a guessed probability or crash the entire analysis.
    if any(not isinstance(levels.get(name), dict) or not {"chips", "mult"} <= levels[name].keys() for name in GAME_NAMES.values()):
        result["lookahead_status"] = "missing_levels"
        return
    deck = sorted(c.code for c in state.deck_remaining)
    for candidate in result["candidates"]:
        if candidate["action"] != "discard":
            continue
        keep = [state.hand[i].code for i in candidate["keep_indices"]]
        draws = candidate["draws"]
        total = comb(len(deck), draws)
        n = min(96, total)
        if not n:
            continue
        rng = random.Random(67293)  # Independent of game and deterministic tests.
        exact = total <= 96
        outcomes = combinations(deck, draws) if exact else (rng.sample(deck, draws) for _ in range(n))
        scores = [best_score(keep+list(drawn)) for drawn in outcomes]
        p = sum(score >= shortfall for score in scores)/n
        mean = sum(scores)/n
        candidate["next_play"] = {"method": "exact" if exact else "monte_carlo", "sample_count": 0 if exact else n,
            "expected_best_score": round(mean, 2), "gain_over_ready_hand": round(mean-best_now, 2),
            "finish_next_play_probability": p, "standard_error": 0 if exact else sqrt(p*(1-p)/n)}
    result["lookahead_status"] = "plain_cards_single_discard"
