"""Integration: vegas + field-sims + entry-id CLI flags."""

from pathlib import Path

from nfl_dfs.cli import main
from nfl_dfs.showdown_rules import UPLOAD_HEADER_ENTRY_ID, parse_cell

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "fixtures" / "showdown_pool.csv"
PROJ = ROOT / "fixtures" / "projections.csv"
OWN = ROOT / "fixtures" / "ownership_sample.csv"
META = ROOT / "fixtures" / "contest_sample.json"


def test_cli_sim_showdown_field_and_vegas(tmp_path: Path):
    out = tmp_path / "exports"
    rc = main(
        [
            "sim-showdown",
            "--pool",
            str(POOL),
            "--projections",
            str(PROJ),
            "--n-scripts",
            "40",
            "--portfolio",
            "8",
            "--exposure",
            "0.40",
            "--seed",
            "7",
            "--out",
            str(out),
            "--spread",
            "-2.5",
            "--total",
            "48.5",
            "--field-sims",
            "40",
            "--ownership",
            str(OWN),
            "--field-size",
            "200",
            "--entry-fee",
            "5",
            "--entry-id-start",
            "9000",
            "--contest-meta",
            str(META),
        ]
    )
    assert rc == 0
    upload = out / "lineups-showdown-upload.csv"
    lines = upload.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == ",".join(UPLOAD_HEADER_ENTRY_ID)
    assert lines[1].startswith("9000,")
    for row in lines[1:]:
        cells = row.split(",")
        assert len(cells) == 7
        for c in cells[1:]:
            parse_cell(c)
    meta = (out / "sim-showdown-meta.json").read_text(encoding="utf-8")
    assert '"field_sims": 40' in meta
    assert '"vegas_spread"' in meta
    assert (out / "sim-showdown-priced.csv").exists()
