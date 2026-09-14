"""DraftKings NFL Classic hard rules and pool I/O."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

SALARY_CAP = 50_000
N_SLOTS = 9
UPLOAD_HEADER = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]
FLEX_ELIGIBLE = frozenset({"RB", "WR", "TE"})
SLOT_ORDER = list(UPLOAD_HEADER)  # QB, RB, RB, WR, WR, WR, TE, FLEX, DST

CELL_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<id>[^)]+)\)\s*$")


@dataclass
class ClassicPlayer:
    dk_id: str
    name: str
    position: str
    team: str
    salary: int
    proj_fp: float
    std: float = 5.0
    opp: str = ""
    game_key: str = ""  # normalized sorted "A@B" for multi-game check
    own_est: float | None = None
    boost: float = 1.0
    rush_share: float | None = None
    target_share: float | None = None
    rz_share: float | None = None
    eligible_slots: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        pos = (self.position or "").upper()
        self.position = pos
        parts = [p.strip() for p in pos.replace(",", "/").split("/") if p.strip()]
        slots: set[str] = set()
        for p in parts or [pos]:
            if p in ("QB", "RB", "WR", "TE", "DST", "K"):
                slots.add(p)
            if p in FLEX_ELIGIBLE:
                slots.add("FLEX")
        if "DST" in slots:
            slots.discard("FLEX")
        if not self.eligible_slots:
            self.eligible_slots = frozenset(slots)
        if not self.game_key:
            t, o = self.team.upper(), (self.opp or "").upper()
            if t and o:
                pair = tuple(sorted([t, o]))
                self.game_key = f"{pair[0]}@{pair[1]}"
            else:
                self.game_key = t or self.dk_id

    def cell(self) -> str:
        return f"{self.name} ({self.dk_id})"

    def can_slot(self, slot: str) -> bool:
        return slot in self.eligible_slots


@dataclass
class ClassicLineup:
    """Ordered slots matching UPLOAD_HEADER."""

    players: list[ClassicPlayer]  # length 9
    script_id: int = -1
    sim_fp: float = 0.0
    salary: int = 0
    tag: str = ""

    def __post_init__(self) -> None:
        if len(self.players) != N_SLOTS:
            raise ValueError(f"Classic lineup needs {N_SLOTS} players, got {len(self.players)}")

    def player_ids(self) -> list[str]:
        return [p.dk_id for p in self.players]

    def key(self) -> str:
        return ",".join(sorted(self.player_ids()))

    def cells(self) -> list[str]:
        return [p.cell() for p in self.players]

    def game_keys(self) -> set[str]:
        return {p.game_key for p in self.players if p.game_key}


def parse_cell(cell: str) -> tuple[str, str]:
    cell = (cell or "").strip()
    m = CELL_RE.match(cell)
    if not m:
        raise ValueError(f"Bad lineup cell (expected 'Name (id)'): {cell!r}")
    return m.group("name").strip(), m.group("id").strip()


def safe_float(x, default=None):
    try:
        if x is None or str(x).strip() == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _game_key_from_info(game_info: str) -> str:
    if not game_info:
        return ""
    token = game_info.split()[0]
    if "@" not in token:
        return ""
    a, b = token.split("@", 1)
    pair = tuple(sorted([a.strip().upper(), b.strip().upper()]))
    return f"{pair[0]}@{pair[1]}"


def lineup_salary(players: Iterable[ClassicPlayer]) -> int:
    return sum(p.salary for p in players)


def validate_lineup(
    lineup: ClassicLineup,
    salary_cap: int = SALARY_CAP,
    require_multi_game: bool | None = None,
    slate_n_games: int | None = None,
) -> list[str]:
    """Return list of rule violations (empty = legal).

    When require_multi_game is None: enforce ≥2 games iff slate_n_games is None
    or slate_n_games >= 2 (standard DK Classic multi-game slate).
    """
    errs: list[str] = []
    if len(lineup.players) != N_SLOTS:
        errs.append(f"need {N_SLOTS} players, got {len(lineup.players)}")
        return errs

    ids = lineup.player_ids()
    if len(ids) != len(set(ids)):
        errs.append("players must be unique")

    for slot, p in zip(SLOT_ORDER, lineup.players):
        if not p.can_slot(slot):
            errs.append(f"{p.name} ({p.position}) not eligible for {slot}")

    sal = lineup_salary(lineup.players)
    if sal > salary_cap:
        errs.append(f"salary {sal} exceeds cap {salary_cap}")
    lineup.salary = sal

    enforce = require_multi_game
    if enforce is None:
        enforce = True if slate_n_games is None else slate_n_games >= 2
    if enforce:
        games = lineup.game_keys()
        if len(games) < 2:
            errs.append(f"need players from ≥2 games, got {len(games)}")

    return errs


def load_pool_csv(path: Path) -> list[dict]:
    players = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dk_id = str(row.get("dk_id") or row.get("ID") or "").strip()
            name = (row.get("name") or row.get("Name") or "").strip()
            if not dk_id and not name:
                continue
            sal = safe_float(row.get("salary") or row.get("Salary"), 0) or 0
            team = (row.get("team") or row.get("TeamAbbrev") or row.get("Team") or "").strip()
            opp = (row.get("opp") or "").strip()
            game_info = (row.get("game_info") or row.get("Game Info") or "").strip()
            game_key = (row.get("game_key") or "").strip() or _game_key_from_info(game_info)
            players.append(
                {
                    "dk_id": dk_id or name,
                    "name": name or dk_id,
                    "position": (row.get("position") or row.get("Position") or "").strip(),
                    "team": team,
                    "opp": opp,
                    "salary": int(sal),
                    "game_info": game_info,
                    "game_key": game_key,
                }
            )
    return players


def load_projections_csv(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dk_id = str(row.get("dk_id") or row.get("ID") or "").strip()
            if not dk_id:
                continue
            proj = safe_float(row.get("proj_fp"))
            if proj is None:
                continue
            std = safe_float(row.get("std"))
            floor = safe_float(row.get("floor"))
            ceiling = safe_float(row.get("ceiling"))
            if std is None and floor is not None and ceiling is not None:
                std = max(1.0, (ceiling - floor) / 1.68)
            if std is None:
                std = max(3.0, proj * 0.40)
            sal = safe_float(row.get("salary"))
            own = safe_float(row.get("own_est"))
            team = (row.get("team") or "").strip()
            opp = (row.get("opp") or "").strip()
            game_info = (row.get("game_info") or row.get("Game Info") or "").strip()
            game_key = (row.get("game_key") or "").strip() or _game_key_from_info(game_info)
            out[dk_id] = {
                "dk_id": dk_id,
                "name": (row.get("name") or "").strip(),
                "position": (row.get("position") or "").strip(),
                "team": team,
                "opp": opp,
                "salary": int(sal) if sal is not None else None,
                "proj_fp": proj,
                "std": std,
                "floor": floor,
                "ceiling": ceiling,
                "own_est": own,
                "game_info": game_info,
                "game_key": game_key,
                "rush_share": safe_float(row.get("rush_share")),
                "target_share": safe_float(row.get("target_share")),
                "rz_share": safe_float(row.get("rz_share")),
            }
    return out


def merge_pool_proj(pool: list[dict], proj: dict[str, dict]) -> list[ClassicPlayer]:
    merged: list[ClassicPlayer] = []
    for p in pool:
        dk_id = p["dk_id"]
        pr = proj.get(dk_id, {})
        salary = p.get("salary") or pr.get("salary") or 0
        proj_fp = pr.get("proj_fp", p.get("proj_fp"))
        if proj_fp is None:
            continue
        std = pr.get("std") or p.get("std") or max(3.0, float(proj_fp) * 0.40)
        team = (pr.get("team") or p.get("team") or "").upper()
        opp = (pr.get("opp") or p.get("opp") or "").upper()
        game_key = pr.get("game_key") or p.get("game_key") or ""
        merged.append(
            ClassicPlayer(
                dk_id=dk_id,
                name=pr.get("name") or p.get("name") or dk_id,
                position=(pr.get("position") or p.get("position") or "").upper(),
                team=team,
                opp=opp,
                salary=int(salary),
                proj_fp=float(proj_fp),
                std=float(std),
                own_est=pr.get("own_est"),
                game_key=game_key,
                rush_share=pr.get("rush_share"),
                target_share=pr.get("target_share"),
                rz_share=pr.get("rz_share"),
            )
        )
    return merged


def apply_reads(players: list[ClassicPlayer], reads: dict[str, float]) -> None:
    by_id = {p.dk_id: p for p in players}
    by_name = {p.name.lower(): p for p in players}
    for key, mult in reads.items():
        p = by_id.get(str(key)) or by_name.get(str(key).lower())
        if p is not None:
            p.boost = float(mult)


def slate_game_count(players: Sequence[ClassicPlayer]) -> int:
    return len({p.game_key for p in players if p.game_key})


def write_upload_csv(path: Path, lineups: list[ClassicLineup]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(UPLOAD_HEADER)
        for lu in lineups:
            w.writerow(lu.cells())
