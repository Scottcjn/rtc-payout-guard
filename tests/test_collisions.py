"""Destination-check regression tests for the graded evidence model.

Covers the three corrected outcomes plus injection and foreign-owner:
- SHARED_DESTINATION: a legitimate human + agent pair (must NOT be
  flagged as suspicious).
- CONTESTED_CLAIM: the JHON12091986 / jjb9707 incident shape.
- INJECTED: the FlintLeng bot incident shape.
"""

from rtc_payout_guard import collisions

ADDR = "RTC" + "921ffc" + "0" * 34
OTHER = "RTC" + "aa" * 20


def claims_fixture():
    """jjb9707 declares ADDR in their own issue; four weeks later
    JHON12091986 claims the same address."""
    return [
        {
            "handle": "JHON12091986",
            "address": ADDR,
            "source_url": "https://github.com/Scottcjn/Rustchain/issues/900",
            "timestamp": 2_000_000,
        },
        {
            "handle": "jjb9707",
            "address": ADDR,
            "source_url": "https://github.com/Scottcjn/Rustchain/issues/500",
            "timestamp": 1_000_000,  # four weeks earlier
        },
    ]


def test_ownership_map_sorted_so_first_declarer_is_identifiable():
    omap = collisions.build_ownership_map(claims_fixture())
    assert [c.handle for c in omap[ADDR]] == ["jjb9707", "JHON12091986"]
    assert omap[ADDR][0].timestamp == 1_000_000


def test_find_shared_destinations_requires_two_distinct_handles():
    same_operator_twice = [
        {"handle": "h1", "address": OTHER, "source_url": "", "timestamp": 1},
        {"handle": "h1", "address": OTHER, "source_url": "", "timestamp": 2},
    ]
    omap = collisions.build_ownership_map(same_operator_twice + claims_fixture())
    shared = collisions.find_shared_destinations(omap)
    assert ADDR in shared
    assert OTHER not in shared
    # Original spec name still works.
    assert collisions.find_collisions is collisions.find_shared_destinations


def test_legitimate_human_plus_agent_pair_is_shared_destination_not_suspicious():
    # One operator: a human account plus an agent identity, one wallet.
    # The normal shape of this ecosystem. Must NOT hold or block.
    claims = [
        {"handle": "Scottcjn", "address": OTHER, "source_url": "u1", "timestamp": 10},
        {"handle": "sophiaeagent-beep", "address": OTHER, "source_url": "u2", "timestamp": 20},
    ]
    omap = collisions.build_ownership_map(claims)
    verdict = collisions.check_destination(
        "sophiaeagent-beep", OTHER, omap, known_owners={}
    )
    assert verdict.status == collisions.SHARED_DESTINATION
    assert verdict.disposition == "proceed_with_note"
    assert "shared payout destination" in verdict.reason
    assert "collision" not in verdict.reason.lower()
    assert verdict.evidence["first_declarer"] == "Scottcjn"


def test_shared_destination_without_bad_signals_is_not_contested():
    # A later claimant with no independent bad signals is presumed to be
    # the same operator; the graded model must not accuse them.
    omap = collisions.build_ownership_map(claims_fixture())
    verdict = collisions.check_destination(
        "JHON12091986", ADDR, omap, known_owners={}, bad_signal_handles=set()
    )
    assert verdict.status == collisions.SHARED_DESTINATION


def test_contested_claim_jhon_claims_jjb9707_address_with_bad_signals():
    # The real incident: JHON12091986 claims RTC921ffc..., first declared
    # by jjb9707 four weeks earlier, while JHON's other claims were
    # provably fabricated.
    omap = collisions.build_ownership_map(claims_fixture())
    verdict = collisions.check_destination(
        "JHON12091986",
        ADDR,
        omap,
        known_owners={},
        bad_signal_handles={"JHON12091986"},
    )
    assert verdict.status == collisions.CONTESTED_CLAIM
    assert verdict.disposition == "hold_for_review"
    assert "jjb9707" in verdict.reason
    assert verdict.evidence["first_declarer"] == "jjb9707"
    # First-declaration ordering is preserved and exposed.
    assert verdict.evidence["claims"][0]["handle"] == "jjb9707"


def test_first_declarer_with_bad_signals_is_not_contested_by_own_claim():
    omap = collisions.build_ownership_map(claims_fixture())
    verdict = collisions.check_destination(
        "jjb9707", ADDR, omap, known_owners={}, bad_signal_handles={"jjb9707"}
    )
    assert verdict.status == collisions.SHARED_DESTINATION


def test_injected_wallet_in_third_party_pr_thread_blocks():
    # The FlintLeng incident shape: a bot posts an address under a
    # "Claim Address" header in someone else's PR thread. The commenter is
    # neither the PR author nor a known owner of the address.
    omap = collisions.build_ownership_map([])
    verdict = collisions.check_destination(
        handle="real-contributor",
        address=ADDR,
        ownership_map=omap,
        known_owners={},
        comment_author="FlintLeng-bot",
        pr_author="real-contributor",
    )
    assert verdict.status == collisions.INJECTED
    assert verdict.disposition == "block"
    assert "FlintLeng-bot" in verdict.reason


def test_owner_posting_their_own_address_on_own_pr_is_not_injected():
    known = {ADDR: "real-contributor"}
    omap = collisions.build_ownership_map(
        [{"handle": "real-contributor", "address": ADDR, "source_url": "", "timestamp": 1}]
    )
    verdict = collisions.check_destination(
        "real-contributor",
        ADDR,
        omap,
        known_owners=known,
        comment_author="real-contributor",
        pr_author="real-contributor",
    )
    assert verdict.status == collisions.SAFE


def test_bot_posting_its_own_address_on_others_pr_is_foreign_owner():
    # FlintLeng variant where the address IS the bot's own registered
    # wallet: injection gate does not fire (commenter owns the address),
    # but paying the PR author's bounty there is still wrong.
    known = {ADDR: "FlintLeng-bot"}
    omap = collisions.build_ownership_map([])
    verdict = collisions.check_destination(
        handle="real-contributor",
        address=ADDR,
        ownership_map=omap,
        known_owners=known,
        comment_author="FlintLeng-bot",
        pr_author="real-contributor",
    )
    assert verdict.status == collisions.FOREIGN_OWNER
    assert verdict.disposition == "hold_for_review"


def test_foreign_owner_when_payee_never_declared_registry_address():
    known = {ADDR: "someone-else"}
    verdict = collisions.check_destination(
        "payee", ADDR, collisions.build_ownership_map([]), known_owners=known
    )
    assert verdict.status == collisions.FOREIGN_OWNER


def test_registry_owner_paying_themselves_is_safe():
    known = {ADDR: "payee"}
    verdict = collisions.check_destination(
        "payee", ADDR, collisions.build_ownership_map([]), known_owners=known
    )
    assert verdict.status == collisions.SAFE
    assert verdict.disposition == "proceed"
