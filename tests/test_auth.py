"""Auth header tests.

The header embeds a real Ed25519 signature, so we verify it against the embedded
public key rather than against a hard-coded golden string. This proves the whole
pipeline (challenge decode, message assembly, SHA512, signing, encoding) is correct.
"""

from __future__ import annotations

import base64
import hashlib

from nacl.signing import VerifyKey

from pyvaonis.auth import PUBLIC_KEY
from pyvaonis.auth import build_auth_header

# A plausible challenge: selector char "x" + base64 of 16 random-ish bytes.
CHALLENGE = "x" + base64.b64encode(bytes(range(16))).decode()
TELESCOPE_ID = "STELLINA-1234"
BOOT_COUNT = 42


def test_key_pair_is_consistent() -> None:
    # build_auth_header imports succeed only if SECRET[32:] == PUBLIC (guarded there).
    assert len(PUBLIC_KEY) == 32


def test_header_shape() -> None:
    header = build_auth_header(CHALLENGE, TELESCOPE_ID, BOOT_COUNT)
    prefix, selector, signature = header.split("|")
    assert prefix == "Basic android"
    assert selector == CHALLENGE[0]
    # signature = 64-byte sig || 64-byte sha512 digest = 128 bytes.
    assert len(base64.b64decode(signature)) == 128


def test_signature_verifies_against_embedded_public_key() -> None:
    header = build_auth_header(CHALLENGE, TELESCOPE_ID, BOOT_COUNT)
    signed = base64.b64decode(header.split("|")[2])
    sig, message = signed[:64], signed[64:]

    expected = hashlib.sha512(
        base64.b64decode(CHALLENGE[1:]) + f"|{TELESCOPE_ID}|{BOOT_COUNT}".encode()
    ).digest()
    assert message == expected

    # Raises BadSignatureError if the signature is invalid.
    VerifyKey(PUBLIC_KEY).verify(message, sig)


def test_header_is_deterministic() -> None:
    a = build_auth_header(CHALLENGE, TELESCOPE_ID, BOOT_COUNT)
    b = build_auth_header(CHALLENGE, TELESCOPE_ID, BOOT_COUNT)
    assert a == b
