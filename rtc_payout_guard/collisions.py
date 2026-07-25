"""Shared payout destinations, contested claims, and injected wallets.

Design note (this matters, read it before "fixing" anything here):

In this ecosystem a single operator legitimately runs a human account plus
one or more agent identities and routes them all to one payout wallet.
That is the normal shape of an agent economy, not fraud. A shared address
is therefore AMBIGUOUS EVIDENCE, never a verdict on its own. This module
grades the evidence instead of shouting "collision":

- ``SAFE``: nothing notable.
- ``SHARED_DESTINATION``: 2+ handles declared the same address and nothing
  else is wrong. Common, usually legitimate (human + agent pair).
  Disposition: proceed, note it. The verdict carries the full claim list
  so a human can judge quickly (who declared first, when, from where).
- ``FOREIGN_OWNER``: the authoritative owner registry says the address
  belongs to a different handle and the payee never declared it
  themselves. Disposition: hold for human review.
- ``CONTESTED_CLAIM``: the payee claims an address FIRST declared by a
  demonstrably different party, and the payee independently shows bad
  signals (fabricated claims elsewhere, template placeholders, claiming
  other people's PRs). First-declaration timestamp is the key
  discriminator; ordering is preserved and exposed. Disposition: hold.
- ``INJECTED``: the address appears in a comment authored by someone who
  is neither the PR author nor the address owner - the classic planted
  "Claim Address" header in a third party's PR thread. This one IS
  unambiguous. Disposition: block; a scraper would pay the commenter
  instead of the author.

Only ``INJECTED`` is a hard stop by default. Treating shared destinations
as fraud would punish exactly the multi-agent operators this ecosystem
exists to attract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Set

from . import wallets

SAFE = "SAFE"
SHARED_DESTINATION = "SHARED_DESTINATION"
FOREIGN_OWNER = "FOREIGN_OWNER"
CONTESTED_CLAIM = "CONTESTED_CLAIM"
INJECTED = "INJECTED"

# Default disposition per status. INJECTED is the only hard stop.
DISPOSITIONS = {
    SAFE: "proceed",
    SHARED_DESTINATION: "proceed_with_note",
    FOREIGN_OWNER: "hold_for_review",
    CONTESTED_CLAIM: "hold_for_review",
    INJECTED: "block",
}


@dataclass(frozen=True)
class Claim:
    """A public declaration that ``handle`` wants payouts sent to ``address``."""

    handle: str
    address: str
    source_url: str
    timestamp: float


@dataclass
class Verdict:
    """Outcome of checking one (handle, address) payout intent."""

    status: str
    handle: str
    address: str
    reason: str
    disposition: str = ""
    # Supporting context for the human reviewer: full claim history for the
    # address (dicts, timestamp-sorted), first declarer, registry owner.
    evidence: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.disposition:
            self.disposition = DISPOSITIONS[self.status]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "disposition": self.disposition,
            "handle": self.handle,
            "address": self.address,
            "reason": self.reason,
            "evidence": self.evidence,
        }


def _normalize(address: str) -> str:
    """Canonicalize an address; leave miner-id names untouched."""
    return wallets.normalize_address(address) or address.strip()


def _as_claim(c) -> Claim:
    if isinstance(c, Claim):
        return Claim(c.handle, _normalize(c.address), c.source_url, float(c.timestamp))
    return Claim(
        handle=c["handle"],
        address=_normalize(c["address"]),
        source_url=c.get("source_url", ""),
        timestamp=float(c["timestamp"]),
    )


def build_ownership_map(claims: Iterable) -> Dict[str, List[Claim]]:
    """Group claims by address, each list sorted by timestamp ascending.

    Claims may be ``Claim`` instances or dicts with keys
    ``handle, address, source_url, timestamp``. Sorting by timestamp means
    ``map[addr][0].handle`` is always the first declarer - the key
    discriminator for contested claims.
    """
    out: Dict[str, List[Claim]] = {}
    for raw in claims:
        claim = _as_claim(raw)
        out.setdefault(claim.address, []).append(claim)
    for addr in out:
        out[addr].sort(key=lambda c: c.timestamp)
    return out


def find_shared_destinations(
    ownership_map: Mapping[str, List[Claim]],
) -> Dict[str, List[Claim]]:
    """Addresses declared by 2+ distinct handles, claims timestamp-sorted.

    The first entry in each list is the first declarer. A shared
    destination is evidence to weigh, not wrongdoing; see the module
    docstring.
    """
    return {
        addr: claims
        for addr, claims in ownership_map.items()
        if len({c.handle for c in claims}) >= 2
    }


# Kept for callers written against the original spec name. Same semantics.
find_collisions = find_shared_destinations


def check_destination(
    handle: str,
    address: str,
    ownership_map: Mapping[str, List[Claim]],
    known_owners: Mapping[str, str],
    *,
    comment_author: Optional[str] = None,
    pr_author: Optional[str] = None,
    bad_signal_handles: Optional[Set[str]] = None,
) -> Verdict:
    """Grade the evidence for paying ``handle`` at ``address``.

    Args:
        handle: the identity about to be paid.
        address: proposed payout destination (address or miner-id name).
        ownership_map: from :func:`build_ownership_map`.
        known_owners: authoritative registry ``{address: handle}``.
        comment_author: who authored the comment the address was scraped
            from, if it was scraped from a thread. Required (with
            ``pr_author``) for injection detection.
        pr_author: author of the PR the comment sits on.
        bad_signal_handles: handles with independently established bad
            signals (fabricated star claims, template placeholders,
            claiming others' PRs). This is the discriminator that turns a
            shared destination into a contested claim; without it a later
            claimant is presumed to be the same operator.

    Precedence: INJECTED > CONTESTED_CLAIM > FOREIGN_OWNER >
    SHARED_DESTINATION > SAFE. Statuses and default dispositions are
    described in the module docstring.
    """
    address = _normalize(address)
    bad_signal_handles = bad_signal_handles or set()
    claims = list(ownership_map.get(address, []))
    claimants = [c.handle for c in claims]
    distinct_handles = list(dict.fromkeys(claimants))
    first_declarer = claimants[0] if claimants else None
    registry_owner = known_owners.get(address)
    owners = {o for o in (registry_owner, first_declarer) if o is not None}

    evidence = {
        "registry_owner": registry_owner,
        "first_declarer": first_declarer,
        "claims": [
            {
                "handle": c.handle,
                "timestamp": c.timestamp,
                "source_url": c.source_url,
            }
            for c in claims
        ],
    }

    if (
        comment_author is not None
        and pr_author is not None
        and comment_author != pr_author
        and comment_author not in owners
    ):
        return Verdict(
            status=INJECTED,
            handle=handle,
            address=address,
            reason=(
                f"address was posted by {comment_author!r}, who is neither the "
                f"PR author ({pr_author!r}) nor a known owner of this address; "
                "a scraper following this comment would pay the commenter"
            ),
            evidence=evidence,
        )

    if (
        first_declarer is not None
        and first_declarer != handle
        and handle in bad_signal_handles
    ):
        return Verdict(
            status=CONTESTED_CLAIM,
            handle=handle,
            address=address,
            reason=(
                f"{handle!r} claims an address first declared by "
                f"{first_declarer!r} at {claims[0].timestamp} "
                f"({claims[0].source_url or 'no source'}), and {handle!r} has "
                "independent bad signals; hold for human review"
            ),
            evidence=evidence,
        )

    if (
        registry_owner is not None
        and registry_owner != handle
        and handle not in claimants
    ):
        return Verdict(
            status=FOREIGN_OWNER,
            handle=handle,
            address=address,
            reason=(
                f"registry says this address belongs to {registry_owner!r} and "
                f"{handle!r} never declared it; hold for human review"
            ),
            evidence=evidence,
        )

    if len(distinct_handles) >= 2:
        return Verdict(
            status=SHARED_DESTINATION,
            handle=handle,
            address=address,
            reason=(
                "shared payout destination: declared by "
                f"{', '.join(repr(h) for h in distinct_handles)} (first: "
                f"{first_declarer!r}). Usually one operator running a human "
                "account plus agents; proceed and note it. Reviewer context: "
                "check for overlapping activity, merged work under both "
                "handles, consistent declarations, plausible account ages"
            ),
            evidence=evidence,
        )

    return Verdict(
        status=SAFE,
        handle=handle,
        address=address,
        reason="no shared declarations, no ownership conflict, no injection signal",
        evidence=evidence,
    )
