"""CLI exit-code regression tests.

Failure mode 0, the meta one: a monitoring tool that exits 0 when its
check fails is itself a bug we shipped. Exit codes: 0 clean, 1 findings,
2 tool error - and these tests hold the contract.
"""

import json

from rtc_payout_guard import cli

VALID = "RTC" + "ab" * 20


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


# --- check-wallet ---------------------------------------------------------


def test_check_wallet_valid_exits_0(capsys):
    assert cli.main(["check-wallet", VALID]) == 0
    assert "valid RTC address" in capsys.readouterr().out


def test_check_wallet_miner_id_exits_0(capsys):
    assert cli.main(["check-wallet", "frozen-factorio-ryan"]) == 0


def test_check_wallet_evm_address_exits_1_not_0(capsys):
    # The regression: the tool must FAIL loudly on a bad wallet.
    assert cli.main(["check-wallet", "0x" + "ab" * 20]) == 1
    assert "FINDING" in capsys.readouterr().out


def test_check_wallet_json_output(capsys):
    assert cli.main(["check-wallet", VALID, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["classification"] == "hex_address"
    assert data["normalized"] == VALID


# --- verify-stars ---------------------------------------------------------


def test_verify_stars_fabricated_claim_exits_1(tmp_path, capsys):
    starred = _write(tmp_path, "starred.json", [])
    code = cli.main(
        ["verify-stars", "claimant", "--repos", "Scottcjn/Rustchain", "--starred-file", starred]
    )
    assert code == 1
    assert "FINDING" in capsys.readouterr().out


def test_verify_stars_genuine_claim_exits_0(tmp_path, capsys):
    starred = _write(tmp_path, "starred.json", ["Scottcjn/Rustchain"])
    code = cli.main(
        ["verify-stars", "honest", "--repos", "Scottcjn/Rustchain", "--starred-file", starred]
    )
    assert code == 0


# --- audit-amounts --------------------------------------------------------


def test_audit_amounts_mismatch_exits_1(tmp_path, capsys):
    issues = _write(
        tmp_path,
        "issues.json",
        [{"number": 165, "title": "[Bounty: 3 RTC] X", "body": "Pays 5 RTC."}],
    )
    assert cli.main(["audit-amounts", "--issues-file", issues]) == 1
    assert "#165" in capsys.readouterr().out


def test_audit_amounts_clean_exits_0(tmp_path):
    issues = _write(
        tmp_path,
        "issues.json",
        [{"number": 1, "title": "[Bounty: 5 RTC] X", "body": "Pays 5 RTC."}],
    )
    assert cli.main(["audit-amounts", "--issues-file", issues]) == 0


def test_missing_file_exits_2(tmp_path, capsys):
    assert cli.main(["audit-amounts", "--issues-file", str(tmp_path / "nope.json")]) == 2
    assert "error" in capsys.readouterr().err


def test_malformed_json_exits_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert cli.main(["audit-amounts", "--issues-file", str(bad)]) == 2


def test_unknown_subcommand_exits_2(capsys):
    assert cli.main(["frobnicate"]) == 2


# --- check-duplicates -----------------------------------------------------


def test_check_duplicates_auto_tier_double_pay_exits_1(tmp_path, capsys):
    rows = _write(
        tmp_path,
        "ledger.json",
        [
            {"dest": VALID, "amount_rtc": 5.0, "reason": "admin_transfer", "timestamp": 1000},
            {"dest": VALID, "amount_rtc": 25.0, "reason": "bounty:pr-142", "timestamp": 5000, "ref": "pr-142"},
        ],
    )
    assert cli.main(["check-duplicates", "--ledger-file", rows]) == 1
    assert "FINDING" in capsys.readouterr().out


def test_check_duplicates_clean_ledger_exits_0(tmp_path):
    rows = _write(
        tmp_path,
        "ledger.json",
        [{"dest": VALID, "amount_rtc": 5.0, "reason": "admin_transfer", "timestamp": 1000}],
    )
    assert cli.main(["check-duplicates", "--ledger-file", rows]) == 0


# --- scan-injection -------------------------------------------------------


def test_scan_injection_planted_address_exits_1(tmp_path, capsys):
    # FlintLeng shape: third party posts an address in someone else's PR.
    comments = _write(
        tmp_path,
        "comments.json",
        [{"author": "FlintLeng-bot", "body": f"Claim Address: {VALID}"}],
    )
    code = cli.main(
        ["scan-injection", "--comments-file", comments, "--pr-author", "real-contributor"]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "INJECTED" in out and "block" in out


def test_scan_injection_author_own_address_exits_0(tmp_path, capsys):
    comments = _write(
        tmp_path,
        "comments.json",
        [{"author": "real-contributor", "body": f"My wallet: {VALID}"}],
    )
    owners = _write(tmp_path, "owners.json", {VALID: "real-contributor"})
    code = cli.main(
        [
            "scan-injection",
            "--comments-file", comments,
            "--pr-author", "real-contributor",
            "--known-owners-file", owners,
        ]
    )
    assert code == 0


def test_scan_injection_shared_destination_is_note_not_finding(tmp_path, capsys):
    # Human + agent pair declaring one wallet: informational, exit 0.
    claims = _write(
        tmp_path,
        "claims.json",
        [
            {"handle": "Scottcjn", "address": VALID, "source_url": "u1", "timestamp": 1},
            {"handle": "real-contributor", "address": VALID, "source_url": "u2", "timestamp": 2},
        ],
    )
    comments = _write(
        tmp_path,
        "comments.json",
        [{"author": "Scottcjn", "body": f"Route my agent's pay here too: {VALID}"}],
    )
    code = cli.main(
        [
            "scan-injection",
            "--comments-file", comments,
            "--pr-author", "real-contributor",
            "--claims-file", claims,
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "SHARED_DESTINATION" in out
    assert "usually legitimate" in out
