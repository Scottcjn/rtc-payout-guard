"""Star-claim regression tests.

Failure modes: fabricated "starred all repos" claims (84 of 369
claimants had zero stars) and un-starring after payment (50 handles).
"""

from rtc_payout_guard import stars

REQUIRED = ["Scottcjn/Rustchain", "Scottcjn/bottube"]


def test_fabricated_star_claim_zero_starred_fails_verification():
    # GET /users/{h}/starred returns nothing: the claim text is worthless.
    result = stars.verify_star_claim("claimant", REQUIRED, lambda h: [])
    assert result["verified"] is False
    assert result["starred"] == []
    assert result["missing"] == REQUIRED


def test_unstarred_after_payment_shows_as_missing_today():
    # We cannot tell never-starred from starred-then-unstarred; either way
    # the current state does not back the claim.
    result = stars.verify_star_claim(
        "unstarrer", REQUIRED, lambda h: ["Scottcjn/Rustchain"]
    )
    assert result["verified"] is False
    assert result["starred"] == ["Scottcjn/Rustchain"]
    assert result["missing"] == ["Scottcjn/bottube"]


def test_genuine_star_claim_verifies():
    fetched = ["scottcjn/rustchain", "Scottcjn/bottube", "other/unrelated"]
    result = stars.verify_star_claim("honest", REQUIRED, lambda h: fetched)
    assert result["verified"] is True
    assert result["missing"] == []
    assert result["starred"] == REQUIRED


def test_comparison_is_case_insensitive():
    result = stars.verify_star_claim(
        "h", ["SCOTTCJN/RUSTCHAIN"], lambda h: ["scottcjn/rustchain"]
    )
    assert result["verified"] is True


def test_fetcher_receives_the_handle():
    seen = []

    def fetch(handle):
        seen.append(handle)
        return []

    stars.verify_star_claim("specific-handle", REQUIRED, fetch)
    assert seen == ["specific-handle"]
