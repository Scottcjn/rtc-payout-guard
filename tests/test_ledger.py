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


# --- destination canonicalization -------------------------------------
#
# wallets.py accepts an uppercase hex body as the same wallet and returns
# the canonical lowercase form; stars.py case-folds repo names before
# comparing. Ledger rows carry whatever the payout system recorded, which
# is often the form the contributor typed into a claim comment. Comparing
# those as raw strings makes a second payment to the same wallet look like
# a first payment - the exact double-pay this module exists to stop.

LOWER = "RTC" + "9f2a1b" + "0" * 34
UPPER = "RTC" + "9F2A1B" + "0" * 34


def test_check_duplicate_matches_same_wallet_written_in_uppercase():
    rows = [_row(UPPER, 25.0, "bounty:pr-142", T0, ref="pr-142")]
    guard = ledger.PaymentGuard(lambda: rows)
    dups = guard.check_duplicate(LOWER, 25.0, lookback_days=7, now=T0 + 86400)
    assert len(dups) == 1
    assert dups[0]["ref"] == "pr-142"


def test_check_duplicate_matches_canonical_row_from_typed_uppercase_input():
    # Same miss in the other direction: canonical ledger, uppercase input.
    rows = [_row(LOWER, 25.0, "bounty:pr-142", T0)]
    guard = ledger.PaymentGuard(lambda: rows)
    assert guard.check_duplicate(UPPER, 25.0, lookback_days=7, now=T0 + 86400)


def test_check_duplicate_ignores_surrounding_whitespace():
    rows = [_row("  " + LOWER + "\n", 25.0, "bounty:pr-142", T0)]
    guard = ledger.PaymentGuard(lambda: rows)
    assert guard.check_duplicate(LOWER, 25.0, lookback_days=7, now=T0 + 86400)


def test_check_duplicate_still_separates_genuinely_different_wallets():
    # Regression guard: canonicalization must not merge distinct addresses.
    other = "RTC" + "9f2a1c" + "0" * 34
    rows = [_row(other, 25.0, "bounty:pr-142", T0)]
    guard = ledger.PaymentGuard(lambda: rows)
    assert guard.check_duplicate(LOWER, 25.0, lookback_days=7, now=T0 + 86400) == []


def test_check_duplicate_does_not_case_fold_miner_id_names():
    # Miner-id names are registered identities, not hex; folding their case
    # would merge two different registered payees.
    rows = [_row("Frozen-Factorio-Ryan", 25.0, "bounty:pr-9", T0)]
    guard = ledger.PaymentGuard(lambda: rows)
    got = guard.check_duplicate("frozen-factorio-ryan", 25.0, lookback_days=7, now=T0)
    assert got == []


def test_auto_tier_duplicate_detected_across_case_variants():
    rows = [
        _row(LOWER, 5.0, "admin_transfer", T0),
        _row(UPPER, 25.0, "bounty:pr-142", T0 + 7200, ref="pr-142"),
    ]
    findings = ledger.detect_auto_tier_duplicates(rows)
    assert len(findings) == 1
    assert findings[0]["dest"] == LOWER  # reported canonically
    assert findings[0]["manual_row"]["ref"] == "pr-142"


def test_rows_without_a_destination_are_not_paired_with_each_other():
    rows = [
        {"amount_rtc": 5.0, "reason": "admin_transfer", "timestamp": T0},
        {"amount_rtc": 25.0, "reason": "bounty:pr-1", "timestamp": T0 + 60, "ref": "pr-1"},
    ]
    assert ledger.detect_auto_tier_duplicates(rows) == []


def test_check_duplicate_with_empty_destination_matches_nothing():
    rows = [_row("", 25.0, "bounty:pr-1", T0)]
    guard = ledger.PaymentGuard(lambda: rows)
    assert guard.check_duplicate("", 25.0, lookback_days=7, now=T0) == []


def test_normalize_dest_canonicalizes_addresses_and_leaves_names_alone():
    assert ledger.normalize_dest(UPPER) == LOWER
    assert ledger.normalize_dest("  " + LOWER + " ") == LOWER
    assert ledger.normalize_dest("frozen-factorio-ryan") == "frozen-factorio-ryan"
    assert ledger.normalize_dest(None) == ""
