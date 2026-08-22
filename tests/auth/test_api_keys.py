"""Tests for auth/api_keys.py -- per-athlete API key generation/hashing (Phase 5, ADR 0008)."""

from perseverer.auth.api_keys import generate_api_key, hash_api_key


def test_generated_keys_are_unique_and_high_entropy() -> None:
    keys = {generate_api_key() for _ in range(20)}
    assert len(keys) == 20
    assert all(len(k) >= 32 for k in keys)


def test_hash_is_deterministic_for_indexed_lookup() -> None:
    key = generate_api_key()
    assert hash_api_key(key) == hash_api_key(key)


def test_different_keys_hash_differently() -> None:
    assert hash_api_key(generate_api_key()) != hash_api_key(generate_api_key())
