"""rtc-payout-guard: verification guards for RTC bounty payouts.

Every module here exists because a specific payout failure was observed
in production triage on 2026-07-25. See README.md for the failure-mode
to guard mapping. No module performs network I/O at import time; all
network access is injected.
"""

__version__ = "0.1.0"
