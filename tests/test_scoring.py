from nfl_dfs.scoring import StatLine, apply_cpt_multiplier, score_stats


def test_pass_bonus_300():
    s = StatLine(pass_yd=300, pass_td=2, interceptions=1)
    # 300*0.04=12 + 8 TD -1 INT +3 bonus = 22
    assert abs(score_stats(s) - 22.0) < 1e-9


def test_rush_and_rec_bonuses():
    s = StatLine(rush_yd=100, rush_td=1, rec_yd=100, receptions=5, rec_td=1)
    # 10 rush yd + 6 rush TD +3 rush bonus + 10 rec yd +5 PPR +6 rec TD +3 rec bonus = 43
    assert abs(score_stats(s) - 43.0) < 1e-9


def test_fumble_and_2pt():
    s = StatLine(rush_yd=10, fumbles_lost=1, two_pt=1)
    assert abs(score_stats(s) - (1.0 - 1.0 + 2.0)) < 1e-9


def test_cpt_multiplier():
    assert apply_cpt_multiplier(20.0) == 30.0
    assert apply_cpt_multiplier(20.0, 1.5) == 30.0
