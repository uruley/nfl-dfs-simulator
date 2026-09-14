"""Scratch / inactives watcher: compare entered lineups vs live status.

Alert only when someone in our lineups is OUT / INACTIVE (DOUBTFUL = soft warn).
Never mutates the lineups file — report suggests next upload version only.
"""

from __future__ import annotations

import csv
import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from nfl_dfs.showdown_rules import parse_cell

HIT_STATUSES = frozenset({"OUT", "INACTIVE"})
SOFT_WARN_STATUSES = frozenset({"DOUBTFUL"})
KNOWN_STATUSES = frozenset(
    {"OUT", "INACTIVE", "DOUBTFUL", "QUESTIONABLE", "ACTIVE", "UNKNOWN"}
)

DEFAULT_OUT = Path("/home/box/nfl-dfs/exports/scratch-watch")
REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_DIR = REPO_ROOT / "sources"

_STATUS_ALIASES = {
    "O": "OUT",
    "OUT": "OUT",
    "IR": "OUT",
    "INACTIVE": "INACTIVE",
    "INACT": "INACTIVE",
    "IA": "INACTIVE",
    "D": "DOUBTFUL",
    "DOUBTFUL": "DOUBTFUL",
    "Q": "QUESTIONABLE",
    "QUESTIONABLE": "QUESTIONABLE",
    "P": "ACTIVE",  # probable → treat as active for HIT purposes
    "PROBABLE": "ACTIVE",
    "A": "ACTIVE",
    "ACTIVE": "ACTIVE",
    "GTD": "QUESTIONABLE",
    "SUS": "OUT",
    "SUSPENDED": "OUT",
}


@dataclass
class LineupPlayer:
    dk_id: str
    name: str
    team: str = ""
    lineups_affected: int = 0


@dataclass
class StatusRow:
    dk_id: str = ""
    name: str = ""
    team: str = ""
    status: str = "UNKNOWN"
    source: str = ""


@dataclass
class FlaggedPlayer:
    dk_id: str
    name: str
    team: str
    status: str
    lineups_affected: int
    severity: str  # "HIT" | "WARN"
    match: str = ""  # how status was resolved


@dataclass
class ScratchWatchResult:
    checked_at: str
    lineups: int
    unique_players: int
    flagged: list[dict[str, Any]] = field(default_factory=list)
    soft_warns: list[dict[str, Any]] = field(default_factory=list)
    all_clear: bool = True
    action: str = "quiet"
    status_source: str = ""
    provenance: list[str] = field(default_factory=list)
    next_upload_suggestion: str = ""
    lineups_path: str = ""
    unknown_count: int = 0


def normalize_status(raw: str | None) -> str:
    if raw is None or str(raw).strip() == "":
        return "UNKNOWN"
    key = str(raw).strip().upper()
    key = re.sub(r"[^A-Z]", "", key) if len(key) <= 3 else key
    # keep multi-word
    key2 = str(raw).strip().upper()
    if key2 in _STATUS_ALIASES:
        return _STATUS_ALIASES[key2]
    # try first token
    tok = key2.split()[0]
    if tok in _STATUS_ALIASES:
        return _STATUS_ALIASES[tok]
    if key2 in KNOWN_STATUSES:
        return key2
    return "UNKNOWN"


def normalize_name(name: str) -> str:
    s = (name or "").lower().strip()
    s = s.replace(".", "").replace("'", "").replace("-", " ")
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _name_keys(name: str) -> list[str]:
    n = normalize_name(name)
    if not n:
        return []
    keys = [n]
    parts = n.split()
    if len(parts) >= 2:
        # last + first initial
        keys.append(f"{parts[-1]} {parts[0][0]}")
        keys.append(parts[-1])
    return keys


def suggest_next_version(lineups_path: Path) -> str:
    """Suggest a next upload filename without touching the original."""
    name = lineups_path.name
    m = re.search(r"(.*-v)(\d+)(\.csv)$", name, re.I)
    if m:
        return f"{m.group(1)}{int(m.group(2)) + 1}{m.group(3)}"
    stem = lineups_path.stem
    return f"{stem}-v2.csv"


def load_pool_lookup(path: Path | None) -> dict[str, dict[str, str]]:
    """dk_id → {name, team, position}."""
    out: dict[str, dict[str, str]] = {}
    if path is None or not path.exists():
        return out
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dk_id = (row.get("dk_id") or row.get("ID") or row.get("id") or "").strip()
            if not dk_id:
                continue
            name = (row.get("name") or row.get("Name") or "").strip()
            team = (row.get("team") or row.get("Team") or "").strip().upper()
            out[dk_id] = {"name": name, "team": team, "position": (row.get("position") or "").strip()}
    return out


def load_lineup_players(path: Path) -> tuple[int, dict[str, LineupPlayer]]:
    """Parse unique players from Classic/Showdown upload CSV.

    Returns (n_lineups, dk_id → LineupPlayer with lineups_affected counts).
    """
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise ValueError(f"empty lineups file: {path}")
    header = [c.strip() for c in rows[0]]
    skip_first = bool(header) and header[0].lower().replace(" ", "") in {
        "entryid",
        "entry_id",
    }

    players: dict[str, LineupPlayer] = {}
    lineup_count = 0
    for row in rows[1:]:
        cells = list(row)
        if skip_first and cells:
            cells = cells[1:]
        cells = [c.strip() for c in cells if c and c.strip()]
        if not cells:
            continue
        lineup_count += 1
        seen_in_lineup: set[str] = set()
        for cell in cells:
            try:
                name, dk_id = parse_cell(cell)
            except ValueError:
                # skip bare entry ids / garbage
                continue
            if dk_id in seen_in_lineup:
                continue
            seen_in_lineup.add(dk_id)
            if dk_id not in players:
                players[dk_id] = LineupPlayer(dk_id=dk_id, name=name)
            players[dk_id].lineups_affected += 1
            # keep first non-empty name
            if name and not players[dk_id].name:
                players[dk_id].name = name
    return lineup_count, players


def load_status_csv(path: Path) -> tuple[dict[str, StatusRow], list[StatusRow]]:
    """Load status CSV keyed by dk_id and a list for name/team fuzzy match."""
    by_id: dict[str, StatusRow] = {}
    rows: list[StatusRow] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for raw in csv.DictReader(f):
            # tolerate varied headers
            lower = {k.lower().strip(): (v or "").strip() for k, v in raw.items() if k}
            dk_id = lower.get("dk_id") or lower.get("id") or lower.get("playerid") or ""
            name = lower.get("name") or lower.get("player") or ""
            team = (lower.get("team") or "").upper()
            status = normalize_status(lower.get("status") or lower.get("injury") or "")
            sr = StatusRow(dk_id=dk_id, name=name, team=team, status=status, source=str(path))
            rows.append(sr)
            if dk_id:
                by_id[dk_id] = sr
    return by_id, rows


def _index_by_name_team(rows: Iterable[StatusRow]) -> dict[tuple[str, str], StatusRow]:
    idx: dict[tuple[str, str], StatusRow] = {}
    for r in rows:
        for key in _name_keys(r.name):
            idx[(key, (r.team or "").upper())] = r
            if not r.team:
                idx[(key, "")] = r
    return idx


def resolve_status(
    player: LineupPlayer,
    by_id: dict[str, StatusRow],
    by_name_team: dict[tuple[str, str], StatusRow],
    pool: dict[str, dict[str, str]],
) -> tuple[str, str, str]:
    """Return (status, team, match_method)."""
    team = player.team
    if not team and player.dk_id in pool:
        team = pool[player.dk_id].get("team", "")
        if not player.name:
            player.name = pool[player.dk_id].get("name", player.name)

    if player.dk_id in by_id:
        sr = by_id[player.dk_id]
        return sr.status, sr.team or team, "dk_id"

    name = player.name or (pool.get(player.dk_id, {}) or {}).get("name", "")
    for key in _name_keys(name):
        if team and (key, team.upper()) in by_name_team:
            sr = by_name_team[(key, team.upper())]
            return sr.status, sr.team or team, "name+team"
        if (key, "") in by_name_team:
            sr = by_name_team[(key, "")]
            return sr.status, sr.team or team, "name"

    # last-name only within team if unique-ish
    parts = normalize_name(name).split()
    if parts and team:
        last = parts[-1]
        if (last, team.upper()) in by_name_team:
            sr = by_name_team[(last, team.upper())]
            return sr.status, sr.team or team, "lastname+team"

    return "UNKNOWN", team, "none"


# --- fetch / scout brief -----------------------------------------------------

_BRIEF_STATUS_RE = re.compile(
    r"\*\*(?P<name>[^*]+)\*\*\s*\|\s*\*\*(?P<status>INACTIVE|OUT|ACTIVE|DOUBTFUL|QUESTIONABLE)[^*]*\*\*",
    re.I,
)
_BRIEF_LIST_RE = re.compile(
    r"\*\*(?P<team>[A-Z]{2,3}):\*\*\s*(?P<names>[^\n]+)",
)
_KC_OUT_RE = re.compile(r"\*\*KC OUT:\*\*[^\n]*?\*\*([^*]+)\*\*", re.I)


def parse_scout_brief(text: str, path: str = "") -> list[StatusRow]:
    """Best-effort parse of Scout markdown briefs for OUT/INACTIVE names."""
    rows: list[StatusRow] = []
    for m in _BRIEF_STATUS_RE.finditer(text):
        name = m.group("name").strip()
        status = normalize_status(m.group("status"))
        rows.append(StatusRow(name=name, status=status, source=path or "scout-brief"))

    for m in _BRIEF_LIST_RE.finditer(text):
        team = m.group("team").upper()
        if team not in {"DAL", "NYG", "DEN", "KC", "BUF", "PHI", "NE", "MIA", "BAL", "CIN",
                        "CLE", "PIT", "HOU", "IND", "JAX", "TEN", "CHI", "DET", "GB", "MIN",
                        "ATL", "CAR", "NO", "TB", "ARI", "LAR", "SF", "SEA", "LAC", "LV", "WAS"}:
            continue
        chunk = m.group("names")
        # preceding heading may say inactives
        for name in re.split(r",|/", chunk):
            name = name.strip().strip("*").strip()
            if not name or len(name) < 3:
                continue
            # skip if looks like status word alone
            if normalize_status(name) != "UNKNOWN" and " " not in name:
                continue
            rows.append(
                StatusRow(name=name, team=team, status="INACTIVE", source=path or "scout-brief-list")
            )

    # KC OUT: **Josh Simmons** style
    for m in _KC_OUT_RE.finditer(text):
        rows.append(
            StatusRow(name=m.group(1).strip(), team="KC", status="OUT", source=path or "scout-brief")
        )
    return rows


def load_scout_brief_fallback(
    sources_dir: Path | None = None,
    games: list[str] | None = None,
) -> tuple[list[StatusRow], list[str]]:
    sources_dir = sources_dir or SOURCES_DIR
    provenance: list[str] = []
    rows: list[StatusRow] = []
    candidates = [
        sources_dir / "classic-2game-brief.md",
        sources_dir / "nyg-rb-hierarchy-harris-out.md",
        sources_dir / "slate-brief-2026-09-13.md",
    ]
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        parsed = parse_scout_brief(text, str(path))
        if parsed:
            rows.extend(parsed)
            provenance.append(f"scout-brief:{path.name} ({len(parsed)} rows)")
    return rows, provenance



TEAM_NAME_TO_ABBR = {
    "arizona cardinals": "ARI",
    "atlanta falcons": "ATL",
    "baltimore ravens": "BAL",
    "buffalo bills": "BUF",
    "carolina panthers": "CAR",
    "chicago bears": "CHI",
    "cincinnati bengals": "CIN",
    "cleveland browns": "CLE",
    "dallas cowboys": "DAL",
    "denver broncos": "DEN",
    "detroit lions": "DET",
    "green bay packers": "GB",
    "houston texans": "HOU",
    "indianapolis colts": "IND",
    "jacksonville jaguars": "JAX",
    "kansas city chiefs": "KC",
    "las vegas raiders": "LV",
    "los angeles chargers": "LAC",
    "los angeles rams": "LAR",
    "miami dolphins": "MIA",
    "minnesota vikings": "MIN",
    "new england patriots": "NE",
    "new orleans saints": "NO",
    "new york giants": "NYG",
    "new york jets": "NYJ",
    "philadelphia eagles": "PHI",
    "pittsburgh steelers": "PIT",
    "san francisco 49ers": "SF",
    "seattle seahawks": "SEA",
    "tampa bay buccaneers": "TB",
    "tennessee titans": "TEN",
    "washington commanders": "WAS",
}


def _http_get(url: str, timeout: float = 12.0) -> str | None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "nfl-dfs-scratch-watch/0.1 (+local desk; best-effort public fetch)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def _extract_espnfitt(html: str) -> dict[str, Any] | None:
    m = re.search(r"window\['__espnfitt__'\]=(\{.*?\});</script>", html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _espn_items_to_rows(items: list[Any], team: str, source: str) -> list[StatusRow]:
    rows: list[StatusRow] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        athlete = it.get("athlete") or {}
        name = (athlete.get("name") or athlete.get("displayName") or "").strip()
        if not name:
            continue
        status_raw = (
            it.get("statusDesc")
            or (it.get("type") or {}).get("description")
            or (it.get("type") or {}).get("abbreviation")
            or ""
        )
        rows.append(
            StatusRow(
                name=name,
                team=team,
                status=normalize_status(str(status_raw)),
                source=source,
            )
        )
    return rows


def _parse_espn_league_json(data: dict[str, Any], source: str, teams_filter: set[str] | None) -> list[StatusRow]:
    rows: list[StatusRow] = []
    blob = (((data.get("page") or {}).get("content") or {}).get("injuries"))
    if isinstance(blob, dict):
        # team page shape: {injuries: [{date, items}], ...}
        groups = blob.get("injuries")
        if isinstance(groups, list):
            for g in groups:
                rows.extend(_espn_items_to_rows((g or {}).get("items") or [], "", source))
            return rows
    if not isinstance(blob, list):
        return rows
    for team_block in blob:
        if not isinstance(team_block, dict):
            continue
        display = (team_block.get("displayName") or "").strip()
        team = TEAM_NAME_TO_ABBR.get(display.lower(), "")
        if teams_filter and team and team not in teams_filter:
            continue
        rows.extend(_espn_items_to_rows(team_block.get("items") or [], team, source))
    return rows


def _parse_html_inactives(html: str, source: str, teams_filter: set[str] | None = None) -> list[StatusRow]:
    """Prefer ESPN embedded JSON; fall back to conservative name+status regex."""
    data = _extract_espnfitt(html)
    if data:
        rows = _parse_espn_league_json(data, source, teams_filter)
        if rows:
            return rows

    rows = []
    # Conservative: "First Last" immediately before status word (max ~3 tokens)
    for m in re.finditer(
        r"\b([A-Z][a-z]+(?:\s[A-Z][a-z\.'\-]+){0,3})\s+(OUT|INACTIVE|Doubtful|Questionable)\b",
        html,
    ):
        name = m.group(1).strip()
        if len(name.split()) < 2:
            continue
        if any(bad in name.lower() for bad in ("player", "practice", "status", "injury", "participation")):
            continue
        rows.append(StatusRow(name=name, status=normalize_status(m.group(2)), source=source))
    return rows


def fetch_public_inactives(
    games: list[str] | None = None,
    *,
    sources_dir: Path | None = None,
) -> tuple[list[StatusRow], list[str]]:
    """Best-effort free public fetch; falls back to Scout briefs on failure."""
    provenance: list[str] = []
    rows: list[StatusRow] = []

    teams: set[str] = set()
    for g in games or []:
        g = g.strip().upper()
        if "@" in g:
            a, b = g.split("@", 1)
            teams.add(a.strip())
            teams.add(b.strip())

    team_map = {t: t.lower() for t in teams}  # ESPN path uses lowercase abbr

    urls: list[str] = [
        "https://www.espn.com/nfl/injuries",
        "https://www.nfl.com/injuries/",
    ]
    for t, abbr in team_map.items():
        urls.append(f"https://www.espn.com/nfl/team/injuries/_/name/{abbr}")

    for url in urls:
        html = _http_get(url)
        if not html:
            provenance.append(f"fetch-fail:{url}")
            continue
        # Derive team abbr from ESPN team injury URLs
        team_from_url = ""
        m_team = re.search(r"/name/([a-z]+)", url)
        if m_team:
            team_from_url = m_team.group(1).upper()
            if team_from_url == "WSH":
                team_from_url = "WAS"
        parsed = _parse_html_inactives(html, url, teams if teams else None)
        if parsed:
            clean = []
            for r in parsed:
                if len((r.name or "").split()) < 2 or len(r.name) >= 60:
                    continue
                if not r.team and team_from_url:
                    r.team = team_from_url
                clean.append(r)
            if clean:
                rows.extend(clean)
                provenance.append(f"fetch-ok:{url} ({len(clean)} rows)")
            else:
                provenance.append(f"fetch-empty:{url}")
        else:
            provenance.append(f"fetch-empty:{url}")

    # Always try Scout brief as supplemental (local, free); CSV path bypasses fetch entirely.
    brief_rows, brief_prov = load_scout_brief_fallback(sources_dir, games)
    if brief_rows:
        rows.extend(brief_rows)
        provenance.extend(brief_prov)

    if not any(p.startswith("fetch-ok") for p in provenance):
        if brief_rows:
            provenance.append("fallback:scout-brief")
        else:
            provenance.append("fetch-failed-no-brief")

    return rows, provenance


def merge_status_rows(
    primary: list[StatusRow],
    extra: list[StatusRow],
) -> tuple[dict[str, StatusRow], dict[tuple[str, str], StatusRow]]:
    """Merge status rows; primary (CSV) wins on dk_id conflicts."""
    by_id: dict[str, StatusRow] = {}
    all_rows: list[StatusRow] = []
    for r in extra + primary:  # primary last so it overwrites
        all_rows.append(r)
        if r.dk_id:
            by_id[r.dk_id] = r
    return by_id, _index_by_name_team(all_rows)


def run_scratch_watch(
    lineups_path: Path,
    *,
    pool_path: Path | None = None,
    status_path: Path | None = None,
    fetch: bool = False,
    games: list[str] | None = None,
    sources_dir: Path | None = None,
) -> ScratchWatchResult:
    n_lineups, players = load_lineup_players(lineups_path)
    pool = load_pool_lookup(pool_path)

    # enrich team from pool
    for p in players.values():
        if p.dk_id in pool:
            if not p.team:
                p.team = pool[p.dk_id].get("team", "")
            if pool[p.dk_id].get("name"):
                # prefer pool name only if empty
                p.name = p.name or pool[p.dk_id]["name"]

    provenance: list[str] = []
    status_rows: list[StatusRow] = []
    status_source = "none"

    if status_path and status_path.exists():
        by_id_csv, status_rows = load_status_csv(status_path)
        status_source = f"status-csv:{status_path}"
        provenance.append(status_source)
        # by_id from CSV; name index built below via merge
        _ = by_id_csv
    elif fetch:
        fetched, fetch_prov = fetch_public_inactives(games, sources_dir=sources_dir)
        provenance.extend(fetch_prov)
        status_rows = fetched
        status_source = (
            "fetch"
            if any(p.startswith("fetch-ok") for p in fetch_prov)
            else "scout-brief-fallback"
        )
    else:
        brief_rows, brief_prov = load_scout_brief_fallback(sources_dir, games)
        if brief_rows:
            status_rows = brief_rows
            provenance.extend(brief_prov)
            status_source = "scout-brief-default"
        else:
            provenance.append("no-status-source")

    by_id, by_name_team = merge_status_rows(status_rows, [])

    hits: list[FlaggedPlayer] = []
    warns: list[FlaggedPlayer] = []
    unknown = 0

    for p in players.values():
        status, team, match = resolve_status(p, by_id, by_name_team, pool)
        team = team or p.team
        if status == "UNKNOWN":
            unknown += 1
            continue
        if status in HIT_STATUSES:
            hits.append(
                FlaggedPlayer(
                    dk_id=p.dk_id,
                    name=p.name,
                    team=team,
                    status=status,
                    lineups_affected=p.lineups_affected,
                    severity="HIT",
                    match=match,
                )
            )
        elif status in SOFT_WARN_STATUSES:
            warns.append(
                FlaggedPlayer(
                    dk_id=p.dk_id,
                    name=p.name,
                    team=team,
                    status=status,
                    lineups_affected=p.lineups_affected,
                    severity="WARN",
                    match=match,
                )
            )

    hits.sort(key=lambda x: (-x.lineups_affected, x.name))
    warns.sort(key=lambda x: (-x.lineups_affected, x.name))

    all_clear = len(hits) == 0
    action = "quiet" if all_clear else "alert"
    checked = datetime.now(timezone.utc).isoformat()

    return ScratchWatchResult(
        checked_at=checked,
        lineups=n_lineups,
        unique_players=len(players),
        flagged=[asdict(h) for h in hits],
        soft_warns=[asdict(w) for w in warns],
        all_clear=all_clear,
        action=action,
        status_source=status_source,
        provenance=provenance,
        next_upload_suggestion=suggest_next_version(lineups_path),
        lineups_path=str(lineups_path),
        unknown_count=unknown,
    )


def write_scratch_artifacts(result: ScratchWatchResult, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    summary = {
        "checked_at": result.checked_at,
        "lineups": result.lineups,
        "unique_players": result.unique_players,
        "flagged": result.flagged,
        "soft_warns": result.soft_warns,
        "all_clear": result.all_clear,
        "action": result.action,
        "status_source": result.status_source,
        "provenance": result.provenance,
        "unknown_count": result.unknown_count,
        "next_upload_suggestion": result.next_upload_suggestion,
        "lineups_path": result.lineups_path,
    }
    json_path = out_dir / "scratch-watch-last.json"
    json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    paths["json"] = json_path

    md_lines = [
        "# Scratch / inactives watch",
        "",
        f"- Checked at: `{result.checked_at}`",
        f"- Lineups: **{result.lineups}**",
        f"- Unique players: **{result.unique_players}**",
        f"- Status source: `{result.status_source}`",
        f"- All clear: **{result.all_clear}**",
        f"- Action: `{result.action}`",
        f"- Lineups file (read-only): `{result.lineups_path}`",
        f"- Suggested next upload name (do not overwrite): `{result.next_upload_suggestion}`",
        "",
    ]
    if result.provenance:
        md_lines.append("## Provenance")
        for p in result.provenance:
            md_lines.append(f"- {p}")
        md_lines.append("")

    if result.flagged:
        md_lines.append("## HITS (OUT / INACTIVE in our lineups)")
        md_lines.append("")
        md_lines.append("| dk_id | name | team | status | lineups_affected |")
        md_lines.append("|-------|------|------|--------|------------------|")
        for h in result.flagged:
            md_lines.append(
                f"| {h['dk_id']} | {h['name']} | {h['team']} | {h['status']} | {h['lineups_affected']} |"
            )
        md_lines.append("")
        md_lines.append(
            f"**Action:** rebuild without flagged players; write "
            f"`{result.next_upload_suggestion}` (never mutate the current upload)."
        )
        md_lines.append("")
    else:
        md_lines.append("## All clear")
        md_lines.append("")
        md_lines.append("No OUT/INACTIVE players found in entered lineups.")
        md_lines.append("")

    if result.soft_warns:
        md_lines.append("## Soft warns (DOUBTFUL)")
        md_lines.append("")
        for w in result.soft_warns:
            md_lines.append(
                f"- {w['name']} ({w['dk_id']}) {w['team']} — {w['status']} "
                f"in {w['lineups_affected']} lineups"
            )
        md_lines.append("")

    if result.unknown_count:
        md_lines.append(f"_Players with UNKNOWN status: {result.unknown_count}_")
        md_lines.append("")

    md_path = out_dir / "scratch-watch-report.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    paths["report"] = md_path

    hits_path = out_dir / "scratch-hits.csv"
    if result.flagged:
        with hits_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f, fieldnames=["dk_id", "name", "team", "status", "lineups_affected"]
            )
            w.writeheader()
            for h in result.flagged:
                w.writerow(
                    {
                        "dk_id": h["dk_id"],
                        "name": h["name"],
                        "team": h["team"],
                        "status": h["status"],
                        "lineups_affected": h["lineups_affected"],
                    }
                )
        paths["hits"] = hits_path
    elif hits_path.exists():
        # clean slate: remove stale hits file if present from prior run
        hits_path.unlink()

    return paths
