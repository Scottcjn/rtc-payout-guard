"""Regression tests for social_watch.

These encode the false-accusation paths an adversarial review found, because
this module's output gets pointed at real people's reputations. Every test
below is named for the wrong accusation it prevents.
"""
import sqlite3
import pytest
from rtc_payout_guard import social_watch as sw


@pytest.fixture
def conn(tmp_path):
    return sw.connect(str(tmp_path / "w.db"))


def test_baseline_run_accuses_nobody(conn):
    r = sw.record_snapshot(conn, sw.STAR, "o/r", ["a", "b", "c"], now=1000)
    assert r["baseline"] and not r["removed"]


def test_single_absence_is_not_yet_a_removal(conn):
    """Pagination shift and eventual consistency both look like one absence."""
    sw.record_snapshot(conn, sw.STAR, "o/r", ["a", "b", "c"], now=1000)
    r = sw.record_snapshot(conn, sw.STAR, "o/r", ["a", "c"], now=2000)
    assert r["removed"] == []
    assert r["pending_removal"] == ["b"]
    assert sw.removals(conn) == []


def test_two_consecutive_absences_becomes_a_removal(conn):
    sw.record_snapshot(conn, sw.STAR, "o/r", ["a", "b", "c"], now=1000)
    sw.record_snapshot(conn, sw.STAR, "o/r", ["a", "c"], now=2000)
    r = sw.record_snapshot(conn, sw.STAR, "o/r", ["a", "c"], now=3000)
    assert r["removed"] == ["b"]
    assert [x["actor"] for x in sw.removals(conn)] == ["b"]


def test_blip_then_return_never_accuses(conn):
    """The commonest innocent case: one bad page, actor back next run."""
    sw.record_snapshot(conn, sw.STAR, "o/r", ["a", "b"], now=1000)
    sw.record_snapshot(conn, sw.STAR, "o/r", ["a"], now=2000)
    r = sw.record_snapshot(conn, sw.STAR, "o/r", ["a", "b"], now=3000)
    assert r["removed"] == [] and sw.removals(conn) == []
    assert r["reappeared"] == []          # never removed, so not a reappearance


def test_mass_drop_is_refused_not_recorded(conn):
    """A broken fetch must never read as a coordinated exodus."""
    sw.record_snapshot(conn, sw.STAR, "o/r", [f"u{i}" for i in range(100)], now=1000)
    r = sw.record_snapshot(conn, sw.STAR, "o/r", ["u0", "u1"], now=2000)
    assert r["skipped"] and "refused" in r["error"]
    assert r["removed"] == []
    assert sw.removals(conn) == []


def test_empty_fetch_is_refused(conn):
    sw.record_snapshot(conn, sw.STAR, "o/r", ["a", "b", "c"], now=1000)
    r = sw.record_snapshot(conn, sw.STAR, "o/r", [], now=2000)
    assert r["skipped"] and sw.removals(conn) == []


def test_rename_is_not_a_removal(conn):
    """Same numeric id, new login. Without ids this is indistinguishable."""
    sw.record_snapshot(conn, sw.STAR, "o/r", [("oldname", 42), ("b", 7)], now=1000)
    r = sw.record_snapshot(conn, sw.STAR, "o/r", [("newname", 42), ("b", 7)], now=2000)
    assert r["renamed"] == [("oldname", "newname")]
    assert r["removed"] == [] and r["pending_removal"] == []
    assert sw.removals(conn) == []


def test_actor_shared_across_targets_is_independent(conn):
    sw.record_snapshot(conn, sw.STAR, "o/r1", ["a"], now=1000)
    sw.record_snapshot(conn, sw.STAR, "o/r2", ["a"], now=1000)
    sw.record_snapshot(conn, sw.STAR, "o/r1", [], now=2000)   # refused, 100% drop
    r2 = sw.record_snapshot(conn, sw.STAR, "o/r2", ["a"], now=2000)
    assert r2["removed"] == []


def test_never_seen_actor_is_not_a_removal(conn):
    sw.record_snapshot(conn, sw.STAR, "o/r", ["a"], now=1000)
    assert not any(x["actor"] == "ghost" for x in sw.removals(conn))


def test_cross_reference_only_reports_paid_actors(conn):
    sw.record_snapshot(conn, sw.STAR, "o/r", ["a", "b", "c", "d", "e"], now=1000)
    sw.record_snapshot(conn, sw.STAR, "o/r", ["a", "b", "c", "d"], now=2000)
    sw.record_snapshot(conn, sw.STAR, "o/r", ["a", "b", "c", "d"], now=3000)
    out = sw.cross_reference_paid(conn, {"e": 1.0, "a": 5.0})
    assert len(out) == 1 and out[0]["actor"] == "e" and out[0]["rtc_paid"] == 1.0
