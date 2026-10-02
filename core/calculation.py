from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Any

from core.probability import RANKS, NAMES, hand_events, after_draw, draw_rank, draw_suit
from core.state_schema import State
from core.scoring import score_hand, number

CATEGORY = ["flush_five", "flush_house", "five_kind", "straight_flush", "four_kind", "full_house", "flush", "straight", "three_kind", "two_pair", "pair"]
GAME_NAMES = {"flush_five": "Flush Five", "flush_house": "Flush House", "five_kind": "Five of a Kind", "straight_flush": "Straight Flush", "four_kind": "Four of a Kind", "full_house": "Full House", "flush": "Flush", "straight": "Straight", "three_kind": "Three of a Kind", "two_pair": "Two Pair", "pair": "Pair", "high_card": "High Card"}
SPECIAL_RULES = {"j_four_fingers", "j_shortcut", "j_smeared"}
STRAIGHT_FLUSH_MIN = 0.15  # Relevance filter, NOT a universal optimality claim.


def nominal(c: str) -> int:
    return min(RANKS.index(c[0]) + 2, 10) if c[0] != "A" else 11


def classify(codes: list[str]) -> str:
    ev = hand_events(codes)
    if len(codes) == 5 and len(set(c[0] for c in codes)) == 1:
        return "flush_five" if "flush" in ev else "five_kind"
    if "full_house" in ev and "flush" in ev:
        return "flush_house"
    return next((c for c in CATEGORY if c in ev), "high_card")


def scoring_codes(codes: list[str], category: str) -> list[str]:
    counts = Counter(c[0] for c in codes)
    if category == "high_card":
        return [max(codes, key=lambda c: RANKS.index(c[0]))]
    if category in {"pair", "three_kind", "four_kind"}:
        needed = {"pair": 2, "three_kind": 3, "four_kind": 4}[category]
        rank = max((r for r, n in counts.items() if n >= needed), key=RANKS.index)
        return [c for c in codes if c[0] == rank]
    if category == "two_pair":
        return [c for c in codes if counts[c[0]] >= 2]
    return codes


def plain_score(state: State, indices: list[int], category: str) -> float | None:
    """Exact immediate score only inside a deliberately narrow tested boundary."""
    if state.jokers or not state.poker_hands or category not in GAME_NAMES:
        return None
    if any("permanent_bonus" not in (c.model_extra or {}) or "base_bonus" not in (c.model_extra or {}) for c in [*state.hand, *state.deck_remaining]):
        return None
    if state.blind.boss and not state.blind.disabled:
        return None
    selected = [state.hand[i] for i in indices]
    if any(c.enhancement or c.edition or c.seal or c.debuffed for c in state.hand):
        return None
    if any(c.enhancement or c.edition or c.seal for c in state.deck_remaining):
        return None
    if any((c.model_extra or {}).get("permanent_bonus", 0) or (c.model_extra or {}).get("base_bonus", 0) for c in [*state.hand, *state.deck_remaining]):
        return None
    if any(c.get("key") in {"c_devil", "c_chariot"} for c in (state.model_extra or {}).get("consumables", [])):
        return None
    level = state.poker_hands.get(GAME_NAMES[category])
    if not isinstance(level, dict) or "chips" not in level or "mult" not in level:
        return None
    codes = [c.code for c in selected]
    scoring = scoring_codes(codes, category)
    try:
        return (float(level["chips"]) + sum(nominal(c) for c in scoring)) * float(level["mult"])
    except (ValueError, TypeError):
        return None


def candidates(state: State, sample_count: int = 2000) -> dict[str, Any]:
    hand = [c.code for c in state.hand]
    deck = [c.code for c in state.deck_remaining]
    special = any(j.get("key") in SPECIAL_RULES for j in state.jokers) or any(c.enhancement in {"m_wild", "m_stone"} for c in [*state.hand, *state.deck_remaining])
    options: list[dict] = []
    blind_active = not state.blind.disabled
    blind_key = state.blind.key if blind_active else None
    blind_extra = state.blind.model_extra or {}
    try:
        shortfall = max(0, number(state.blind.target) - number(state.score))
    except (ValueError, TypeError):
        shortfall = None
    result = {"candidates": options, "win_probability": None, "shortfall": shortfall,
              "immediate_finish_ids": [], "score_limitations": [], "assumptions": [
                  "Single-discard probabilities are not Blind win probabilities.",
                  "Remaining deck is an unordered physical-card multiset.",
                  "Certified immediate finishes take priority over speculative draws.",
                  "Straight flush is omitted unless relevant and single-draw probability >= 15%."]}
    plays = []
    if state.hands_left:
        for n in range(1, min(5, len(hand)) + 1):
            for choice in combinations(range(len(hand)), n):
                cards = [hand[i] for i in choice]
                category = classify(cards)
                if blind_key == "bl_psychic" and n < 5:
                    continue
                if blind_key == "bl_mouth" and blind_extra.get("only_hand") and blind_extra["only_hand"] != GAME_NAMES.get(category):
                    continue
                if blind_key == "bl_eye" and (blind_extra.get("played_hand_types") or {}).get(GAME_NAMES.get(category)):
                    continue
                estimate = score_hand(state, list(choice), category)
                exact_score = estimate.value
                if estimate.reason and estimate.reason not in result["score_limitations"]:
                    result["score_limitations"].append(estimate.reason)
                strength = len(CATEGORY) - CATEGORY.index(category) if category in CATEGORY else 0
                plays.append((exact_score if exact_score is not None else strength * 100 + sum(nominal(c) for c in scoring_codes(cards, category)), list(choice), category, exact_score, estimate.certified))
        plays.sort(key=lambda x: (x[3] is None, -x[0], len(x[1]), x[1]))
        finish = [p for p in plays if p[4] and p[3] is not None and shortfall is not None and shortfall > 0 and p[3] >= shortfall]
        if finish:
            # End this Blind now, save discards and unused hands. Do not ask the
            # model whether to gamble after an already verified winning move.
            chosen = min(finish, key=lambda p: (len(p[1]), -p[3], p[1]))
            plays = [chosen]
        seen = Counter()
        for _, indices, category, score, certified in plays:
            signature = (category, score, len(indices))
            if signature in seen or seen[category] >= 2:
                continue
            seen[signature] += 1
            seen[category] += 1
            options.append({"id": f"p{len(options)}", "action": "play", "indices": indices, "cards": [hand[i] for i in indices], "keep_indices": [i for i in range(len(hand)) if i not in indices], "hand_type": category, "expected_score": score, "score_certified": certified, "win_probability": None, "probability": None, "probability_method": "unavailable"})
            if len(options) >= 10:
                break
        if finish:
            result["immediate_finish_ids"] = [options[0]["id"]]
            return result
    # Preserve the simple best ready-made hand as a discard option; the old
    # generator only offered suit/run/rank chases, omitting this basic strategy.
    if state.discards_left and deck:
        keeps: set[tuple[int, ...]] = set()
        if plays:
            keeps.add(tuple(plays[0][1]))
        for suit in "SHCD":
            keep = tuple(i for i, c in enumerate(hand) if c[1] == suit)
            if len(keep) >= 3:
                keeps.add(keep)
        for start in range(1, 11):
            run = {14 if r == 1 else r for r in range(start, start + 5)}
            indices = []
            ranks_seen = set()
            for i, c in enumerate(hand):
                r = RANKS.index(c[0]) + 2
                if r in run and r not in ranks_seen:
                    indices.append(i)
                    ranks_seen.add(r)
            if len(indices) >= 3:
                keeps.add(tuple(indices))
        counts = Counter(c[0] for c in hand)
        for rank, count in counts.items():
            if count >= 2:
                keeps.add(tuple(i for i, c in enumerate(hand) if c[0] == rank))
        pair_keep = tuple(i for i, c in enumerate(hand) if counts[c[0]] >= 2)
        if pair_keep:
            keeps.add(pair_keep)
        if not keeps:
            keeps.add(tuple(sorted(range(len(hand)), key=lambda i: -RANKS.index(hand[i][0]))[:max(1, len(hand) - 5)]))
        for keep in sorted(keeps, key=lambda k: (-len(k), k))[:8]:
            discards = [i for i in range(len(hand)) if i not in keep][:5]
            if not discards:
                continue
            keep = tuple(i for i in range(len(hand)) if i not in discards)
            draws = min(len(discards), len(deck))
            if blind_key == "bl_serpent":
                draws = min(3, len(deck))
            probs = after_draw([hand[i] for i in keep], deck, draws, sample_count=sample_count)
            existing = hand_events([hand[i] for i in keep])
            # Choose a target that has a genuine chance of improvement; explain
            # the event explicitly, never relabel it as a Blind win probability.
            sf_relevant = max((len(set(hand[i][0] for i in keep if hand[i][1] == s)) for s in "SHCD"), default=0) >= 4 and probs["straight_flush"].value >= STRAIGHT_FLUSH_MIN
            # Use actual exported hand levels, not poker category prestige.
            def utility(event, p):
                level = (state.poker_hands or {}).get(GAME_NAMES[event], {})
                try:
                    reward = number(level["chips"]) * number(level["mult"])
                except (KeyError, ValueError, TypeError):
                    reward = 1  # unknown levels: prefer likelihood, not size
                return p.value * reward
            choices = [(utility(event, p), event) for event, p in probs.items() if event not in existing and event in CATEGORY and p.value > 0 and (event != "straight_flush" or sf_relevant)]
            event = max(choices)[1] if choices else "pair"
            prob = None if special or not choices else probs[event].payload()
            evidence = {
                "rank_hits": {r: draw_rank(deck, r, draws).payload() for r in sorted(set(hand[i][0] for i in keep))},
                "suit_hits": {s: draw_suit(deck, s, draws).payload() for s in sorted(set(hand[i][1] for i in keep))},
            }
            options.append({"id": f"d{len(options)}", "action": "discard", "indices": discards, "cards": [hand[i] for i in discards], "keep_indices": list(keep), "draws": draws, "target": event, "probability": prob, "probability_method": prob["method"] if prob else "unavailable", "all_hand_events": {} if special else {e: p.payload() for e, p in probs.items() if e != "straight_flush" or sf_relevant}, "draw_events": evidence, "expected_score": None, "win_probability": None})
    if special:
        result["assumptions"].append("Hand structure probabilities disabled for Wild/Stone or rule-changing Jokers.")
    from core.lookahead import annotate_plans
    annotate_plans(state, result, plays)
    return result
