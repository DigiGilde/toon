"""Tests for serving/feedback_seal.py: every way unseal refuses a token, the
format spelled out against the spec, and what seal will not write. No database
needed."""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from toon.serving import feedback_seal
from toon.serving.feedback_seal import MAX_TOKEN_LENGTH, PURPOSE_CONSENT, PURPOSE_HIDE, PURPOSE_PAGE, seal, unseal

SECRET = "sessie-geheim-van-minstens-32-bytes!"
OTHER_SECRET = "ander-geheim-van-minstens-32-bytes!!"
PAYLOAD: dict[str, object] = {
    "v": 1,
    "site": "6f1c2a52-3c1e-4c55-9d0e-8a4b1f0e2d3c",
    "ses": None,
    "exp": 1_790_000_000,
}
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
PURPOSES = [PURPOSE_PAGE, PURPOSE_CONSENT, PURPOSE_HIDE]


def _key(secret: str) -> bytes:
    """The key as spec §16.5 defines it, derived here without the
    module."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"toon-feedback-v1").derive(
        secret.encode("utf-8")
    )


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(token: str) -> bytes:
    return base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))


def _hand_sealed(plaintext: bytes, *, purpose: bytes = PURPOSE_PAGE) -> str:
    """A token built from the format itself, for plaintexts seal would never
    write: only the holder of the key can make one, so each refusal below is
    about the content and not about the seal."""
    nonce = b"n" * 12
    return _b64(nonce + AESGCM(_key(SECRET)).encrypt(nonce, plaintext, purpose))


def _flip(token: str, index: int) -> str:
    raw = bytearray(_unb64(token))
    raw[index] ^= 0x01
    return _b64(bytes(raw))


class TestUnsealRefuses:
    @pytest.mark.parametrize("token", [None, b"bytes", 42, ["token"], {"token": "x"}])
    def test_anything_but_a_string(self, token):
        assert unseal(SECRET, PURPOSE_PAGE, token) is None

    def test_a_genuine_token_longer_than_the_maximum(self):
        """Refused for its length alone, before anything is decoded: the
        same plaintext opens by hand."""
        plaintext = json.dumps({"p": "x" * 3040}, separators=(",", ":")).encode("ascii")
        token = _hand_sealed(plaintext)
        assert len(token) > MAX_TOKEN_LENGTH
        raw = _unb64(token)
        assert AESGCM(_key(SECRET)).decrypt(raw[:12], raw[12:], PURPOSE_PAGE) == plaintext

        assert unseal(SECRET, PURPOSE_PAGE, token) is None

    @pytest.mark.parametrize(
        "spoil",
        [
            pytest.param(lambda token: token + "=" * (-len(token) % 4 or 4), id="padding"),
            pytest.param(lambda token: token.replace(token[5], "+", 1), id="plus"),
            pytest.param(lambda token: token.replace(token[5], "/", 1), id="slash"),
            pytest.param(lambda token: token[:10] + " " + token[11:], id="space"),
            pytest.param(lambda token: token[:10] + "!" + token[11:], id="punctuation"),
            pytest.param(lambda token: token[:10] + "é" + token[11:], id="non-ascii"),
            pytest.param(lambda token: token[:10] + "\x00" + token[11:], id="nul"),
            pytest.param(lambda token: "", id="empty"),
        ],
    )
    def test_not_base64url(self, spoil):
        token = seal(SECRET, PURPOSE_PAGE, PAYLOAD)
        assert unseal(SECRET, PURPOSE_PAGE, spoil(token)) is None

    def test_a_length_no_base64_has(self):
        token = seal(SECRET, PURPOSE_PAGE, PAYLOAD)
        spoiled = token + "A" * ((1 - len(token)) % 4)
        assert len(spoiled) % 4 == 1
        assert unseal(SECRET, PURPOSE_PAGE, spoiled) is None

    def test_a_second_spelling_of_the_same_bytes(self):
        """The spare bits of the last character: a lenient decoder reads both
        spellings as the same bytes, so only the one seal wrote may open."""
        token = seal(SECRET, PURPOSE_PAGE, {"a": 1})
        last = ALPHABET.index(token[-1])
        assert len(token) % 4 in (2, 3) and last % 4 == 0
        variant = token[:-1] + ALPHABET[last + 1]
        assert _unb64(variant) == _unb64(token)

        assert unseal(SECRET, PURPOSE_PAGE, token) == {"a": 1}
        assert unseal(SECRET, PURPOSE_PAGE, variant) is None

    @pytest.mark.parametrize("length", [1, 5, 11, 12, 27])
    def test_shorter_than_a_nonce_and_a_tag(self, length):
        """Under 8 bytes AESGCM would not even take the nonce, and would
        raise instead of refusing. The length check answers first."""
        assert unseal(SECRET, PURPOSE_PAGE, _b64(b"\x00" * length)) is None

    def test_a_nonce_and_a_tag_without_ciphertext(self):
        assert unseal(SECRET, PURPOSE_PAGE, _b64(b"\x00" * 28)) is None

    @pytest.mark.parametrize(
        "index", [0, 11, 12, -17, -1], ids=["nonce", "nonce-end", "ciphertext", "ciphertext-end", "tag"]
    )
    def test_tampering(self, index):
        token = seal(SECRET, PURPOSE_PAGE, PAYLOAD)
        assert unseal(SECRET, PURPOSE_PAGE, _flip(token, index)) is None

    @pytest.mark.parametrize("sealed_for", PURPOSES, ids=lambda purpose: purpose.decode())
    def test_another_purpose(self, sealed_for):
        token = seal(SECRET, sealed_for, PAYLOAD)
        for purpose in PURPOSES:
            assert (unseal(SECRET, purpose, token) is None) is (purpose != sealed_for), purpose

    def test_every_purpose_is_its_own(self):
        assert len(set(PURPOSES)) == len(PURPOSES)

    def test_another_secret(self):
        token = seal(SECRET, PURPOSE_PAGE, PAYLOAD)
        assert unseal(OTHER_SECRET, PURPOSE_PAGE, token) is None
        assert unseal(SECRET, PURPOSE_PAGE, seal(OTHER_SECRET, PURPOSE_PAGE, PAYLOAD)) is None

    @pytest.mark.parametrize(
        "plaintext",
        [b"geen json", b'{"exp":', b"\xff\xfe{}", b'{"exp":NaN}', b'{"exp":Infinity}', b'{"exp":-Infinity}'],
        ids=["text", "cut-off", "not-utf8", "nan", "infinity", "minus-infinity"],
    )
    def test_not_json(self, plaintext):
        assert unseal(SECRET, PURPOSE_PAGE, _hand_sealed(plaintext)) is None

    @pytest.mark.parametrize("plaintext", [b"[1,2]", b'"tekst"', b"1", b"null", b"true"])
    def test_json_that_is_not_an_object(self, plaintext):
        assert unseal(SECRET, PURPOSE_PAGE, _hand_sealed(plaintext)) is None


class TestFormat:
    def test_a_token_opens_with_the_secret_and_purpose_it_was_sealed_with(self):
        for purpose in PURPOSES:
            assert unseal(SECRET, purpose, seal(SECRET, purpose, PAYLOAD)) == PAYLOAD

    def test_the_token_is_the_nonce_and_the_ciphertext_of_compact_json(self):
        token = seal(SECRET, PURPOSE_PAGE, PAYLOAD)
        raw = _unb64(token)

        plaintext = AESGCM(_key(SECRET)).decrypt(raw[:12], raw[12:], PURPOSE_PAGE)

        assert plaintext == json.dumps(PAYLOAD, separators=(",", ":")).encode("ascii")
        assert len(raw) == 12 + len(plaintext) + 16

    def test_unseal_reads_the_format_built_by_hand(self):
        assert unseal(SECRET, PURPOSE_PAGE, _hand_sealed(b'{"exp":1}')) == {"exp": 1}

    def test_the_token_is_base64url_without_padding(self):
        for payload in ({}, {"a": 1}, {"ab": 1}, PAYLOAD):
            token = seal(SECRET, PURPOSE_PAGE, payload)
            assert set(token) <= set(ALPHABET), token

    def test_every_seal_draws_its_own_nonce(self):
        first, second = seal(SECRET, PURPOSE_PAGE, PAYLOAD), seal(SECRET, PURPOSE_PAGE, PAYLOAD)
        assert first != second
        assert _unb64(first)[:12] != _unb64(second)[:12]
        assert unseal(SECRET, PURPOSE_PAGE, first) == unseal(SECRET, PURPOSE_PAGE, second) == PAYLOAD

    def test_the_key_is_derived_once_per_secret(self):
        assert feedback_seal._cipher(SECRET) is feedback_seal._cipher(SECRET)
        assert feedback_seal._cipher(OTHER_SECRET) is not feedback_seal._cipher(SECRET)

    def test_a_token_of_exactly_the_maximum_length_opens(self):
        # 12 + 3044 + 16 bytes is 3072, which base64 writes in 4096 characters.
        payload = {"p": "x" * (3044 - len('{"p":""}'))}
        token = seal(SECRET, PURPOSE_PAGE, payload)
        assert len(token) == MAX_TOKEN_LENGTH
        assert unseal(SECRET, PURPOSE_PAGE, token) == payload


class TestSealRefuses:
    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_what_json_cannot_say(self, value):
        with pytest.raises(ValueError):
            seal(SECRET, PURPOSE_PAGE, {"exp": value})

    def test_a_payload_that_unseal_would_refuse_for_its_length(self):
        payload = {"p": "x" * (3045 - len('{"p":""}'))}
        with pytest.raises(ValueError, match="longer than unseal reads"):
            seal(SECRET, PURPOSE_PAGE, payload)
