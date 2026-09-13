from pathlib import Path

from nfl_dfs.ingest import (
    detect_contest_kind,
    ingest_dk_salary_csv,
    parse_game_info,
    parse_name_id,
    write_normalized_pool,
)
from nfl_dfs.cli import main

ROOT = Path(__file__).resolve().parents[1]
SD = ROOT / "fixtures" / "dk_salary_showdown_sample.csv"
CL = ROOT / "fixtures" / "dk_salary_classic_sample.csv"


def test_parse_name_id():
    n, i = parse_name_id("Patrick Mahomes (900001)")
    assert n == "Patrick Mahomes"
    assert i == "900001"


def test_parse_game_info():
    opp, gk, tok = parse_game_info("KC@BUF 01:00PM ET", "KC")
    assert opp == "BUF"
    assert gk == "BUF@KC"
    opp2, _, _ = parse_game_info("KC@BUF 01:00PM ET", "BUF")
    assert opp2 == "KC"


def test_detect_kinds():
    assert detect_contest_kind(["CPT/FLEX", "CPT/FLEX"]) == "showdown"
    assert detect_contest_kind(["QB", "RB/FLEX", "WR/FLEX", "DST"]) == "classic"


def test_ingest_showdown(tmp_path: Path):
    result = ingest_dk_salary_csv(SD)
    assert result.kind == "showdown"
    assert len(result.rows) >= 8
    row = result.rows[0]
    assert row["dk_id"]
    assert row["salary"] > 0
    assert "CPT" in row["roster_positions"]
    out = tmp_path / "pool.csv"
    write_normalized_pool(out, result)
    text = out.read_text(encoding="utf-8")
    assert "dk_id" in text.splitlines()[0]
    assert "roster_positions" in text


def test_ingest_classic(tmp_path: Path):
    result = ingest_dk_salary_csv(CL)
    assert result.kind == "classic"
    assert len(result.rows) >= 10
    # opp parsed from Game Info
    mahomes = next(r for r in result.rows if r["dk_id"] == "910001")
    assert mahomes["opp"] == "BUF"
    assert mahomes["game_key"] == "BUF@KC"
    out = tmp_path / "classic_pool.csv"
    write_normalized_pool(out, result)
    header = out.read_text(encoding="utf-8").splitlines()[0]
    assert "game_key" in header


def test_cli_ingest(tmp_path: Path):
    out = tmp_path / "norm.csv"
    rc = main(["ingest-dk-salary", "--input", str(SD), "--out", str(out)])
    assert rc == 0
    assert out.exists()
