"""Per-athlete API key generation/hashing (Phase 5). Deliberately NOT PBKDF2 like
auth/passwords.py -- an API key is already a 256-bit random token (secrets.token_urlsafe), not
a low-entropy human password, so a direct SHA-256 hash gives the same "never store the
plaintext" property while allowing an O(1) indexed lookup (`WHERE api_key_hash = ?`) instead of
an O(n) linear scan with a constant-time compare per row. See docs/adr/0008-phase-5-frontend.md.
"""

from __future__ import annotations

import hashlib
import secrets


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
