"""Duplicate-payment detection and idempotency keys for RTC payouts.

The observed failure: merging a PR triggers an auto-payout bot (flat
5 RTC, ledger reason ``admin_transfer``); a manual considered payment for
the same work lands on top and the contributor is paid twice. Four such
duplicates had to be voided by hand. These guards run BEFORE sending.

Ledger row shape (dicts):
    ``dest``       payout destination (address or miner-id name)
    ``amount_rtc`` float
    ``reason``     e.g. ``admin_transfer`` or ``bounty:<ref>``
    ``timestamp``  unix epoch seconds
    ``ref``        optional payment reference
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Callable, Iterable, List, Optional

AUTO_TIER_REASON = "admin_transfer"
MANUAL_REASON_PREFIX = "bounty:"
DEFAULT_DUPLICATE_WINDOW_S = 7 * 86400

_IDEMPOTENCY_ALLOWED_RE = re.compile(r"[^A-Za-z0-9._:\-]")
_IDEMPOTENCY_MAX_LEN = 128

LedgerReader = Callable[[], Iterable[dict]]


class PaymentGuard:
    """Pre-send checks against an injected ledger reader.

    ``ledger_reader`` is a zero-argument callable returning ledger rows
    (see module docstring). Injected so tests run offline and callers
    decide whether rows come from SQLite, an API, or a JSON export.
    """

    def __init__(self, ledger_reader: LedgerReader):
        self._read = ledger_reader

    def check_duplicate(
        self,
        dest: str,
        amount: float,
        lookback_days: int,
        now: Optional[float] = None,
    ) -> List[dict]:
        """Rows paying the same amount to the same destination recently.

        Returns matching rows (empty list means safe to send). Amount
        comparison uses a 1e-6 RTC tolerance to survive float round-trips.
        """
        now = time.time() if now is None else now
        cutoff = now - lookback_days * 86400
        return [
            row
            for row in self._read()
            if row.get("dest") == dest
            and abs(float(row.get("amount_rtc", 0)) - amount) < 1e-6
            and float(row.get("timestamp", 0)) >= cutoff
        ]


def detect_auto_tier_duplicates(
    rows: Iterable[dict],
    manual_refs: Optional[Iterable[str]] = None,
    window_seconds: int = DEFAULT_DUPLICATE_WINDOW_S,
) -> List[dict]:
    """Find auto-tier payouts that duplicate a manual bounty payment.

    A finding is an ``admin_transfer`` row and a ``bounty:*`` row to the
    same destination within ``window_seconds`` of each other. If
    ``manual_refs`` is given, only manual rows whose ``ref`` is in it are
    considered; otherwise every ``bounty:*`` row is.

    Returns findings as ``{"dest", "auto_row", "manual_row",
    "seconds_apart"}``, one per (auto, manual) pair, ordered by the auto
    row's timestamp.
    """
    refs = set(manual_refs) if manual_refs is not None else None
    rows = list(rows)
    autos = [r for r in rows if r.get("reason") == AUTO_TIER_REASON]
    manuals = [
        r
        for r in rows
        if str(r.get("reason", "")).startswith(MANUAL_REASON_PREFIX)
        and (refs is None or r.get("ref") in refs)
    ]
    findings = []
    for auto in autos:
        for manual in manuals:
            if auto.get("dest") != manual.get("dest"):
                continue
            gap = abs(float(auto.get("timestamp", 0)) - float(manual.get("timestamp", 0)))
            if gap <= window_seconds:
                findings.append(
                    {
                        "dest": auto.get("dest"),
                        "auto_row": auto,
                        "manual_row": manual,
                        "seconds_apart": gap,
                    }
                )
    findings.sort(key=lambda f: float(f["auto_row"].get("timestamp", 0)))
    return findings


def make_idempotency_key(handle: str, ref: str, batch: str) -> str:
    """Build a server-safe idempotency key from handle, ref, and batch id.

    The server accepts ``[A-Za-z0-9._:-]{1,128}``. Characters outside that
    set are replaced with ``-``. If the sanitized key exceeds 128 chars it
    is truncated and suffixed with a 12-hex digest of the ORIGINAL inputs,
    so distinct long inputs never sanitize to the same key.
    """
    raw = f"{handle}:{ref}:{batch}"
    key = _IDEMPOTENCY_ALLOWED_RE.sub("-", raw)
    if not key:
        key = "-"
    if len(key) > _IDEMPOTENCY_MAX_LEN:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        key = key[: _IDEMPOTENCY_MAX_LEN - 13] + ":" + digest
    return key
