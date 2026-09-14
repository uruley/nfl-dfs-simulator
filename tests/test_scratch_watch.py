"""Tests for scratch / inactives watcher."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from nfl_dfs.cli import main
from nfl_dfs.scratch_watch import (
    load_lineup_players,
    load_status_csv,
    normalize_status,
    run_scratch_watch,
    suggest_next_version,
    write_scratch_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "fixtures" / "scratch_status_sample.csv"
LINEUPS = ROOT / "fixtures" / "scratch_lineups_sample.csv"
POOL = ROOT / "fixtures" / "classic_pool.csv"


def test_normalize_status():
    assert normalize_status("out") == "OUT"
    assert normalize_status("INACTIVE") == "INACTIVE"
    assert normalize_status("D") == "DOUBTFUL"
    assert normalize_status("probable") == "ACTIVE"
    assert normalize_status("") == "UNKNOWN"


def test_detects_hit(tmp_path: Path):
    result = run_scratch_watch(LINEUPS, pool_path=POOL, status_path=STATUS)
    assert result.all_clear is False
    assert result.action == "alert"
    assert result.lineups == 3
    ids = {f["dk_id"] for f in result.flagged}
    assert "910009" in ids  # Clyde Edwards-Helaire OUT
    clyde = next(f for f in result.flagged if f["dk_id"] == "910009")
    assert clyde["status"] == "OUT"
    assert clyde["lineups_affected"] == 2  # in lineup 1 and 3
    paths = write_scratch_artifacts(result, tmp_path)
    assert paths["json"].exists()
    assert paths["report"].exists()
    assert paths["hits"].exists()
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["all_clear"] is False
    assert payload["flagged"]
    with paths["hits"].open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert any(r["dk_id"] == "910009" for r in rows)


def test_all_clear_when_active(tmp_path: Path):
    # Rewrite status so Clyde is ACTIVE
    status_path = tmp_path / "all_active.csv"
    status_path.write_text(
        "dk_id,name,team,status\n"
        "910009,Clyde Edwards-Helaire,KC,ACTIVE\n"
        "910005,Isiah Pacheco,KC,ACTIVE\n"
        "910006,James Cook,BUF,ACTIVE\n"
        "910001,Patrick Mahomes,KC,ACTIVE\n"
        "910002,Josh Allen,BUF,ACTIVE\n",
        encoding="utf-8",
    )
    result = run_scratch_watch(LINEUPS, pool_path=POOL, status_path=status_path)
    assert result.all_clear is True
    assert result.action == "quiet"
    assert result.flagged == []
    paths = write_scratch_artifacts(result, tmp_path / "out")
    assert paths["json"].exists()
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["all_clear"] is True
    assert not (tmp_path / "out" / "scratch-hits.csv").exists()


def test_does_not_mutate_lineups(tmp_path: Path):
    # Copy fixture so we can compare bytes
    src = LINEUPS.read_bytes()
    copy = tmp_path / "lineups.csv"
    copy.write_bytes(src)
    before = copy.read_bytes()
    result = run_scratch_watch(copy, pool_path=POOL, status_path=STATUS)
    write_scratch_artifacts(result, tmp_path / "out")
    after = copy.read_bytes()
    assert before == after
    assert after == src


def test_suggest_next_version():
    assert suggest_next_version(Path("uploads/lineups-classic-2game-20-v4.csv")) == (
        "lineups-classic-2game-20-v5.csv"
    )
    assert suggest_next_version(Path("foo.csv")) == "foo-v2.csv"


def test_cli_scratch_watch_hit(tmp_path: Path):
    out = tmp_path / "scratch-watch"
    rc = main(
        [
            "scratch-watch",
            "--lineups",
            str(LINEUPS),
            "--pool",
            str(POOL),
            "--status",
            str(STATUS),
            "--out",
            str(out),
        ]
    )
    assert rc == 1  # HIT
    assert (out / "scratch-watch-last.json").exists()
    assert (out / "scratch-hits.csv").exists()


def test_cli_quiet_ok(tmp_path: Path):
    status_path = tmp_path / "active.csv"
    status_path.write_text(
        "dk_id,name,team,status\n910009,Clyde Edwards-Helaire,KC,ACTIVE\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    rc = main(
        [
            "scratch-watch",
            "--lineups",
            str(LINEUPS),
            "--status",
            str(status_path),
            "--out",
            str(out),
            "--quiet-ok",
        ]
    )
    assert rc == 0
    payload = json.loads((out / "scratch-watch-last.json").read_text(encoding="utf-8"))
    assert payload["all_clear"] is True


def test_load_helpers():
    n, players = load_lineup_players(LINEUPS)
    assert n == 3
    assert "910009" in players
    by_id, rows = load_status_csv(STATUS)
    assert by_id["910009"].status == "OUT"
    assert any(r.status == "INACTIVE" for r in rows)
