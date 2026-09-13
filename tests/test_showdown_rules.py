from pathlib import Path

import pytest

from nfl_dfs.showdown_rules import (
    CPT_MULT,
    SALARY_CAP,
    UPLOAD_HEADER,
    Lineup,
    Player,
    lineup_salary,
    parse_cell,
    validate_lineup,
    write_upload_csv,
)


def _p(dk_id, name, salary, **kw):
    return Player(
        dk_id=dk_id,
        name=name,
        position=kw.get("position", "WR"),
        team=kw.get("team", "KC"),
        salary=salary,
        proj_fp=kw.get("proj_fp", 10.0),
        can_cpt=kw.get("can_cpt", True),
        can_flex=kw.get("can_flex", True),
    )


def test_cpt_salary_math():
    p = _p("1", "A", 10000)
    assert p.cpt_salary == int(round(10000 * CPT_MULT))
    assert p.cpt_salary == 15000


def test_uniqueness_violation():
    a = _p("1", "A", 8000)
    b = _p("2", "B", 5000)
    c = _p("3", "C", 4000)
    d = _p("4", "D", 3000)
    e = _p("5", "E", 2000)
    # CPT also in flex
    lu = Lineup(cpt=a, flex=[a, b, c, d, e])
    errs = validate_lineup(lu)
    assert any("unique" in e.lower() for e in errs)


def test_salary_cap_with_cpt():
    # CPT 11200 -> 16800; five flex that blow the cap
    cpt = _p("1", "Allen", 11200)
    flex = [_p(str(i), f"P{i}", 8000) for i in range(2, 7)]
    sal = lineup_salary(cpt, flex)
    assert sal == 16800 + 5 * 8000
    assert sal > SALARY_CAP
    lu = Lineup(cpt=cpt, flex=flex)
    errs = validate_lineup(lu)
    assert any("salary" in e.lower() for e in errs)


def test_legal_lineup():
    cpt = _p("1", "Allen", 11200)  # cpt_sal 16800
    flex = [
        _p("2", "B", 7200),
        _p("3", "C", 6400),
        _p("4", "D", 4800),
        _p("5", "E", 4000),
        _p("6", "F", 3000),
    ]
    lu = Lineup(cpt=cpt, flex=flex)
    errs = validate_lineup(lu)
    assert errs == []
    assert lu.salary <= SALARY_CAP


def test_upload_csv_header(tmp_path: Path):
    cpt = _p("900001", "Patrick Mahomes", 10600)
    flex = [_p(str(900000 + i), f"P{i}", 3000 + i * 100) for i in range(2, 7)]
    lu = Lineup(cpt=cpt, flex=flex)
    path = tmp_path / "up.csv"
    write_upload_csv(path, [lu])
    text = path.read_text(encoding="utf-8")
    header = text.strip().splitlines()[0]
    assert header == ",".join(UPLOAD_HEADER)
    row = text.strip().splitlines()[1]
    assert "Patrick Mahomes (900001)" in row
    cells = row.split(",")
    assert len(cells) == 6
    for cell in cells:
        parse_cell(cell)


def test_parse_cell():
    name, dk_id = parse_cell("Stefon Diggs (900004)")
    assert name == "Stefon Diggs"
    assert dk_id == "900004"
    with pytest.raises(ValueError):
        parse_cell("no id here")
