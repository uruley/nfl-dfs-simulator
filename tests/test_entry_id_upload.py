"""Entry-ID upload format."""

from pathlib import Path

from nfl_dfs.showdown_rules import (
    UPLOAD_HEADER,
    UPLOAD_HEADER_ENTRY_ID,
    Lineup,
    Player,
    load_entry_ids,
    make_entry_ids,
    write_upload_csv,
)

ROOT = Path(__file__).resolve().parents[1]
ENTRY_FIX = ROOT / "fixtures" / "entry_ids_sample.csv"


def _p(dk_id, name, salary=3000):
    return Player(
        dk_id=dk_id,
        name=name,
        position="WR",
        team="KC",
        salary=salary,
        proj_fp=10.0,
    )


def _lu():
    cpt = _p("900001", "Patrick Mahomes", 10600)
    flex = [_p(str(900000 + i), f"P{i}", 3000 + i * 100) for i in range(2, 7)]
    return Lineup(cpt=cpt, flex=flex)


def test_bare_upload_header(tmp_path: Path):
    path = tmp_path / "bare.csv"
    write_upload_csv(path, [_lu()])
    header = path.read_text(encoding="utf-8").strip().splitlines()[0]
    assert header == ",".join(UPLOAD_HEADER)


def test_entry_id_upload_header_shape(tmp_path: Path):
    path = tmp_path / "eid.csv"
    ids = make_entry_ids(2, start=500)
    write_upload_csv(path, [_lu(), _lu()], entry_ids=ids)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == ",".join(UPLOAD_HEADER_ENTRY_ID)
    assert lines[0].startswith("Entry ID,")
    row = lines[1].split(",")
    assert row[0] == "500"
    assert "Patrick Mahomes (900001)" in lines[1]
    assert len(row) == 7  # Entry ID + 6 slots


def test_load_entry_ids_fixture():
    ids = load_entry_ids(ENTRY_FIX)
    assert ids[0] == "100001"
    assert len(ids) == 5
