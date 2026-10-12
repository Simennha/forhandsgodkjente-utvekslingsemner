"""Small crypto helpers: credential hashing, Ed25519 key encoding, random tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def random_token(nbytes: int = 32) -> str:
    return b64e(secrets.token_bytes(nbytes))


def hash_credential(code: str, salt: bytes, pepper: bytes = b"") -> bytes:
    """scrypt(HMAC(pepper, normalized code)). Short PINs are only safe with the pepper,
    which lives in a different secret than the hash."""
    normalized = " ".join(code.strip().lower().split())
    keyed = (
        hmac.new(pepper, normalized.encode(), hashlib.sha256).digest()
        if pepper
        else normalized.encode()
    )
    return hashlib.scrypt(keyed, salt=salt, n=2**14, r=8, p=1, dklen=32)


def credential_matches(code: str, salt: bytes, expected: bytes, pepper: bytes = b"") -> bool:
    return hmac.compare_digest(hash_credential(code, salt, pepper), expected)


def ed25519_private_to_str(key: Ed25519PrivateKey) -> str:
    return b64e(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))


def ed25519_public_to_str(key: Ed25519PublicKey) -> str:
    return b64e(key.public_bytes(Encoding.Raw, PublicFormat.Raw))


def ed25519_private_from_str(text: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(b64d(text))


def ed25519_public_from_str(text: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(b64d(text))


def new_ed25519() -> tuple[str, str]:
    key = Ed25519PrivateKey.generate()
    return ed25519_private_to_str(key), ed25519_public_to_str(key.public_key())
