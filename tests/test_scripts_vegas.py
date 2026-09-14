"""Vegas-aware script sampling."""

import numpy as np

from nfl_dfs.scripts import sample_scripts


def test_sample_scripts_deterministic_seed():
    a = sample_scripts(20, np.random.default_rng(42))
    b = sample_scripts(20, np.random.default_rng(42))
    assert [s["pace"] for s in a] == [s["pace"] for s in b]
    assert [s["pass_tilt"] for s in a] == [s["pass_tilt"] for s in b]


def test_vegas_total_biases_pace_up():
    low = sample_scripts(400, np.random.default_rng(1), total=38.0)
    high = sample_scripts(400, np.random.default_rng(1), total=55.0)
    assert np.mean([s["pace"] for s in high]) > np.mean([s["pace"] for s in low]) + 3.0


def test_vegas_spread_biases_home_share():
    # Home favorite (spread negative) → higher home_share
    fav = sample_scripts(400, np.random.default_rng(2), spread=-7.0)
    dog = sample_scripts(400, np.random.default_rng(2), spread=7.0)
    assert np.mean([s["home_share"] for s in fav]) > np.mean([s["home_share"] for s in dog]) + 0.08


def test_vegas_priors_still_deterministic():
    a = sample_scripts(30, np.random.default_rng(9), spread=-3.0, total=49.0)
    b = sample_scripts(30, np.random.default_rng(9), spread=-3.0, total=49.0)
    assert [s["margin"] for s in a] == [s["margin"] for s in b]
    assert all(s["vegas_spread"] == -3.0 for s in a)
    assert all(s["vegas_total"] == 49.0 for s in a)
