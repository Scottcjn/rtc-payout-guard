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
    actor_id    INTEGER,                -- stable numeric id; a login can be renamed
    first_seen  INTEGER NOT NULL,
    absent_since INTEGER,               -- first run they went missing, not yet a removal
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
    for col, decl in (("actor_id", "INTEGER"), ("absent_since", "INTEGER")):
        try:
            conn.execute(f"ALTER TABLE observations ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass  # already present
    conn.commit()
    return conn


def record_snapshot(
    conn: sqlite3.Connection,
    kind: str,
    target: str,
    actors: Iterable,
    now: int | None = None,
    max_drop_ratio: float = 0.20,
    min_drop_count: int = 5,
) -> dict:
    """Fold one observed membership list into the store.

    `actors` may be logins or (login, numeric_id) pairs. Prefer the pairs: a
    GitHub rename changes the login and keeps the id, and without the id a
    rename is indistinguishable from someone removing their star.

    Two deliberate conservatisms, both because a false accusation costs more
    than a missed one:

    - **Two strikes.** A first absence records `absent_since` and nothing else.
      Only absence across two consecutive successful runs becomes a removal.
      Pagination shift, eventual consistency and transient blips all resolve
      themselves inside one run, so this removes most of them for free.
    - **Mass-drop guard.** If more than `max_drop_ratio` of the previous
      membership vanishes at once AND at least `min_drop_count` actors are
      involved, that is far more likely to be a broken fetch than a coordinated
      exodus. The run is refused and prior state is kept. The absolute floor
      matters: on a repo with three stargazers a single ordinary unstar is 33%,
      so a ratio alone would refuse every real removal on small targets and
      quietly make the tool blind exactly where each event counts most.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    now = int(time.time()) if now is None else now

    seen: dict[str, int | None] = {}
    for a in actors:
        if isinstance(a, (tuple, list)):
            if a and a[0]:
                seen[a[0]] = a[1] if len(a) > 1 else None
        elif a:
            seen[a] = None

    prior = {
        row[0]: {"removed_at": row[1], "actor_id": row[2], "absent_since": row[3]}
        for row in conn.execute(
            "SELECT actor, removed_at, actor_id, absent_since FROM observations"
            " WHERE kind=? AND target=?", (kind, target))
    }
    present_before = {a for a, v in prior.items() if v["removed_at"] is None}

    # Rename detection: same numeric id, different login.
    id_to_old = {v["actor_id"]: a for a, v in prior.items()
                 if v["actor_id"] and v["removed_at"] is None}
    renames = []
    for actor, aid in seen.items():
        if aid and actor not in prior and aid in id_to_old:
            renames.append((id_to_old[aid], actor))

    renamed_from = {old for old, _ in renames}
    effective_missing = present_before - set(seen) - renamed_from

    if present_before and effective_missing:
        drop = len(effective_missing) / len(present_before)
        # An empty result is categorically different from a partial one: no
        # real membership empties completely between two hourly polls, so treat
        # it as a failed fetch at any size rather than applying the floor.
        if (not seen) or (drop > max_drop_ratio and len(effective_missing) >= min_drop_count):
            conn.execute(
                "INSERT INTO runs (kind,target,taken_at,observed) VALUES (?,?,?,?)",
                (kind, target, now, -1))       # -1 marks a refused run
            conn.commit()
            return {"kind": kind, "target": target, "observed": len(seen),
                    "added": [], "removed": [], "pending_removal": [],
                    "reappeared": [], "renamed": renames,
                    "skipped": True, "baseline": False,
                    "error": (f"refused: {len(effective_missing)} of {len(present_before)} "
                              f"missing ({drop:.0%}) exceeds {max_drop_ratio:.0%}; "
                              "treating as a fetch failure, not a mass removal")}

    renamed_to = {new for _, new in renames}
    added, reappeared = [], []
    for actor, aid in sorted(seen.items()):
        if actor in renamed_to:
            continue          # handled by the rename UPDATE below, not a new row
        if actor not in prior:
            added.append(actor)
            conn.execute(
                "INSERT INTO observations (kind,target,actor,actor_id,first_seen,last_seen)"
                " VALUES (?,?,?,?,?,?)", (kind, target, actor, aid, now, now))
        else:
            if prior[actor]["removed_at"] is not None:
                reappeared.append(actor)
                conn.execute(
                    "UPDATE observations SET removed_at=NULL, absent_since=NULL,"
                    " reappeared=reappeared+1, last_seen=?, actor_id=COALESCE(?,actor_id)"
                    " WHERE kind=? AND target=? AND actor=?",
                    (now, aid, kind, target, actor))
            else:
                conn.execute(
                    "UPDATE observations SET last_seen=?, absent_since=NULL,"
                    " actor_id=COALESCE(?,actor_id) WHERE kind=? AND target=? AND actor=?",
                    (now, aid, kind, target, actor))

    # Two-strike promotion.
    removed, pending = [], []
    for actor in sorted(effective_missing):
        if prior[actor]["absent_since"] is None:
            pending.append(actor)
            conn.execute(
                "UPDATE observations SET absent_since=? WHERE kind=? AND target=? AND actor=?",
                (now, kind, target, actor))
        else:
            removed.append(actor)
            conn.execute(
                "UPDATE observations SET removed_at=? WHERE kind=? AND target=? AND actor=?",
                (now, kind, target, actor))

    for old, new in renames:
        conn.execute(
            "UPDATE observations SET actor=?, absent_since=NULL, last_seen=?"
            " WHERE kind=? AND target=? AND actor=?", (new, now, kind, target, old))

    conn.execute("INSERT INTO runs (kind,target,taken_at,observed) VALUES (?,?,?,?)",
                 (kind, target, now, len(seen)))
    conn.commit()
    return {"kind": kind, "target": target, "observed": len(seen), "added": added,
            "removed": removed, "pending_removal": pending, "reappeared": reappeared,
            "renamed": renames, "baseline": not prior}


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
