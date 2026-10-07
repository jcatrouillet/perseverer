"""Tests for auth/passwords.py -- PBKDF2 password hashing."""

from perseverer.auth.passwords import hash_password, verify_password


def test_round_trip() -> None:
    encoded = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", encoded)


def test_wrong_password_rejected() -> None:
    encoded = hash_password("correct horse battery staple")
    assert not verify_password("wrong password", encoded)


def test_two_hashes_of_same_password_differ() -> None:
    """Random per-call salt -- confirms we're not accidentally hashing without one."""
    assert hash_password("same") != hash_password("same")


def test_malformed_encoded_hash_rejected_not_raised() -> None:
    assert not verify_password("anything", "not-a-valid-encoded-hash")
    assert not verify_password("anything", "")
