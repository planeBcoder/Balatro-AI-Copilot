from core.presentation import metric


def test_score_lookahead_without_win_probability_renders_cleanly():
    candidate={'action':'discard','next_play':{'finish_next_play_probability':None,'expected_best_score':125.5,'sample_count':12}}
    text=metric(candidate,{})
    assert '125.5' in text and '非过关率' in text
    assert '无法' not in text
