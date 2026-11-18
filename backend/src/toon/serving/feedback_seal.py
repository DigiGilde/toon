"""Sealing for the tokens of site feedback: the page token in the widget's
config, the consent nonce in Toon's consent form and the mark in the cookie
that hides the button.

A sealed token sits where others can read it, a site's own scripts included,
so unlike `sessions.sign` it has to hide what it carries as well as prove it:
the page token names the visitor's content session. AES-256-GCM does both.
The token is base64url without padding over a 12-byte random nonce followed by
the ciphertext and its 16-byte tag. The plaintext is compact JSON, and the
purpose is the associated data, so a token sealed for one purpose never opens
as another.

The key is HKDF-SHA256 over TOON_SESSION_SECRET, without a salt and with an
info string of its own, so it changes with that secret and nothing else.
Random 96-bit nonces under one key stay safe up to 2^32 seals, far beyond what
one Toon mints between two changes of the secret.

Expiry is not sealing's concern: each payload carries its own `exp`, which
serving/feedback_token.py checks.
"""

from __future__ import annotations

import base64
import functools
import json
import os
import re
from typing import NoReturn

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PURPOSE_PAGE = b"toon-feedback-page-v1"
PURPOSE_CONSENT = b"toon-feedback-consent-v1"
PURPOSE_HIDE = b"toon-feedback-hide-v1"

# What unseal reads at most, of a value any request can hand in. Every token
# Toon mints stays well below it, and seal refuses one that would not.
MAX_TOKEN_LENGTH = 4096

_KEY_INFO = b"toon-feedback-v1"
_NONCE_LENGTH = 12
_TAG_LENGTH = 16
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")


@functools.lru_cache(maxsize=4)
def _cipher(secret: str) -> AESGCM:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_KEY_INFO).derive(secret.encode("utf-8"))
    return AESGCM(key)


def seal(secret: str, purpose: bytes, payload: dict[str, object]) -> str:
    """The payload as a token that only `unseal` with the same secret and
    purpose opens. Raises ValueError for a payload that is not strict JSON
    (NaN, infinity) or that would make a token too long to unseal."""
    plaintext = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("ascii")
    nonce = os.urandom(_NONCE_LENGTH)
    token = _encode(nonce + _cipher(secret).encrypt(nonce, plaintext, purpose))
    if len(token) > MAX_TOKEN_LENGTH:
        raise ValueError("the sealed payload is longer than unseal reads")
    return token


def unseal(secret: str, purpose: bytes, token: object) -> dict[str, object] | None:
    """The payload `seal` put in, or None for anything else: not a string, too
    long, not base64url, too short, tampered with, sealed with another secret
    or for another purpose, or not a JSON object."""
    if not isinstance(token, str) or len(token) > MAX_TOKEN_LENGTH:
        return None
    sealed = _decode(token)
    if sealed is None or len(sealed) < _NONCE_LENGTH + _TAG_LENGTH:
        return None
    try:
        plaintext = _cipher(secret).decrypt(sealed[:_NONCE_LENGTH], sealed[_NONCE_LENGTH:], purpose)
    except InvalidTag:
        return None
    try:
        payload = json.loads(plaintext.decode("utf-8"), parse_constant=_refuse_constant)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _refuse_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not JSON")


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(token: str) -> bytes | None:
    # Only the alphabet and the lengths a token is written in, and only its
    # one spelling: a base64 decoder skips foreign characters and ignores the
    # spare bits of the last character, which would give one token many.
    if not _BASE64URL.fullmatch(token) or len(token) % 4 == 1:
        return None
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    return raw if _encode(raw) == token else None


__all__ = [
    "MAX_TOKEN_LENGTH",
    "PURPOSE_CONSENT",
    "PURPOSE_HIDE",
    "PURPOSE_PAGE",
    "seal",
    "unseal",
]
