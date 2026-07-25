"""RTC payout destination validation, extraction, and classification.

Canonical RTC address form: the literal prefix ``RTC`` followed by exactly
40 lowercase hex characters (the first 40 hex digits of SHA-256 of the
wallet's Ed25519 public key).

Normalization choice: an address whose 40-character body is valid hex but
contains uppercase letters is normalized to lowercase and then validated.
Hex is case-insensitive, so these are unambiguous; accepting them avoids
rejecting copy-paste artifacts. Every address this module *returns* is in
canonical lowercase form.

Rejected outright:
- EVM addresses (``0x`` + 40 hex) - a different chain, paying one loses funds.
- Base58 / Solana-looking strings.
- RTC-*suffixed* forms (``<40 hex>RTC``) - seen in the wild, not valid.
- Wrong-length hex bodies.
- A bare 40-hex string with no ``RTC`` prefix. This is probably an address
  body with the prefix stripped; we refuse to guess rather than pay it as a
  miner-id name.
"""

from __future__ import annotations

import re
from typing import List, Optional

HEX_ADDRESS = "hex_address"
MINER_ID_NAME = "miner_id_name"
INVALID = "invalid"

_CANONICAL_RE = re.compile(r"^RTC[0-9a-f]{40}$")
_HEX_BODY_RE = re.compile(r"^RTC([0-9a-fA-F]{40})$")
# Word boundary on both sides so "RTC<41 hex>" or "<hex>RTC<hex>" is not
# extracted as a valid address. R/T/C are not hex digits, so an address can
# never start mid-way through another hex run.
_EXTRACT_RE = re.compile(r"\bRTC[0-9a-fA-F]{40}\b")
_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_RTC_SUFFIX_RE = re.compile(r"^[0-9a-fA-F]{40}RTC$")
_BARE_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
# A malformed attempt at an RTC address: RTC prefix plus a long hex run of
# the wrong length. Distinguished from short names like "rtc-fan-club".
_RTC_MALFORMED_RE = re.compile(r"^RTC[0-9a-fA-F]{20,}$", re.IGNORECASE)
# Base58 alphabet (no 0, O, I, l), typical foreign-chain address lengths.
_BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
# Registered payout identities: miner ids and wallet names like
# "frozen-factorio-ryan" or "founder_dev_fund".
_MINER_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{1,63}$")


def normalize_address(s: str) -> Optional[str]:
    """Return the canonical lowercase form of an RTC address, or None.

    Accepts uppercase hex in the body (normalized down); everything else
    that is not already canonical returns None.
    """
    s = s.strip()
    m = _HEX_BODY_RE.match(s)
    if not m:
        return None
    return "RTC" + m.group(1).lower()


def is_valid_rtc_address(s: str) -> bool:
    """True if ``s`` is an RTC address (after case normalization of the body)."""
    return normalize_address(s) is not None


def extract_addresses(text: str) -> List[str]:
    """Return all RTC addresses found in free text, canonicalized, in order.

    Duplicates (after normalization) are collapsed to the first occurrence.
    """
    seen = set()
    out: List[str] = []
    for match in _EXTRACT_RE.findall(text):
        addr = normalize_address(match)
        if addr is not None and addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def classify_destination(s: str) -> str:
    """Classify a payout destination string.

    Returns one of:
    - ``hex_address``: a valid RTC address (possibly after case normalization).
    - ``miner_id_name``: a bare registered payout identity. These ARE
      legitimate destinations in this system (e.g. ``frozen-factorio-ryan``,
      ``founder_dev_fund``), so they are labeled, not rejected.
    - ``invalid``: anything address-shaped but wrong - EVM ``0x`` forms,
      base58/foreign-chain strings, RTC-suffixed hex, wrong-length RTC
      bodies, or a bare 40-hex string missing its prefix.

    Ambiguity rules, documented deliberately:
    - Bare hex of exactly 40 chars is treated as ``invalid`` (an address
      body with the prefix stripped), never as a name.
    - Bare hex of other lengths (e.g. truncated miner-id hashes seen in the
      ledger) is treated as ``miner_id_name``.
    - A base58-looking run of 32-44 chars with no separators is treated as
      ``invalid`` (foreign-chain address), because no registered identity in
      this system looks like that.
    """
    s = s.strip()
    if not s:
        return INVALID
    if is_valid_rtc_address(s):
        return HEX_ADDRESS
    if _EVM_RE.match(s):
        return INVALID
    if _RTC_SUFFIX_RE.match(s):
        return INVALID
    if _RTC_MALFORMED_RE.match(s):
        return INVALID
    if _BARE_HEX_RE.match(s):
        return INVALID if len(s) == 40 else MINER_ID_NAME
    if _BASE58_RE.match(s) and any(c.isdigit() for c in s) and s.lower() != s:
        return INVALID
    if _MINER_ID_RE.match(s):
        return MINER_ID_NAME
    return INVALID
