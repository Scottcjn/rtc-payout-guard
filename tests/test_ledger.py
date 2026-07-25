"""Duplicate-payment and idempotency-key regression tests.

Failure mode: merge triggers an auto-payout bot (flat 5 RTC, reason
``admin_transfer``); a manual considered payment on top double-pays.
Four such duplicates were voided by hand.
"""

import re

from rtc_payout_guard import ledger

T0 = 1_700_000_000


def _row(dest, amount, reason, ts, ref=None):
    row = {"dest": dest, "amount_rtc": amount, "reason": reason, "timestamp": ts}
    if ref is not None:
        row["ref"] = ref
    return row


def test_auto_tier_duplicate_after_manual_bounty_detected():
    # The observed incident: auto 5 RTC on merge, manual 25 RTC bounty for
    # the same work two hours later, same destination.
    rows = [
        _row("RTC" + "ab" * 20, 5.0, "admin_transfer", T0),
        _row("RTC" + "ab" * 20, 25.0, "bounty:pr-142", T0 + 7200, ref="pr-142"),
        _row("RTC" + "cd" * 20, 5.0, "admin_transfer", T0),  # different dest, fine
    ]
    findings = ledger.detect_auto_tier_duplicates(rows)
    assert len(findings) == 1
    f = findings[0]
    assert f["dest"] == "RTC" + "ab" * 20
    assert f["auto_row"]["reason"] == "admin_transfer"
    assert f["manual_row"]["ref"] == "pr-142"
    assert f["seconds_apart"] == 7200


def test_auto_and_manual_far_apart_not_flagged():
    rows = [
        _row("d1", 5.0, "admin_transfer", T0),
        _row("d1", 25.0, "bounty:old-work", T0 + 30 * 86400),
    ]
    assert ledger.detect_auto_tier_duplicates(rows) == []


def test_manual_refs_filter_restricts_matches():
    rows = [
        _row("d1", 5.0, "admin_transfer", T0),
        _row("d1", 25.0, "bounty:pr-1", T0 + 100, ref="pr-1"),
        _row("d1", 10.0, "bounty:pr-2", T0 + 200, ref="pr-2"),
    ]
    findings = ledger.detect_auto_tier_duplicates(rows, manual_refs=["pr-2"])
    assert len(findings) == 1
    assert findings[0]["manual_row"]["ref"] == "pr-2"


def test_check_duplicate_finds_recent_identical_payment():
    rows = [_row("d1", 25.0, "bounty:pr-9", T0)]
    guard = ledger.PaymentGuard(lambda: rows)
    dups = guard.check_duplicate("d1", 25.0, lookback_days=7, now=T0 + 86400)
    assert len(dups) == 1


def test_check_duplicate_respects_lookback_window():
    rows = [_row("d1", 25.0, "bounty:pr-9", T0)]
    guard = ledger.PaymentGuard(lambda: rows)
    assert guard.check_duplicate("d1", 25.0, lookback_days=7, now=T0 + 8 * 86400) == []
    assert guard.check_duplicate("d2", 25.0, lookback_days=7, now=T0 + 86400) == []
    assert guard.check_duplicate("d1", 24.0, lookback_days=7, now=T0 + 86400) == []


def test_idempotency_key_matches_server_charset():
    key = ledger.make_idempotency_key("handle with spaces!", "pr#142", "batch/7")
    assert re.fullmatch(r"[A-Za-z0-9._:\-]{1,128}", key)


def test_idempotency_key_is_deterministic_and_distinct():
    a = ledger.make_idempotency_key("h", "ref-1", "b")
    b = ledger.make_idempotency_key("h", "ref-2", "b")
    assert a == ledger.make_idempotency_key("h", "ref-1", "b")
    assert a != b


def test_idempotency_key_long_inputs_truncated_but_unique():
    long1 = ledger.make_idempotency_key("h" * 200, "ref", "batch-A")
    long2 = ledger.make_idempotency_key("h" * 200, "ref", "batch-B")
    assert len(long1) <= 128
    assert len(long2) <= 128
    assert re.fullmatch(r"[A-Za-z0-9._:\-]{1,128}", long1)
    assert long1 != long2
