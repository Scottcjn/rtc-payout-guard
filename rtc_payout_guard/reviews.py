"""Review-bounty qualification: three gates, honestly scoped.

Gate 1 (mechanical): the review state must be APPROVED or
CHANGES_REQUESTED. COMMENTED reviews and plain issue comments never
qualify.

Gate 2 (mechanical): the target PR must be MERGED. Reviews on open,
closed-unmerged, or draft PRs never qualify, and bot dependency bumps
never qualify regardless of merge state.

Gate 3 (ADVISORY): the review must name a specific defect in the diff.
:func:`looks_generic` catches known farming shapes - supply-chain hygiene
boilerplate, restatements of the PR description, template placeholders -
but it is a heuristic. It cannot judge whether an insight is real or
novel. A review that passes gate 3 here still needs a human to confirm
the defect exists; a review that fails it deserves a human glance before
final rejection. The verdict exposes each gate separately so the human
sees exactly which one failed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

QUALIFYING_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED"})

# Phrases that indicate supply-chain hygiene boilerplate rather than a
# defect in the diff. Any one alone is fine inside a real review; a body
# consisting mostly of these is farming.
_HYGIENE_PATTERNS = [
    r"\bpin (?:the |to )?(?:a )?(?:commit )?sha\b",
    r"\bconfirm (?:the )?cve(?:s| floor)?\b",
    r"\bcheck (?:the )?cve\b",
    r"\blink (?:the |to )?(?:a )?changelog\b",
    r"\breview (?:the )?(?:release notes|changelog)\b",
    r"\bverify (?:the )?(?:package )?integrity\b",
    r"\block ?file\b",
]
_HYGIENE_RES = [re.compile(p, re.IGNORECASE) for p in _HYGIENE_PATTERNS]

# Literal template artifacts observed in farmed reviews.
_PLACEHOLDER_MARKERS = ("[INSERT", "[ID_DEL_", "xxxxx", "YOUR_")

_BOT_BUMP_PATTERNS = [
    # Conventional-commit prefix requires the colon, so a legitimate title
    # like "Fix bump-map allocator overflow" is not misread as a bump.
    r"^(?:chore|build|fix|deps)\s*(?:\([^)]*\))?\s*:\s*bump\b",
    r"^bump\s+\S+\s+from\s+\S+\s+to\s+\S+",
    r"^update\s+(?:dependency|dependencies)\b",
    r"\bdependabot\b",
    r"\brenovate\b",
]
_BOT_BUMP_RES = [re.compile(p, re.IGNORECASE) for p in _BOT_BUMP_PATTERNS]

_WORD_RE = re.compile(r"[a-z0-9']+")


@dataclass
class ReviewVerdict:
    """Per-gate result for one review-bounty claim."""

    qualifies: bool
    gates: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"qualifies": self.qualifies, "gates": self.gates, "notes": self.notes}


def is_bot_dependency_bump(pr_title: str) -> bool:
    """True for dependabot/renovate-style dependency bump PR titles."""
    title = (pr_title or "").strip()
    return any(rx.search(title) for rx in _BOT_BUMP_RES)


def _tokens(text: str) -> set:
    return set(_WORD_RE.findall((text or "").lower()))


def looks_generic(body: str, pr_description: Optional[str] = None) -> bool:
    """Heuristic: does this review body fail to name a specific defect?

    Flags, in order of confidence:
    - empty or near-empty bodies;
    - template placeholders (``[INSERT``, ``[ID_DEL_``, ``xxxxx``, ``YOUR_``);
    - bodies whose sentences are mostly supply-chain hygiene boilerplate
      ("pin the SHA", "confirm the CVE floor", "link the changelog");
    - bodies that are largely a restatement of the PR description
      (>=80% of the review's words already appear in the description,
      only checked when the review has enough words to make that ratio
      meaningful).

    This is advisory. It cannot recognize a genuinely novel defect report,
    only the known shapes of not-one.
    """
    text = (body or "").strip()
    if len(text) < 20:
        return True
    if any(marker in text for marker in _PLACEHOLDER_MARKERS):
        return True

    sentences = [s.strip() for s in re.split(r"[.!?\n]+", text) if s.strip()]
    if sentences:
        hygiene = sum(
            1 for s in sentences if any(rx.search(s) for rx in _HYGIENE_RES)
        )
        if hygiene / len(sentences) >= 0.5:
            return True

    if pr_description:
        body_words = _tokens(text)
        if len(body_words) >= 8:
            desc_words = _tokens(pr_description)
            overlap = len(body_words & desc_words) / len(body_words)
            if overlap >= 0.8:
                return True

    return False


def qualifies_for_review_bounty(review: dict) -> ReviewVerdict:
    """Apply the three gates to one review-bounty claim.

    ``review`` keys:
        state: GitHub review state ("APPROVED", "CHANGES_REQUESTED",
            "COMMENTED", ...). Plain issue comments have no review state
            and should be passed as "COMMENTED" or omitted.
        pr_merged: bool, whether the target PR is merged.
        pr_title: used for the bot-bump exclusion.
        body: the review text.
        pr_description: optional, enables the restatement heuristic.

    Gates 1 and 2 are mechanical facts. Gate 3 (``names_defect``) is the
    advisory :func:`looks_generic` heuristic and needs human confirmation
    either way; the notes say so whenever it decides the outcome.
    """
    state = (review.get("state") or "").upper()
    gate_state = state in QUALIFYING_STATES
    gate_merged = bool(review.get("pr_merged"))
    gate_not_bot_bump = not is_bot_dependency_bump(review.get("pr_title", ""))
    generic = looks_generic(review.get("body", ""), review.get("pr_description"))
    gate_defect = not generic

    gates = {
        "state_is_approve_or_changes_requested": gate_state,
        "pr_is_merged": gate_merged,
        "not_a_bot_dependency_bump": gate_not_bot_bump,
        "names_specific_defect_advisory": gate_defect,
    }
    notes = []
    if not gate_state:
        notes.append(f"review state {state or 'MISSING'!s} does not qualify")
    if not gate_merged:
        notes.append("target PR is not merged")
    if not gate_not_bot_bump:
        notes.append("bot dependency bumps never qualify for review bounties")
    if gate_state and gate_merged and gate_not_bot_bump:
        if gate_defect:
            notes.append(
                "gate 3 passed heuristically; a human must still confirm the "
                "named defect is real before paying"
            )
        else:
            notes.append(
                "gate 3 (advisory) flagged the body as generic; have a human "
                "glance before final rejection"
            )

    return ReviewVerdict(qualifies=all(gates.values()), gates=gates, notes=notes)
