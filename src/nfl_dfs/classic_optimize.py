"""Fast Classic optimizer: fill QB/RB/RB/WR/WR/WR/TE/FLEX/DST under $50k."""

from __future__ import annotations

from typing import Sequence

import numpy as np

from nfl_dfs.classic_rules import (
    N_SLOTS,
    SALARY_CAP,
    SLOT_ORDER,
    ClassicLineup,
    ClassicPlayer,
    lineup_salary,
    slate_game_count,
    validate_lineup,
)


def _ordered_cands(
    players: Sequence[ClassicPlayer],
    fp: np.ndarray,
    slot: str,
    used: set[int],
    rem: int,
    value_tilt: float,
    limit: int = 8,
) -> list[int]:
    scored: list[tuple[float, int]] = []
    for j, p in enumerate(players):
        if j in used or not p.can_slot(slot):
            continue
        if p.salary > rem:
            continue
        fpts = float(fp[j])
        value = fpts / max(1.0, p.salary / 1000.0)
        scored.append((fpts + value_tilt * value, j))
    scored.sort(key=lambda x: -x[0])
    return [j for _, j in scored[:limit]]


def _search(
    players: Sequence[ClassicPlayer],
    fp: np.ndarray,
    salary_cap: int,
    value_tilt: float = 0.0,
    qb_lock: int | None = None,
    max_nodes: int = 8_000,
    find_best: bool = False,
    cand_limit: int = 8,
) -> list[int] | None:
    """DFS fill in SLOT_ORDER. find_best=False returns first legal quickly."""
    require_multi = slate_game_count(players) >= 2
    chosen = [-1] * N_SLOTS
    used: set[int] = set()
    nodes = 0
    best: list[int] | None = None
    best_fp = -1.0
    found_one = False

    def dfs(slot_i: int, rem: int) -> bool:
        """Return True to stop entire search."""
        nonlocal nodes, best, best_fp, found_one
        if nodes > max_nodes:
            return True
        if slot_i >= N_SLOTS:
            games = {players[i].game_key for i in chosen}
            if require_multi and len(games) < 2:
                return False
            total = float(sum(fp[i] for i in chosen))
            if total > best_fp:
                best_fp = total
                best = list(chosen)
            found_one = True
            return not find_best  # stop early unless optimizing

        slot = SLOT_ORDER[slot_i]
        nodes += 1

        if slot_i == 0 and qb_lock is not None:
            if (
                qb_lock not in used
                and players[qb_lock].can_slot("QB")
                and players[qb_lock].salary <= rem
            ):
                cands = [qb_lock]
            else:
                cands = []
        else:
            cands = _ordered_cands(
                players, fp, slot, used, rem, value_tilt, limit=cand_limit
            )

        slots_left = N_SLOTS - slot_i - 1
        for j in cands:
            sal = players[j].salary
            rem_after = rem - sal
            if slots_left > 0 and rem_after < slots_left * 1800:
                continue
            chosen[slot_i] = j
            used.add(j)
            stop = dfs(slot_i + 1, rem_after)
            used.remove(j)
            chosen[slot_i] = -1
            if stop:
                return True
        return False

    dfs(0, salary_cap)
    return best


def _local_swaps(
    players: Sequence[ClassicPlayer],
    fp: np.ndarray,
    chosen: list[int],
    salary_cap: int,
    n_games: int,
) -> list[int]:
    salaries = [p.salary for p in players]
    used = set(chosen)
    sal_used = sum(salaries[i] for i in chosen)
    improved = True
    rounds = 0
    while improved and rounds < 30:
        improved = False
        rounds += 1
        for slot_i, j in enumerate(list(chosen)):
            slot = SLOT_ORDER[slot_i]
            for alt, p in enumerate(players):
                if alt in used or not p.can_slot(slot):
                    continue
                new_sal = sal_used - salaries[j] + salaries[alt]
                if new_sal > salary_cap:
                    continue
                if float(fp[alt]) <= float(fp[j]) + 1e-9:
                    continue
                trial = list(chosen)
                trial[slot_i] = alt
                trial_players = [players[i] for i in trial]
                lu = ClassicLineup(players=trial_players)
                if validate_lineup(lu, salary_cap=salary_cap, slate_n_games=n_games):
                    continue
                used.remove(j)
                used.add(alt)
                chosen = trial
                sal_used = new_sal
                improved = True
                break
            if improved:
                break
    return chosen


def _to_lineup(
    players: Sequence[ClassicPlayer],
    fp: np.ndarray,
    idxs: list[int],
    script_id: int,
    tag: str,
) -> ClassicLineup:
    roster = [players[i] for i in idxs]
    return ClassicLineup(
        players=roster,
        script_id=script_id,
        sim_fp=float(sum(fp[i] for i in idxs)),
        salary=lineup_salary(roster),
        tag=tag,
    )


def build_best_lineup(
    players: Sequence[ClassicPlayer],
    fp: np.ndarray,
    script_id: int = -1,
    tag: str = "",
    salary_cap: int = SALARY_CAP,
) -> ClassicLineup | None:
    n_games = slate_game_count(players)
    for tilt in (0.0, 5.0, 12.0):
        idxs = _search(
            players,
            fp,
            salary_cap,
            value_tilt=tilt,
            find_best=True,
            max_nodes=12_000,
            cand_limit=10,
        )
        if idxs is None:
            continue
        idxs = _local_swaps(players, fp, idxs, salary_cap, n_games)
        lu = _to_lineup(players, fp, idxs, script_id, tag)
        if not validate_lineup(lu, salary_cap=salary_cap, slate_n_games=n_games):
            return lu
    return None


def build_lineups_diverse(
    players: Sequence[ClassicPlayer],
    fp: np.ndarray,
    script_id: int = -1,
    tag: str = "",
    salary_cap: int = SALARY_CAP,
    top_n: int = 6,
) -> list[ClassicLineup]:
    """Several legal lineups via QB locks + value tilts (fast first-legal search)."""
    n_games = slate_game_count(players)
    qbs = [i for i, p in enumerate(players) if p.can_slot("QB")]
    qbs.sort(key=lambda i: -float(fp[i]))
    out: list[ClassicLineup] = []
    seen: set[str] = set()

    attempts: list[tuple[int | None, float]] = [(None, 0.0), (None, 8.0)]
    for qb_i in qbs[:4]:
        attempts.append((qb_i, 0.0))
        attempts.append((qb_i, 6.0))

    for qb_lock, tilt in attempts:
        idxs = _search(
            players,
            fp,
            salary_cap,
            value_tilt=tilt,
            qb_lock=qb_lock,
            find_best=False,
            max_nodes=4_000,
            cand_limit=7,
        )
        if idxs is None:
            continue
        idxs = _local_swaps(players, fp, idxs, salary_cap, n_games)
        lu = _to_lineup(players, fp, idxs, script_id, tag)
        if validate_lineup(lu, salary_cap=salary_cap, slate_n_games=n_games):
            continue
        k = lu.key()
        if k in seen:
            continue
        seen.add(k)
        out.append(lu)
        if len(out) >= top_n:
            break

    out.sort(key=lambda x: -x.sim_fp)
    return out[:top_n]
