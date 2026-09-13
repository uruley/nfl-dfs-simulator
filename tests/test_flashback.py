import json
from pathlib import Path

from nfl_dfs.cli import main
from nfl_dfs.flashback import (
    build_gates,
    load_lineups_csv,
    run_flashback,
    score_cells,
    write_flashback_artifacts,
)
from nfl_dfs.showdown_rules import UPLOAD_HEADER as SD_HEADER
from nfl_dfs.showdown_rules import write_upload_csv
from nfl_dfs.showdown_rules import (
    Lineup,
    Player,
    load_pool_csv,
    load_projections_csv,
    merge_pool_proj,
)
from nfl_dfs.optimize import build_best_lineup
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ACTUALS = ROOT / "fixtures" / "actuals.csv"
POOL = ROOT / "fixtures" / "showdown_pool.csv"
PROJ = ROOT / "fixtures" / "projections.csv"
CLASSIC_ACTUALS = ROOT / "fixtures" / "classic_actuals.csv"


def _showdown_upload(tmp_path: Path) -> Path:
    players = merge_pool_proj(load_pool_csv(POOL), load_projections_csv(PROJ))
    fp = np.array([p.proj_fp for p in players], dtype=np.float64)
    lus = []
    # build a few distinct-ish lineups by perturbing
    rng = np.random.default_rng(3)
    for s in range(5):
        noise = fp + rng.normal(0, 2.0, size=len(fp))
        lu = build_best_lineup(players, noise, script_id=s)
        if lu is not None:
            lus.append(lu)
    path = tmp_path / "lineups-showdown-upload.csv"
    write_upload_csv(path, lus[:4] if lus else [])
    assert path.exists()
    return path


def test_score_showdown_cpt():
    cells = [
        "Josh Allen (900002)",
        "James Cook (900006)",
        "Khalil Shakir (900010)",
        "Dalton Kincaid (900008)",
        "Tyler Bass (900016)",
        "Curtis Samuel (900022)",
    ]
    from nfl_dfs.backtest import load_actual_fp

    actual = load_actual_fp(ACTUALS)
    score, ids, _ = score_cells(cells, actual, "showdown")
    # CPT Allen 31.2 * 1.5 + flex
    expected = 31.2 * 1.5 + 22.5 + 16.1 + 12.8 + 11.0 + 8.5
    assert abs(score - expected) < 1e-6


def test_flashback_scoring_and_gates(tmp_path: Path):
    upload = _showdown_upload(tmp_path)
    out = tmp_path / "backtests"
    result = run_flashback(upload, ACTUALS)
    assert result.kind == "showdown"
    assert len(result.scores) >= 1
    assert result.max_fp >= result.mean_fp >= result.min_fp
    paths = write_flashback_artifacts(result, out)
    assert paths["scores"].exists()
    assert paths["summary"].exists()
    gates_path = paths["gates"]
    assert gates_path.exists()
    payload = json.loads(gates_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert "gates" in payload
    assert "policy" in payload
    assert payload["kind"] == "showdown"
    for g in payload["gates"]:
        assert g["action"] in ("fade", "boost")
        assert "dk_id" in g
        assert "suggestion" in g


def test_gates_shape():
    exposures = {"a": 0.50, "b": 0.10, "c": 0.05}
    actual = {"a": 2.0, "b": 22.0, "c": 1.0}
    names = {"a": "A", "b": "B", "c": "C"}
    gates = build_gates(exposures, actual, names)
    actions = {g["action"] for g in gates}
    assert "fade" in actions  # a is overexposed dud
    assert "boost" in actions  # b is under-owned winner


def test_cli_flashback(tmp_path: Path):
    upload = _showdown_upload(tmp_path)
    out = tmp_path / "backtests"
    rc = main(
        [
            "flashback",
            "--lineups",
            str(upload),
            "--actuals",
            str(ACTUALS),
            "--contest",
            str(ROOT / "fixtures" / "contest_sample.json"),
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    assert (out / "flashback-scores.csv").exists()
    assert (out / "flashback-summary.md").exists()
    assert (out / "next-build-gates.json").exists()


def test_flashback_never_writes_uploads(tmp_path: Path):
    """Refuse writing into a directory named uploads."""
    upload = _showdown_upload(tmp_path)
    bad = tmp_path / "uploads"
    bad.mkdir()
    # write_flashback_artifacts should raise if out is uploads
    result = run_flashback(upload, ACTUALS)
    try:
        write_flashback_artifacts(result, bad)
        raised = False
    except ValueError:
        raised = True
    assert raised
