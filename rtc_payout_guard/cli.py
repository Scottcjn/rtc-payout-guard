"""rtc-guard command line interface.

Exit codes are part of the contract and are tested:
    0  check ran and found nothing wrong
    1  check ran and FOUND something (invalid wallet, unverified claim,
       amount mismatch, duplicate, injected/contested destination)
    2  tool error (bad arguments, unreadable file, malformed JSON,
       network failure)

A monitoring tool that exits 0 when its check fails is itself a bug we
have shipped before; do not "simplify" the exit codes.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import bounty_audit, collisions, ledger, stars, wallets

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2


def _load_json(path: str):
    """Read a JSON file from disk and return the parsed object.

    Raises ``FileNotFoundError`` / ``json.JSONDecodeError`` on failure; the
    caller surfaces those as exit code 2 (tool error), not 0 or 1.
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _emit(result: dict, as_json: bool, lines: List[str]) -> None:
    """Print a check result either as pretty JSON or as human-readable lines.

    ``result`` is the full result dict (always emitted as JSON when
    ``--json`` is set); ``lines`` are the pre-formatted text lines for the
    non-JSON path. Exactly one of the two representations is printed.
    """
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for line in lines:
            print(line)


def _cmd_check_wallet(args) -> int:
    dest = args.destination
    kind = wallets.classify_destination(dest)
    normalized = wallets.normalize_address(dest)
    result = {"destination": dest, "classification": kind, "normalized": normalized}
    if kind == wallets.HEX_ADDRESS:
        lines = [f"OK: valid RTC address (canonical: {normalized})"]
        code = EXIT_CLEAN
    elif kind == wallets.MINER_ID_NAME:
        lines = [
            f"OK: bare miner-id name {dest!r} - a legitimate registered "
            "payout identity, not a hex address"
        ]
        code = EXIT_CLEAN
    else:
        lines = [
            f"FINDING: {dest!r} is not a valid RTC destination "
            "(canonical form is RTC + 40 lowercase hex chars)"
        ]
        code = EXIT_FINDINGS
    _emit(result, args.json, lines)
    return code


def _cmd_verify_stars(args) -> int:
    required = [r.strip() for r in args.repos.split(",") if r.strip()]
    if not required:
        print("error: --repos is empty", file=sys.stderr)
        return EXIT_ERROR
    if args.starred_file:
        starred_list = _load_json(args.starred_file)
        fetch = lambda _handle: starred_list  # noqa: E731 - trivial injection
    else:
        fetch = stars.github_fetch_starred
    result = stars.verify_star_claim(args.handle, required, fetch)
    lines = [
        f"handle: {result['handle']}",
        f"verified: {result['verified']}",
        f"starred ({len(result['starred'])}): {', '.join(result['starred']) or '-'}",
        f"missing ({len(result['missing'])}): {', '.join(result['missing']) or '-'}",
    ]
    if not result["verified"]:
        lines.append("FINDING: star claim is not backed by current GitHub state")
    _emit(result, args.json, lines)
    return EXIT_CLEAN if result["verified"] else EXIT_FINDINGS


def _cmd_audit_amounts(args) -> int:
    issues = _load_json(args.issues_file)
    mismatches = [
        m for m in (bounty_audit.find_amount_mismatch(i) for i in issues) if m
    ]
    result = {"issues_checked": len(issues), "mismatches": mismatches}
    lines = [f"checked {len(issues)} issues, {len(mismatches)} mismatches"]
    for m in mismatches:
        lines.append(f"FINDING: issue #{m['number']}: {m['detail']}")
    _emit(result, args.json, lines)
    return EXIT_FINDINGS if mismatches else EXIT_CLEAN


def _cmd_check_duplicates(args) -> int:
    rows = _load_json(args.ledger_file)
    manual_refs = args.manual_ref if args.manual_ref else None
    findings = ledger.detect_auto_tier_duplicates(
        rows, manual_refs=manual_refs, window_seconds=args.window_days * 86400
    )
    dest_dups = []
    if args.dest is not None and args.amount is not None:
        guard = ledger.PaymentGuard(lambda: rows)
        dest_dups = guard.check_duplicate(args.dest, args.amount, args.window_days)
    result = {"auto_tier_duplicates": findings, "destination_duplicates": dest_dups}
    lines = [f"auto-tier duplicates: {len(findings)}"]
    for f in findings:
        lines.append(
            f"FINDING: {f['dest']} paid by auto tier and manual bounty "
            f"{f['seconds_apart'] / 3600:.1f}h apart "
            f"(manual ref: {f['manual_row'].get('ref', '?')})"
        )
    if args.dest is not None and args.amount is not None:
        lines.append(f"prior identical payments to {args.dest}: {len(dest_dups)}")
        for row in dest_dups:
            lines.append(
                f"FINDING: {row.get('amount_rtc')} RTC already sent to "
                f"{args.dest} (reason: {row.get('reason', '?')})"
            )
    found = bool(findings or dest_dups)
    _emit(result, args.json, lines)
    return EXIT_FINDINGS if found else EXIT_CLEAN


def _cmd_scan_injection(args) -> int:
    comments = _load_json(args.comments_file)
    claims = _load_json(args.claims_file) if args.claims_file else []
    known_owners = _load_json(args.known_owners_file) if args.known_owners_file else {}
    bad_signals = set(_load_json(args.bad_signals_file)) if args.bad_signals_file else set()
    ownership = collisions.build_ownership_map(claims)

    verdicts = []
    for comment in comments:
        author = comment.get("author", "")
        for addr in wallets.extract_addresses(comment.get("body", "")):
            verdict = collisions.check_destination(
                handle=args.pr_author,
                address=addr,
                ownership_map=ownership,
                known_owners=known_owners,
                comment_author=author,
                pr_author=args.pr_author,
                bad_signal_handles=bad_signals,
            )
            verdicts.append(verdict)

    blocking = [v for v in verdicts if v.disposition in ("block", "hold_for_review")]
    notes = [v for v in verdicts if v.status == collisions.SHARED_DESTINATION]
    result = {"verdicts": [v.to_dict() for v in verdicts]}
    lines = [f"addresses scanned: {len(verdicts)}"]
    for v in verdicts:
        prefix = "FINDING" if v.disposition in ("block", "hold_for_review") else "note"
        lines.append(f"{prefix}: [{v.status}/{v.disposition}] {v.address}: {v.reason}")
    if not verdicts:
        lines.append("no RTC addresses found in thread")
    if notes and not blocking:
        lines.append(
            "shared payout destinations are expected and usually legitimate; "
            "no action needed"
        )
    _emit(result, args.json, lines)
    return EXIT_FINDINGS if blocking else EXIT_CLEAN


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser with one subcommand per guard.

    Each subcommand binds to an ``_cmd_*`` handler and returns the guard's
    exit code (0 clean / 1 finding / 2 error). The parser is built fresh on
    every invocation so tests can pass isolated argv lists.
    """
    parser = argparse.ArgumentParser(
        prog="rtc-guard",
        description="Verification guards for RTC bounty payouts. "
        "Exit codes: 0 clean, 1 findings, 2 tool error.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check-wallet", help="validate and classify a payout destination")
    p.add_argument("destination")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_check_wallet)

    p = sub.add_parser("verify-stars", help="verify a star-bounty claim against GitHub")
    p.add_argument("handle")
    p.add_argument("--repos", required=True, help="comma-separated owner/repo list")
    p.add_argument(
        "--starred-file",
        help="JSON list of owner/repo the handle stars (offline mode); "
        "omitted = live GitHub API",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_verify_stars)

    p = sub.add_parser("audit-amounts", help="find title/body RTC amount mismatches")
    p.add_argument("--issues-file", required=True, help="JSON list of {number,title,body}")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_audit_amounts)

    p = sub.add_parser("check-duplicates", help="find double payments in a ledger export")
    p.add_argument("--ledger-file", required=True, help="JSON list of ledger rows")
    p.add_argument("--manual-ref", action="append", help="restrict manual rows to these refs")
    p.add_argument("--dest", help="also check a pending payment destination")
    p.add_argument("--amount", type=float, help="pending payment amount in RTC")
    p.add_argument("--window-days", type=int, default=7)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_check_duplicates)

    p = sub.add_parser(
        "scan-injection",
        help="scan a PR thread export for planted payout addresses",
    )
    p.add_argument("--comments-file", required=True, help="JSON list of {author,body}")
    p.add_argument("--pr-author", required=True)
    p.add_argument("--claims-file", help="JSON list of address claims")
    p.add_argument("--known-owners-file", help="JSON {address: handle}")
    p.add_argument("--bad-signals-file", help="JSON list of handles with known bad signals")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_scan_injection)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Parse argv, run the selected guard, and return its exit code.

    Preserves the exit-code contract (0 clean / 1 finding / 2 error) across
    argparse's own ``SystemExit`` and any runtime exception from a guard;
    usage errors become exit code 2 and ``--help`` becomes 0.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse exits 2 on usage errors and 0 on --help; keep the contract.
        return EXIT_ERROR if e.code not in (0, None) else EXIT_CLEAN
    try:
        return args.func(args)
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as e:  # network errors from requests, etc.
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_ERROR


def entrypoint() -> None:
    """Console-script wrapper so ``main`` stays testable as a function."""
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
