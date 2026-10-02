from math import comb

import pytest

from core.probability import at_least_one, hand_events, after_draw, target_hand_probability, draw_rank, draw_suit


def test_44_four_targets_four_draws_regression():
    # Manually verifiable products: C(40,4)=91390; C(44,4)=135751.
    expected = 44361 / 135751
    assert at_least_one(44, 4, 4) == pytest.approx(expected, abs=1e-15)


@pytest.mark.parametrize("n,k,d,wanted", [(4, 1, 1, 1/4), (4, 1, 2, 1/2), (4, 4, 4, 1), (4, 0, 4, 0), (4, 1, 0, 0), (4, 3, 2, 1)])
def test_hypergeometric_small(n, k, d, wanted):
    assert at_least_one(n, k, d) == wanted


def test_rank_and_suit():
    deck = ["AS", "AH", "2S", "3C"]
    assert draw_rank(deck, "A", 2).value == pytest.approx(5/6)
    assert draw_suit(deck, "S", 2).value == pytest.approx(5/6)


@pytest.mark.parametrize("cards,event", [
    (["AS", "AH"], "pair"),
    (["AS", "AH", "2S", "2C"], "two_pair"),
    (["AS", "AH", "AC"], "three_kind"),
    (["AS", "AH", "AC", "AD"], "four_kind"),
    (["AS", "AH", "AC", "2S", "2C"], "full_house"),
    (["AH", "2S", "3D", "4C", "5H"], "straight"),
    (["TH", "JH", "QH", "KH", "AH"], "straight_flush"),
    (["2H", "4H", "7H", "9H", "AH"], "flush"),
])
def test_known_hand_events(cards, event):
    assert event in hand_events(cards)


def test_no_wraparound_straight():
    assert "straight" not in hand_events(["QH", "KH", "AH", "2H", "3H"])


def test_exact_pair_enumeration():
    # Six pairs of draws: AH2C,AH2D,AH3C,2C2D,2C3C,2D3C.
    result = after_draw(["AS"], ["AH", "2C", "2D", "3C"], 2)
    assert result["pair"].method == "exact"
    assert result["pair"].value == pytest.approx(4/6)


def test_open_straight_flush_one_card_exact():
    p = target_hand_probability(["2H", "3H", "4H", "5H"], ["AH", "6H", "2C", "7D"], 1, "straight_flush")
    assert p.method == "exact" and p.value == 0.5


def test_monte_carlo_known_value_and_repeatability():
    exact = after_draw(["AS"], ["AH", "2C", "2D", "3C"], 2)
    kwargs = dict(sample_count=20000, seed=9, exact_limit=0)
    mc = after_draw(["AS"], ["AH", "2C", "2D", "3C"], 2, **kwargs)
    assert mc["pair"].value == pytest.approx(exact["pair"].value, abs=0.015)
    assert mc["pair"].method == "monte_carlo" and mc["pair"].sample_count == 20000
    assert mc == after_draw(["AS"], ["3C", "2D", "AH", "2C"], 2, **kwargs)


def test_mc_only_samples_given_deck():
    # No Aces or hearts are in the supplied deck: no hidden full-52 fallback.
    results = after_draw(["AS"], ["2C", "3C", "4C", "5C"], 2, sample_count=500, exact_limit=0)
    assert results["pair"].value == 0
    assert results["straight_flush"].value == 0


def test_physical_duplicate_entries():
    assert target_hand_probability([], ["AS", "AS", "2C"], 2, "pair").value == pytest.approx(1/3)


def test_invalid_draw():
    with pytest.raises(ValueError):
        after_draw([], ["AS"], 2)
