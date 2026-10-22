"""AES-GCM blobs for the token file. The key never lives in the repo."""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"\x01"
_NONCE_LEN = 12


def generate_key() -> str:
    """Return a new urlsafe-base64 32-byte key. The caller decides where it goes."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def parse_key(value: str) -> bytes:
    raw = value.strip()
    if not raw or raw == "your_base64_token_key_here":
        raise ValueError("TESLA_TOKEN_KEY is not set.")
    padded = raw + "=" * (-len(raw) % 4)
    try:
        key = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("TESLA_TOKEN_KEY is not valid base64.") from exc
    if len(key) != 32:
        raise ValueError("TESLA_TOKEN_KEY must decode to 32 bytes.")
    return key


def encrypt(key: bytes, plaintext: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("AES-GCM key must be 32 bytes.")
    nonce = os.urandom(_NONCE_LEN)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return _VERSION + nonce + ciphertext


def decrypt(key: bytes, blob: bytes) -> bytes:
    if len(blob) < 1 + _NONCE_LEN + 16 or blob[:1] != _VERSION:
        raise ValueError("Token file is not an encrypted blob this server can read.")
    nonce = blob[1 : 1 + _NONCE_LEN]
    ciphertext = blob[1 + _NONCE_LEN :]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise ValueError("Could not decrypt the token file. Check TESLA_TOKEN_KEY.") from exc
