"""Finite-population, without-replacement probabilities. Never uses deck order.

Hand events mean 'there exists a playable subset with this structure', not
exclusive highest-hand classification and not probability of beating a Blind.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from itertools import combinations
from math import comb, sqrt
import random
from typing import Sequence

RANKS = "23456789TJQKA"
EVENTS = ("pair", "two_pair", "three_kind", "four_kind", "straight", "flush", "full_house", "straight_flush")
NAMES = {"pair": "对子", "two_pair": "两对", "three_kind": "三条", "four_kind": "四条", "straight": "顺子", "flush": "同花", "full_house": "葫芦", "straight_flush": "同花顺"}
STRAIGHTS = [sum(1 << i for i in range(start, start + 5)) for start in range(9)] + [(1 << 12) | 15]


@dataclass(frozen=True)
class Probability:
    value: float
    method: str
    sample_count: int
    event: str
    standard_error: float = 0.0

    def payload(self) -> dict:
        return asdict(self)


def at_least_one(population: int, targets: int, draws: int) -> float:
    if not 0 <= targets <= population or not 0 <= draws <= population:
        raise ValueError("Invalid hypergeometric parameters")
    if not draws or not targets:
        return 0.0
    return 1.0 - (comb(population - targets, draws) if draws <= population - targets else 0) / comb(population, draws)


def draw_rank(deck: Sequence[str], rank: str, draws: int) -> Probability:
    if rank not in RANKS:
        raise ValueError("Unknown rank")
    return Probability(at_least_one(len(deck), sum(c[0] == rank for c in deck), draws), "exact", 0, f"draw_rank_{rank}")


def draw_suit(deck: Sequence[str], suit: str, draws: int) -> Probability:
    if suit not in "SHCD":
        raise ValueError("Unknown suit")
    return Probability(at_least_one(len(deck), sum(c[1] == suit for c in deck), draws), "exact", 0, f"draw_suit_{suit}")


def encode(cards: Sequence[str]) -> tuple[tuple[int, int], ...]:
    return tuple((RANKS.index(c[0]), "SHCD".index(c[1])) for c in cards)


def hand_events_encoded(cards: Sequence[tuple[int, int]]) -> set[str]:
    ranks = [0] * 13
    suits = [0] * 4
    masks = [0] * 4
    mask = 0
    for r, s in cards:
        ranks[r] += 1
        suits[s] += 1
        mask |= 1 << r
        masks[s] |= 1 << r
    pairs = sum(n >= 2 for n in ranks)
    trips = sum(n >= 3 for n in ranks)
    results = set()
    if pairs:
        results.add("pair")
    if pairs >= 2:
        results.add("two_pair")
    if trips:
        results.add("three_kind")
    if max(ranks, default=0) >= 4:
        results.add("four_kind")
    if trips and pairs >= 2:
        results.add("full_house")
    if any(mask & pattern == pattern for pattern in STRAIGHTS):
        results.add("straight")
    if max(suits, default=0) >= 5:
        results.add("flush")
        if any(m & p == p for m in masks for p in STRAIGHTS):
            results.add("straight_flush")
    return results


def hand_events(cards: Sequence[str]) -> set[str]:
    return hand_events_encoded(encode(cards))


def after_draw(keep: Sequence[str], deck: Sequence[str], draws: int, *, sample_count: int = 2000, seed: int = 93481, exact_limit: int = 10000) -> dict[str, Probability]:
    """Physical card entries are sampled by index, including legitimate copies.

    The caller supplies G.deck only; current hand and discarded cards are never
    added back into the population. Multi-discard/adaptive strategy is excluded.
    """
    if not 0 <= draws <= len(deck) or sample_count < 1:
        raise ValueError("Invalid sample parameters")
    fixed, population = encode(keep), encode(sorted(deck))
    total = comb(len(population), draws)
    hits = dict.fromkeys(EVENTS, 0)
    if total <= exact_limit:
        outcomes = combinations(population, draws)
        n, method = total, "exact"
    else:
        rng = random.Random(seed)
        outcomes = (rng.sample(population, draws) for _ in range(sample_count))
        n, method = sample_count, "monte_carlo"
    for outcome in outcomes:
        for event in hand_events_encoded((*fixed, *outcome)):
            hits[event] += 1
    return {event: Probability(hits[event] / n, method, n if method == "monte_carlo" else 0, event, sqrt(hits[event] / n * (1 - hits[event] / n) / n) if method == "monte_carlo" else 0) for event in EVENTS}


def target_hand_probability(keep: Sequence[str], deck: Sequence[str], draws: int, target: str, **kwargs) -> Probability:
    if target not in EVENTS:
        raise ValueError("Unknown target")
    return after_draw(keep, deck, draws, **kwargs)[target]
