"""Authorization header generation.

Reproduces ``InstrumentRepository.getAuthHeader()``. The header is a TweetNaCl
(Ed25519) signature over ``SHA512(challenge_bytes + "|telescopeId|bootCount")``,
using a keypair embedded in the app. The challenge rotates with every status push,
so a fresh header is computed per request from the latest status.

The embedded NaCl secret key is ``seed || public_key`` (verified:
``SECRET_KEY[32:] == PUBLIC_KEY``), so ``SigningKey(seed)`` reproduces it exactly.
``TweetNacl.sign()`` returns ``signature || message`` (128 bytes here), matching
PyNaCl ``bytes(SigningKey.sign(...))``.
"""

from __future__ import annotations

import base64
import hashlib

from nacl.signing import SigningKey

# Embedded keys from InstrumentRepository.getAuthHeader().
PUBLIC_KEY_B64 = "aCPG7E1gvOBDwWdj82OceoebY0ARMdie0XG++to/Afc="
SECRET_KEY_B64 = (
    "O8GD9ttc5pbB/QvCK1W7TfVOmd4ZYlgOZ22Qvz6GhMZoI8bsTWC84EPBZ2PzY5x6h5tjQBEx2J7Rcb762j8B9w=="
)

PUBLIC_KEY = base64.b64decode(PUBLIC_KEY_B64)
_SECRET_KEY = base64.b64decode(SECRET_KEY_B64)
if _SECRET_KEY[32:] != PUBLIC_KEY:  # pragma: no cover - sanity guard on embedded constants
    raise RuntimeError("embedded Stellina key pair is inconsistent")

_SIGNING_KEY = SigningKey(_SECRET_KEY[:32])


def build_auth_header(challenge: str, telescope_id: str, boot_count: int) -> str:
    """Build the ``Authorization`` header value for a REST request.

    Args:
        challenge: ``status.challenge`` (first char is a selector, rest is base64).
        telescope_id: ``status.telescopeId``.
        boot_count: ``status.bootCount``.
    """
    first = challenge[0]
    rest = base64.b64decode(challenge[1:])
    suffix = f"|{telescope_id}|{boot_count}".encode()
    digest = hashlib.sha512(rest + suffix).digest()  # 64 bytes
    signed = bytes(_SIGNING_KEY.sign(digest))  # 64-byte signature || 64-byte digest
    return f"Basic android|{first}|{base64.b64encode(signed).decode('ascii')}"
