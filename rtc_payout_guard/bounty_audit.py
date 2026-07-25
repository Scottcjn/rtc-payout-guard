"""Detect title/body RTC amount mismatches in bounty issues.

Observed failure: bounty issues advertising different amounts in title
and body (#165 said 3 vs 5, #2308 said 17 vs 25). Contributors claim
against one number and get paid another.

Amount-extraction rules, chosen deliberately:
- Amounts are recognized in the forms ``N RTC``, ``N-M RTC`` (a range),
  and ``RTC N``. Each amount is modeled as a closed interval; a point
  value is a degenerate interval.
- A mismatch is reported only when BOTH title and body contain amounts
  and NO title interval overlaps ANY body interval. Multiple mentions in
  the body (tier tables, totals) are fine as long as one of them is
  consistent with the title.
- If either side mentions no amount there is nothing to compare, so no
  mismatch is reported. An issue advertising an amount only in the title
  is a style problem, not a payment hazard.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

Interval = Tuple[float, float]

_AMOUNT_BEFORE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*RTC\b"
    r"|(\d+(?:\.\d+)?)\s*RTC\b",
    re.IGNORECASE,
)
_AMOUNT_AFTER_RE = re.compile(r"\bRTC\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE)
# "RTC" immediately followed by 40 hex chars is an address, not an amount.
_ADDRESS_RE = re.compile(r"\bRTC[0-9a-fA-F]{40}\b")


def _amounts_in(text: str) -> List[Interval]:
    """All RTC amount intervals mentioned in ``text``, deduplicated, in order."""
    text = _ADDRESS_RE.sub(" ", text or "")
    found: List[Interval] = []
    for m in _AMOUNT_BEFORE_RE.finditer(text):
        if m.group(1) is not None:
            lo, hi = float(m.group(1)), float(m.group(2))
            found.append((min(lo, hi), max(lo, hi)))
        else:
            v = float(m.group(3))
            found.append((v, v))
    for m in _AMOUNT_AFTER_RE.finditer(text):
        v = float(m.group(1))
        found.append((v, v))
    return list(dict.fromkeys(found))


def extract_amounts(title: str, body: str) -> Tuple[List[Interval], List[Interval]]:
    """Amount intervals found in the title and in the body, respectively."""
    return _amounts_in(title), _amounts_in(body)


def _overlaps(a: Interval, b: Interval) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def find_amount_mismatch(issue: dict) -> Optional[dict]:
    """Report a title/body amount disagreement for one issue, or None.

    ``issue`` needs ``title`` and ``body``; ``number`` is carried through
    if present. Returns None when consistent or when either side has no
    amounts (see module docstring for the rules).
    """
    title_amounts, body_amounts = extract_amounts(
        issue.get("title", ""), issue.get("body", "")
    )
    if not title_amounts or not body_amounts:
        return None
    if any(_overlaps(t, b) for t in title_amounts for b in body_amounts):
        return None
    return {
        "number": issue.get("number"),
        "title": issue.get("title", ""),
        "title_amounts": title_amounts,
        "body_amounts": body_amounts,
        "detail": (
            "title advertises "
            + ", ".join(_fmt(i) for i in title_amounts)
            + " RTC but body says "
            + ", ".join(_fmt(i) for i in body_amounts)
            + " RTC"
        ),
    }


def _fmt(interval: Interval) -> str:
    lo, hi = interval
    def one(v: float) -> str:
        return str(int(v)) if v == int(v) else str(v)
    return one(lo) if lo == hi else f"{one(lo)}-{one(hi)}"
