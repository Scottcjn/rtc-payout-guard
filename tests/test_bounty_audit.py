"""Amount-mismatch regression tests.

Failure mode: bounty issues advertising different amounts in title vs
body (#165 said 3 vs 5, #2308 said 17 vs 25).
"""

from rtc_payout_guard import bounty_audit


def test_issue_165_title_3_body_5_mismatch():
    issue = {
        "number": 165,
        "title": "[Bounty: 3 RTC] Fix explorer pagination",
        "body": "Reward is 5 RTC on merge. Claim in a comment below.",
    }
    report = bounty_audit.find_amount_mismatch(issue)
    assert report is not None
    assert report["number"] == 165
    assert report["title_amounts"] == [(3.0, 3.0)]
    assert report["body_amounts"] == [(5.0, 5.0)]
    assert "3 RTC" in report["detail"] and "5 RTC" in report["detail"]


def test_issue_2308_title_17_body_25_mismatch():
    issue = {
        "number": 2308,
        "title": "Bounty 17 RTC: harden attestation replay window",
        "body": "Pays 25 RTC. See RIP-200 for context.",
    }
    assert bounty_audit.find_amount_mismatch(issue) is not None


def test_consistent_amounts_pass():
    issue = {
        "number": 1,
        "title": "[Bounty: 5 RTC] Do the thing",
        "body": "This pays 5 RTC when merged.",
    }
    assert bounty_audit.find_amount_mismatch(issue) is None


def test_range_in_body_overlapping_title_passes():
    issue = {
        "number": 2,
        "title": "[Bounty: 25 RTC] Security review",
        "body": "Depending on severity this pays 15-35 RTC.",
    }
    assert bounty_audit.find_amount_mismatch(issue) is None


def test_range_in_body_excluding_title_amount_is_mismatch():
    issue = {
        "number": 3,
        "title": "[Bounty: 50 RTC] Security review",
        "body": "Depending on severity this pays 15-35 RTC.",
    }
    assert bounty_audit.find_amount_mismatch(issue) is not None


def test_multiple_body_mentions_one_consistent_passes():
    issue = {
        "number": 4,
        "title": "[Bounty: 5 RTC] Docs fix",
        "body": "Tier table: 3 RTC minor, 5 RTC standard, 10 RTC major.",
    }
    assert bounty_audit.find_amount_mismatch(issue) is None


def test_no_amount_on_either_side_is_not_a_mismatch():
    assert (
        bounty_audit.find_amount_mismatch(
            {"number": 5, "title": "[Bounty: 5 RTC] X", "body": "Details inside."}
        )
        is None
    )
    assert (
        bounty_audit.find_amount_mismatch(
            {"number": 6, "title": "Fix the thing", "body": "Pays 5 RTC."}
        )
        is None
    )


def test_rtc_addresses_are_not_parsed_as_amounts():
    addr = "RTC" + "1" * 40
    title_amounts, body_amounts = bounty_audit.extract_amounts(
        "[Bounty: 5 RTC] X", f"Pays 5 RTC to {addr}"
    )
    assert title_amounts == [(5.0, 5.0)]
    assert body_amounts == [(5.0, 5.0)]


def test_rtc_n_form_is_recognized():
    amounts = bounty_audit._amounts_in("reward: RTC 12 on merge")
    assert amounts == [(12.0, 12.0)]
