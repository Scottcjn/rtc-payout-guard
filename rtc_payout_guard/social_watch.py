"""Snapshot GitHub stars, forks and followers so removals become visible.

GitHub does not expose an unstar, unfork or unfollow event. `WatchEvent` fires
on star and never on unstar, and the events feed expires. So the only way to
know that someone starred a repo, collected a bounty, and quietly removed the
star is to record the membership yourself and diff it over time.

That matters here because star, fork and follow bounties pay for a state, not
an action. A claimant who reverts the state after payment has taken the money
and given nothing back, and until the first snapshot exists that is invisible.

Two consequences of the design worth stating plainly:

- The first run establishes a baseline and can detect nothing. Removals are
  only visible from the second run onward. Nothing recovers history that was
  never recorded.
- An actor absent from a snapshot is not proof of a removal. They may never
  have been present. `first_seen` distinguishes the two: a row that was seen
  and later disappears is a removal; an actor with no row was never observed.

Storage is a single SQLite file so a run is cheap enough to schedule hourly.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Callable, Iterable, Sequence

STAR = "star"
FORK = "fork"
FOLLOW = "follow"
KINDS = (STAR, FORK, FOLLOW)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    kind        TEXT NOT NULL,          -- star | fork | follow
    target      TEXT NOT NULL,          -- "owner/repo", or the account for follow
    actor       TEXT NOT NULL,          -- the login doing the starring/forking/following
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    removed_at  INTEGER,                -- set when the actor stops appearing
    reappeared  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kind, target, actor)
);
CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY,
    kind      TEXT NOT NULL,
    target    TEXT NOT NULL,
    taken_at  INTEGER NOT NULL,
    observed  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_removed ON observations(removed_at);
CREATE INDEX IF NOT EXISTS idx_obs_actor   ON observations(actor);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10)
    conn.executescript(_SCHEMA)
    return conn


def record_snapshot(
    conn: sqlite3.Connection,
    kind: str,
    target: str,
    actors: Iterable[str],
    now: int | None = None,
) -> dict:
    """Fold one observed membership list into the store.

    Returns the delta for this run: actors added, actors removed, and actors
    that had been removed before and are present again.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    now = int(time.time()) if now is None else now
    seen = {a for a in actors if a}

    prior = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT actor, removed_at FROM observations WHERE kind=? AND target=?",
            (kind, target),
        )
    }

    added, reappeared = [], []
    for actor in sorted(seen):
        if actor not in prior:
            added.append(actor)
            conn.execute(
                "INSERT INTO observations (kind,target,actor,first_seen,last_seen)"
                " VALUES (?,?,?,?,?)",
                (kind, target, actor, now, now),
            )
        else:
            if prior[actor] is not None:
                reappeared.append(actor)
                conn.execute(
                    "UPDATE observations SET removed_at=NULL, reappeared=reappeared+1,"
                    " last_seen=? WHERE kind=? AND target=? AND actor=?",
                    (now, kind, target, actor),
                )
            else:
                conn.execute(
                    "UPDATE observations SET last_seen=? WHERE kind=? AND target=? AND actor=?",
                    (now, kind, target, actor),
                )

    removed = [a for a, rm in prior.items() if rm is None and a not in seen]
    for actor in removed:
        conn.execute(
            "UPDATE observations SET removed_at=? WHERE kind=? AND target=? AND actor=?",
            (now, kind, target, actor),
        )

    conn.execute(
        "INSERT INTO runs (kind,target,taken_at,observed) VALUES (?,?,?,?)",
        (kind, target, now, len(seen)),
    )
    conn.commit()
    return {
        "kind": kind,
        "target": target,
        "observed": len(seen),
        "added": added,
        "removed": removed,
        "reappeared": reappeared,
        "baseline": not prior,
    }


def removals(conn: sqlite3.Connection, kind: str | None = None, since: int = 0) -> list[dict]:
    """Actors that were observed and later stopped appearing."""
    sql = ("SELECT kind,target,actor,first_seen,removed_at,reappeared FROM observations"
           " WHERE removed_at IS NOT NULL AND removed_at >= ?")
    args: list = [since]
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    sql += " ORDER BY removed_at DESC"
    return [
        {"kind": k, "target": t, "actor": a, "first_seen": f,
         "removed_at": r, "reappeared": rp, "held_seconds": r - f}
        for k, t, a, f, r, rp in conn.execute(sql, args)
    ]


def churn_report(conn: sqlite3.Connection, min_cycles: int = 1) -> list[dict]:
    """Actors who have removed and re-added at least `min_cycles` times.

    Repeated cycling is the signal worth escalating. A single removal can be a
    change of mind; a pattern of add, claim, remove, re-add is not.
    """
    rows = conn.execute(
        "SELECT actor, COUNT(*) targets, SUM(reappeared) cycles,"
        " SUM(CASE WHEN removed_at IS NOT NULL THEN 1 ELSE 0 END) currently_removed"
        " FROM observations GROUP BY actor HAVING cycles >= ? OR currently_removed > 0"
        " ORDER BY cycles DESC, currently_removed DESC",
        (min_cycles,),
    )
    return [
        {"actor": a, "targets": t, "cycles": c or 0, "currently_removed": cr}
        for a, t, c, cr in rows
    ]


def cross_reference_paid(
    conn: sqlite3.Connection, paid_actors: dict[str, float]
) -> list[dict]:
    """Removals by actors who were paid. This is the list that costs money.

    `paid_actors` maps a GitHub login to the RTC paid for a star/fork/follow
    bounty. Anyone here who has a removal took payment for a state they later
    reverted.
    """
    out = []
    for r in removals(conn):
        amount = paid_actors.get(r["actor"])
        if amount is not None:
            out.append({**r, "rtc_paid": amount})
    return out


def collect(
    conn: sqlite3.Connection,
    targets: Sequence[tuple[str, str]],
    fetch: Callable[[str, str], list[str]],
) -> list[dict]:
    """Run a full pass. `targets` is a sequence of (kind, target) pairs.

    `fetch(kind, target)` returns the current actor logins. It is injected so
    the whole module tests offline, and so a fetch failure for one target
    cannot silently be recorded as "everyone removed" for that target.
    """
    results = []
    for kind, target in targets:
        try:
            actors = fetch(kind, target)
        except Exception as exc:  # a failed fetch must never look like a mass removal
            results.append({"kind": kind, "target": target, "error": str(exc), "skipped": True})
            continue
        if actors is None:
            results.append({"kind": kind, "target": target, "error": "no data", "skipped": True})
            continue
        results.append(record_snapshot(conn, kind, target, actors))
    return results
