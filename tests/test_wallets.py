"""Wallet validation regression tests.

Failure modes covered: contributors submitting EVM addresses as RTC
wallets, RTC-suffixed forms, prefix-stripped bodies, and address
extraction from claim comments.
"""

from rtc_payout_guard import wallets

VALID = "RTC" + "a1b2c3d4e5" * 4  # 40 lowercase hex chars


def test_canonical_address_is_valid():
    assert wallets.is_valid_rtc_address(VALID)


def test_uppercase_hex_body_is_normalized_then_validated():
    upper = "RTC" + ("A1B2C3D4E5" * 4)
    assert wallets.is_valid_rtc_address(upper)
    assert wallets.normalize_address(upper) == VALID


def test_evm_address_submitted_as_rtc_wallet_rejected():
    # Observed: contributors paste MetaMask addresses into RTC claim fields.
    evm = "0x" + "ab" * 20
    assert not wallets.is_valid_rtc_address(evm)
    assert wallets.classify_destination(evm) == wallets.INVALID


def test_solana_looking_base58_rejected():
    sol = "7EYnhQoR9YM3N7UoaKRoA44Uy8JeaZV3qyouov87awMs"
    assert wallets.classify_destination(sol) == wallets.INVALID


def test_rtc_suffixed_form_rejected():
    # Observed in the wild: "<40 hex>RTC" instead of "RTC<40 hex>".
    suffixed = ("a1b2c3d4e5" * 4) + "RTC"
    assert not wallets.is_valid_rtc_address(suffixed)
    assert wallets.classify_destination(suffixed) == wallets.INVALID


def test_wrong_length_bodies_rejected():
    assert not wallets.is_valid_rtc_address("RTC" + "a" * 39)
    assert not wallets.is_valid_rtc_address("RTC" + "a" * 41)
    assert wallets.classify_destination("RTC" + "a" * 41) == wallets.INVALID


def test_bare_40_hex_without_prefix_is_invalid_not_a_name():
    assert wallets.classify_destination("a1b2c3d4e5" * 4) == wallets.INVALID


def test_miner_id_names_are_legitimate_destinations():
    # Bare names ARE registered payout identities in this system.
    for name in ("frozen-factorio-ryan", "founder_dev_fund", "sophia-nas-c4130"):
        assert wallets.classify_destination(name) == wallets.MINER_ID_NAME


def test_long_bare_hex_miner_hash_is_a_name_not_an_address():
    # Ledger contains 64-hex miner ids; only exactly-40 hex is ambiguous.
    assert wallets.classify_destination("886c11d07cf87bc5" * 4) == wallets.MINER_ID_NAME


def test_empty_and_garbage_are_invalid():
    assert wallets.classify_destination("") == wallets.INVALID
    assert wallets.classify_destination("   ") == wallets.INVALID
    assert wallets.classify_destination("!!!") == wallets.INVALID


def test_extract_addresses_from_claim_comment():
    found = wallets.extract_addresses(f"Claim Address: {VALID} and RTC{'F' * 40}")
    assert found == [VALID, "RTC" + "f" * 40]


def test_extract_does_not_match_overlong_hex_runs():
    assert wallets.extract_addresses("RTC" + "a" * 41) == []


def test_extract_dedupes_case_variants():
    upper = "RTC" + ("A1B2C3D4E5" * 4)
    assert wallets.extract_addresses(f"{VALID} {upper}") == [VALID]
