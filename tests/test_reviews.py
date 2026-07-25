"""Review-bounty gate regression tests.

Failure mode: well-written reviews that name no actual defect, target
unmerged PRs, or review bot dependency bumps.
"""

from rtc_payout_guard import reviews

REAL_DEFECT_BODY = (
    "The retry loop in fetch_block() never resets backoff_ms after a "
    "success, so the second failure in a long session waits the maximum "
    "delay immediately. Reset it at the top of the success branch, and the "
    "unit test on line 88 asserts the old buggy value."
)


def _review(**overrides):
    base = {
        "state": "CHANGES_REQUESTED",
        "pr_merged": True,
        "pr_title": "Fix retry backoff reset in block fetcher",
        "body": REAL_DEFECT_BODY,
    }
    base.update(overrides)
    return base


def test_defect_naming_review_on_merged_pr_qualifies():
    verdict = reviews.qualifies_for_review_bounty(_review())
    assert verdict.qualifies is True
    assert all(verdict.gates.values())
    # Even a pass says the third gate needs human confirmation.
    assert any("human" in n for n in verdict.notes)


def test_commented_state_fails_gate_one():
    verdict = reviews.qualifies_for_review_bounty(_review(state="COMMENTED"))
    assert verdict.qualifies is False
    assert verdict.gates["state_is_approve_or_changes_requested"] is False
    assert verdict.gates["pr_is_merged"] is True  # per-gate visibility


def test_unmerged_pr_fails_gate_two():
    verdict = reviews.qualifies_for_review_bounty(_review(pr_merged=False))
    assert verdict.qualifies is False
    assert verdict.gates["pr_is_merged"] is False


def test_dependabot_review_claim_rejected():
    # Reviews of bot dependency bumps NEVER qualify.
    verdict = reviews.qualifies_for_review_bounty(
        _review(pr_title="chore(deps): bump requests from 2.31.0 to 2.32.5")
    )
    assert verdict.qualifies is False
    assert verdict.gates["not_a_bot_dependency_bump"] is False


def test_renovate_and_plain_bump_titles_detected():
    assert reviews.is_bot_dependency_bump("Bump lodash from 4.17.20 to 4.17.21")
    assert reviews.is_bot_dependency_bump("Update dependency pytest to v9")
    assert reviews.is_bot_dependency_bump("build(deps): bump actions/checkout")
    assert not reviews.is_bot_dependency_bump("Fix bump-map allocator overflow")


def test_supply_chain_hygiene_boilerplate_flagged_generic():
    body = (
        "Please pin the SHA for this action. Also confirm the CVE floor "
        "is met. Finally, link the changelog for the new version."
    )
    assert reviews.looks_generic(body) is True
    verdict = reviews.qualifies_for_review_bounty(_review(body=body))
    assert verdict.qualifies is False
    assert verdict.gates["names_specific_defect_advisory"] is False
    assert any("advisory" in n for n in verdict.notes)


def test_template_placeholders_flagged_generic():
    for marker in ("[INSERT finding here]", "[ID_DEL_42]", "xxxxx", "YOUR_WALLET"):
        assert reviews.looks_generic(f"Great work overall but see {marker} for details")


def test_restatement_of_pr_description_flagged_generic():
    desc = (
        "This PR adds a new caching layer to the attestation endpoint to "
        "reduce database load during epoch settlement"
    )
    restated = (
        "This adds a new caching layer to the attestation endpoint "
        "to reduce database load during epoch settlement."
    )
    assert reviews.looks_generic(restated, pr_description=desc) is True
    assert reviews.looks_generic(REAL_DEFECT_BODY, pr_description=desc) is False


def test_empty_body_is_generic():
    assert reviews.looks_generic("") is True
    assert reviews.looks_generic("LGTM") is True
