# rtc-payout-guard

Verification guards for RTC bounty payouts. Built after a full manual
triage of the RustChain bounty program on 2026-07-25 surfaced eight
distinct payout failure modes with one root cause: claims and payout
destinations were trusted instead of verified. This tool verifies them.

Pure stdlib plus `requests`. No network calls at import time; every
network call is injectable, so the whole test suite runs offline.

## The failure modes and the guard for each

| # | Observed failure | Guard |
|---|---|---|
| 1 | **Wallet injection.** A bot posted its own RTC address under a "Claim Address" header inside PR-review comments on other people's PRs. Any scraper paying from thread text pays the wrong person. | `scan-injection` / `collisions.check_destination`. An address posted by someone who is neither the PR author nor the address owner is `INJECTED` and blocks. |
| 2 | **Shared payout destinations.** One address declared by 2+ handles (12+ pairs found). | `collisions.find_shared_destinations`. See "Shared destinations are usually legitimate" below; this is graded evidence, not a fraud verdict. |
| 3 | **Fabricated star claims.** 84 of 369 claimants who said "starred all repos" had zero stars. | `verify-stars` / `stars.verify_star_claim`. Checks the actual GitHub state, never the claim text. |
| 4 | **Un-starring after payment.** 50 handles claimed star bounties historically and show zero stars today. | Same guard; current state either backs the claim or it does not. |
| 5 | **Double payment.** Merge triggers an auto-payout bot (flat 5 RTC, reason `admin_transfer`); a manual bounty payment on top double-pays. Four duplicates were voided by hand. | `check-duplicates` / `ledger.detect_auto_tier_duplicates` plus `PaymentGuard.check_duplicate` before sending. |
| 6 | **Title/body amount mismatch.** #165 said 3 vs 5, #2308 said 17 vs 25; contributors claim one number and get paid another. | `audit-amounts` / `bounty_audit.find_amount_mismatch`. |
| 7 | **Review-bounty farming.** Polished reviews that name no defect, target unmerged PRs, or review bot dependency bumps. | `reviews.qualifies_for_review_bounty`: three gates with per-gate results. |
| 8 | **Stranded balances.** Earnings credited to a bare miner-id with no keypair; the contributor cannot sign a transfer out. | `check-wallet` / `wallets.classify_destination` labels bare names as `miner_id_name` so a human can confirm the recipient can actually spend from it before crediting. |

## Shared destinations are usually legitimate

In this ecosystem a single operator normally runs a human account plus
one or more agent identities and routes them all to one payout wallet.
That is the expected shape of an agent economy, not fraud. Treating a
shared address as a fraud signal would punish exactly the multi-agent
operators this ecosystem is built to attract.

So the destination check grades evidence instead of issuing a binary
verdict:

- `SHARED_DESTINATION` - 2+ handles, one address, nothing else wrong.
  The common, usually legitimate case. Disposition: proceed and note it.
  The verdict carries the full timestamp-sorted claim history so a
  reviewer can quickly check for overlapping activity, merged work under
  both handles, consistent declarations, and plausible account ages.
- `CONTESTED_CLAIM` - a handle claims an address first declared by a
  demonstrably different party, and the later claimant independently
  shows bad signals (fabricated claims elsewhere, template placeholders,
  claiming other people's PRs). First-declaration timestamp is the key
  discriminator and is always preserved and exposed. Disposition: hold
  for human review.
- `INJECTED` - the address appears in a comment authored by someone who
  is neither the PR author nor the address owner. This one is
  unambiguous. Disposition: block.

Only `INJECTED` is a hard stop by default.

## Install

```bash
pip install .
```

## CLI

```bash
rtc-guard check-wallet RTC921ffc...            # validate/classify a destination
rtc-guard verify-stars somehandle --repos Scottcjn/Rustchain,Scottcjn/bottube
rtc-guard audit-amounts --issues-file issues.json
rtc-guard check-duplicates --ledger-file ledger.json --dest RTC921ffc... --amount 25
rtc-guard scan-injection --comments-file thread.json --pr-author realdev \
    --claims-file claims.json --known-owners-file owners.json
```

Every subcommand takes `--json` for machine-readable output.

Exit codes are part of the contract: `0` the check ran and found nothing
wrong, `1` the check ran and found something, `2` the tool itself failed
(bad file, malformed JSON, network error). A monitor that exits 0 when
its check fails is a bug; the test suite pins these codes.

`verify-stars` talks to the public GitHub API unless you pass
`--starred-file`; everything else is fully offline.

## What this tool cannot do

Stated plainly, because pretending otherwise is how payouts go wrong:

- It cannot judge whether a review's insight is real or novel. Gate 3
  (`looks_generic`) only recognizes known farming shapes: boilerplate
  supply-chain hygiene, restated PR descriptions, template placeholders.
  A review that passes still needs a human to confirm the defect exists.
- It cannot tell never-starred from starred-then-unstarred. GitHub stars
  are public, so zero stars today is solid evidence the claim is not
  currently backed, but distinguishing fabrication from post-payment
  un-starring requires payment-time records.
- It cannot verify off-GitHub social claims (Discord activity, X posts,
  "I told my friends about RustChain").
- It cannot prove two handles are or are not the same operator. The
  shared-destination evidence helps a human decide; contested claims
  genuinely need a human every time.
- It cannot confirm a miner-id destination has a keypair behind it; it
  can only flag that the destination is a name, so someone checks before
  crediting a balance the recipient cannot move.

## Development

```bash
pip install -e .[dev]
pytest
```

## License

AGPL-3.0-or-later. Copyright (C) 2026 Elyan Labs LLC.
