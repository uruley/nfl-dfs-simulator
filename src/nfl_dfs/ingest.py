"""Ingest DraftKings salary-file style CSVs into normalized pool schemas.

Supports common DK lobby / Salary Cap export columns:
  Position, Name + ID, Name, ID, Roster Position, Salary, Game Info,
  TeamAbbrev, AvgPointsPerGame

Detects Showdown (CPT/FLEX in Roster Position) vs Classic (QB/RB/…).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

CELL_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<id>[^)]+)\)\s*$")
NAME_ID_COL_RE = re.compile(r"^name\s*\+?\s*id$", re.I)

ContestKind = Literal["showdown", "classic", "unknown"]


@dataclass
class IngestResult:
    kind: ContestKind
    rows: list[dict]
    warnings: list[str]


def _norm_header(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").strip().lower())


def _header_map(fieldnames: list[str] | None) -> dict[str, str]:
    """Map normalized header → original header."""
    out: dict[str, str] = {}
    for h in fieldnames or []:
        out[_norm_header(h)] = h
    return out


def _get(row: dict, hmap: dict[str, str], *aliases: str) -> str:
    for a in aliases:
        key = hmap.get(_norm_header(a))
        if key and row.get(key) not in (None, ""):
            return str(row[key]).strip()
    return ""


def parse_name_id(raw: str) -> tuple[str, str]:
    """Parse 'Name (ID)' or return (raw, '')."""
    raw = (raw or "").strip()
    m = CELL_RE.match(raw)
    if m:
        return m.group("name").strip(), m.group("id").strip()
    return raw, ""


def parse_game_info(game_info: str, team: str = "") -> tuple[str, str, str]:
    """Parse DK Game Info into (opp, game_key, game_info_token).

    Examples:
      'KC@BUF 01:00PM ET' with team=KC → opp=BUF, game_key=BUF@KC
      'BUF@MIA 01:00PM ET' with team=MIA → opp=BUF
    """
    gi = (game_info or "").strip()
    if not gi:
        return "", "", ""
    token = gi.split()[0]
    if "@" not in token:
        return "", "", token
    away, home = [x.strip().upper() for x in token.split("@", 1)]
    pair = tuple(sorted([away, home]))
    game_key = f"{pair[0]}@{pair[1]}"
    team_u = (team or "").upper()
    if team_u == away:
        opp = home
    elif team_u == home:
        opp = away
    else:
        opp = ""
    return opp, game_key, token


def detect_contest_kind(roster_positions: list[str]) -> ContestKind:
    joined = " ".join(roster_positions).upper()
    if "CPT" in joined:
        return "showdown"
    classic_markers = {"QB", "RB", "WR", "TE", "DST", "FLEX"}
    tokens = set()
    for rp in roster_positions:
        for part in re.split(r"[/,\s]+", (rp or "").upper()):
            if part:
                tokens.add(part)
    if tokens & {"QB", "RB", "WR", "TE", "DST"}:
        return "classic"
    if "FLEX" in tokens and "CPT" not in tokens:
        # ambiguous single-game flex — treat as showdown-like if only FLEX
        return "classic" if tokens & classic_markers else "unknown"
    return "unknown"


def ingest_dk_salary_csv(path: Path) -> IngestResult:
    """Parse a DK salary export into normalized row dicts."""
    warnings: list[str] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        hmap = _header_map(list(reader.fieldnames or []))
        raw_rows = list(reader)

    if not raw_rows:
        return IngestResult(kind="unknown", rows=[], warnings=["empty CSV"])

    # Detect Name + ID column
    name_id_col = None
    for nh, orig in hmap.items():
        if NAME_ID_COL_RE.match(nh) or nh in ("name + id", "name+id"):
            name_id_col = orig
            break

    roster_vals: list[str] = []
    rows: list[dict] = []
    for row in raw_rows:
        position = _get(row, hmap, "Position", "position")
        roster = _get(row, hmap, "Roster Position", "roster_position", "roster_positions")
        salary_s = _get(row, hmap, "Salary", "salary")
        team = _get(row, hmap, "TeamAbbrev", "Team", "team")
        game_info = _get(row, hmap, "Game Info", "game_info")
        avg = _get(row, hmap, "AvgPointsPerGame", "avg_points", "FPPG")

        name = _get(row, hmap, "Name", "name")
        dk_id = _get(row, hmap, "ID", "Id", "id", "dk_id")

        if name_id_col and row.get(name_id_col):
            n2, i2 = parse_name_id(str(row[name_id_col]))
            if n2:
                name = name or n2
            if i2:
                dk_id = dk_id or i2
        # Sometimes Name itself is "Name (ID)"
        if name and "(" in name and not dk_id:
            n2, i2 = parse_name_id(name)
            if i2:
                name, dk_id = n2, i2

        if not name and not dk_id:
            continue

        try:
            salary = int(float(salary_s)) if salary_s else 0
        except ValueError:
            salary = 0
            warnings.append(f"bad salary for {name}: {salary_s!r}")

        opp, game_key, _tok = parse_game_info(game_info, team)
        roster_vals.append(roster or position)

        rows.append(
            {
                "dk_id": dk_id or name,
                "name": name or dk_id,
                "position": position.upper() if position else "",
                "team": team.upper() if team else "",
                "opp": opp,
                "salary": salary,
                "roster_positions": (roster or position or "").upper(),
                "game_info": game_info,
                "game_key": game_key,
                "avg_points": avg,
            }
        )

    kind = detect_contest_kind(roster_vals)
    if kind == "unknown":
        warnings.append("could not detect Showdown vs Classic from Roster Position")
    return IngestResult(kind=kind, rows=rows, warnings=warnings)


def write_normalized_pool(path: Path, result: IngestResult) -> Path:
    """Write showdown_pool or classic_pool style CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if result.kind == "showdown":
        fields = ["dk_id", "name", "position", "team", "opp", "salary", "roster_positions"]
    else:
        fields = [
            "dk_id",
            "name",
            "position",
            "team",
            "opp",
            "salary",
            "game_info",
            "game_key",
            "roster_positions",
        ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in result.rows:
            # Classic: prefer Position for position; Showdown keep as-is
            out = dict(row)
            if result.kind == "classic" and not out.get("position"):
                # Roster Position may be "RB/FLEX" — take first classic slot
                rp = out.get("roster_positions") or ""
                for part in re.split(r"[/,\s]+", rp):
                    if part in ("QB", "RB", "WR", "TE", "DST", "K"):
                        out["position"] = part
                        break
            w.writerow(out)
    return path
