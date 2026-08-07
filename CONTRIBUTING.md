# Contributing to rtc-payout-guard

Thanks for helping make RTC bounty payouts safer. This tool exists because
real payout failures were found by hand; every guard here guards a failure
mode that actually happened. Please keep that grounding when contributing.

## Development setup

`rtc-payout-guard` is pure stdlib plus `requests`, with no network calls at
import time. That makes a local checkout trivial:

```bash
git clone https://github.com/Scottcjn/rtc-payout-guard.git
cd rtc-payout-guard
python3 -m venv .venv && . .venv/bin/activate
pip install -e .[dev]   # or: pip install requests && pip install pytest
```

## Running the tests

The whole suite runs offline — every network call is injected. From the
repo root:

```bash
python3 -m pytest -q
# or, if pytest is unavailable for a quick smoke check:
python3 -m tests.test_wallets
```

Tests must stay offline-capable. If you add a guard that needs live data,
inject the fetcher the way `verify_star_claim` and `record_snapshot` do;
never put a real network call on the test path.

## What makes a good contribution

A new guard is welcome only when it is grounded in an *observed* failure
mode, not a hypothetical one. When you add one:

1. **Name the failure mode** in the module docstring the way the existing
   guards do (the README table maps each numbered mode to its guard).
2. **Fail closed.** A guard that exits 0 when its check errors is itself a
   bug we have shipped before — mirror the exit-code contract in `cli.py`.
3. **Grade evidence, don't invent verdicts.** Prefer `SHARED_DESTINATION`
   over a binary fraud flag; the ecosystem expects multi-agent operators
   to share a payout wallet.
4. **Add a test** that reproduces the failure mode with injected data.

Documentation improvements (docstrings, README clarity, this file) are
always welcome and need no failure-mode justification.

## Pull requests

- One logical change per PR. A docs-only PR should not also reformat code.
- Keep the diff readable; match the existing code style (no formatter
  reflow unless the whole file is being touched).
- Make sure `python3 -m pytest -q` passes before requesting review.
- Reference the relevant failure-mode number from the README table in your
  PR description when the change touches a guard.

## Reporting a payout failure mode

If you find a *new* way a payout can go wrong, open an issue with:

- A concrete description of the failure (no speculation about motives).
- Why the existing guards do not catch it.
- The minimal input that reproduces it (redact wallet addresses).

We will triage it the way the original eight were triaged, then decide
whether it needs a new guard or an adjustment to an existing one.
