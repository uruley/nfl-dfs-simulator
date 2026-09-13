from pathlib import Path

import pytest

from nfl_dfs.classic_rules import (
    SALARY_CAP,
    UPLOAD_HEADER,
    ClassicLineup,
    ClassicPlayer,
    parse_cell,
    validate_lineup,
    write_upload_csv,
)


def _p(dk_id, name, pos, salary, team="KC", opp="BUF", **kw):
    return ClassicPlayer(
        dk_id=dk_id,
        name=name,
        position=pos,
        team=team,
        opp=opp,
        salary=salary,
        proj_fp=kw.get("proj_fp", 10.0),
        game_key=kw.get("game_key", ""),
    )


def _legal_lineup():
    # Two games: BUF@KC and DAL@PHI
    return ClassicLineup(
        players=[
            _p("1", "QB1", "QB", 7000, "KC", "BUF"),
            _p("2", "RB1", "RB", 6000, "KC", "BUF"),
            _p("3", "RB2", "RB", 5500, "PHI", "DAL", game_key="DAL@PHI"),
            _p("4", "WR1", "WR", 6500, "BUF", "KC"),
            _p("5", "WR2", "WR", 5000, "DAL", "PHI", game_key="DAL@PHI"),
            _p("6", "WR3", "WR", 4500, "PHI", "DAL", game_key="DAL@PHI"),
            _p("7", "TE1", "TE", 4000, "KC", "BUF"),
            _p("8", "FLX", "RB", 3500, "DAL", "PHI", game_key="DAL@PHI"),
            _p("9", "DST1", "DST", 2500, "BUF", "KC"),
        ]
    )


def test_salary_cap():
    lu = _legal_lineup()
    # inflate salaries to blow cap
    for p in lu.players:
        p.salary = 8000
    errs = validate_lineup(lu)
    assert any("salary" in e.lower() for e in errs)


def test_flex_eligibility():
    lu = _legal_lineup()
    # Put QB in FLEX slot (index 7)
    lu.players[7] = _p("99", "BadQB", "QB", 3000, "DAL", "PHI", game_key="DAL@PHI")
    errs = validate_lineup(lu)
    assert any("FLEX" in e for e in errs)

    # DST in FLEX also illegal
    lu.players[7] = _p("98", "BadDST", "DST", 3000, "DAL", "PHI", game_key="DAL@PHI")
    errs = validate_lineup(lu)
    assert any("FLEX" in e for e in errs)

    # WR in FLEX OK
    lu.players[7] = _p("97", "GoodWR", "WR", 3000, "DAL", "PHI", game_key="DAL@PHI")
    # fix uniqueness / other slots still legal
    errs = validate_lineup(lu)
    assert not any("FLEX" in e for e in errs)


def test_multi_game_rule():
    # All same game
    players = [
        _p("1", "QB1", "QB", 7000),
        _p("2", "RB1", "RB", 6000),
        _p("3", "RB2", "RB", 5500),
        _p("4", "WR1", "WR", 6500),
        _p("5", "WR2", "WR", 5000),
        _p("6", "WR3", "WR", 4500),
        _p("7", "TE1", "TE", 4000),
        _p("8", "FLX", "RB", 3500),
        _p("9", "DST1", "DST", 2500),
    ]
    lu = ClassicLineup(players=players)
    errs = validate_lineup(lu, slate_n_games=2)
    assert any("≥2 games" in e or "2 games" in e for e in errs)

    lu2 = _legal_lineup()
    assert validate_lineup(lu2, slate_n_games=2) == []
    assert lu2.salary <= SALARY_CAP


def test_upload_csv_header(tmp_path: Path):
    lu = _legal_lineup()
    path = tmp_path / "classic.csv"
    write_upload_csv(path, [lu])
    text = path.read_text(encoding="utf-8")
    header = text.strip().splitlines()[0]
    assert header == ",".join(UPLOAD_HEADER)
    row = text.strip().splitlines()[1]
    cells = row.split(",")
    assert len(cells) == 9
    for cell in cells:
        parse_cell(cell)


def test_player_flex_slots():
    rb = _p("1", "RB", "RB", 5000)
    assert rb.can_slot("RB") and rb.can_slot("FLEX")
    assert not rb.can_slot("QB")
    dst = _p("2", "DST", "DST", 3000)
    assert dst.can_slot("DST")
    assert not dst.can_slot("FLEX")
