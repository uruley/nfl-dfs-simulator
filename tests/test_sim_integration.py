from pathlib import Path

from nfl_dfs.cli import main
from nfl_dfs.showdown_rules import UPLOAD_HEADER, parse_cell

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "fixtures" / "showdown_pool.csv"
PROJ = ROOT / "fixtures" / "projections.csv"


def test_cli_sim_showdown(tmp_path: Path):
    out = tmp_path / "exports"
    rc = main(
        [
            "sim-showdown",
            "--pool",
            str(POOL),
            "--projections",
            str(PROJ),
            "--n-scripts",
            "50",
            "--portfolio",
            "10",
            "--exposure",
            "0.40",
            "--seed",
            "7",
            "--out",
            str(out),
            "--read",
            "Josh Allen=1.15",
            "--read",
            "900009=0.7",
            "--field-size",
            "500",
            "--entry-fee",
            "3",
        ]
    )
    assert rc == 0
    upload = out / "lineups-showdown-upload.csv"
    assert upload.exists()
    lines = upload.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == ",".join(UPLOAD_HEADER)
    assert len(lines) == 11  # header + 10
    for row in lines[1:]:
        cells = row.split(",")
        assert len(cells) == 6
        ids = []
        for c in cells:
            _n, i = parse_cell(c)
            ids.append(i)
        assert len(ids) == len(set(ids))
    assert (out / "sim-showdown-summary.txt").exists()
