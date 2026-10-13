"""Verification of independently signed release-artifact digests."""

from __future__ import annotations

import base64
from collections.abc import Mapping


def verify_artifact_signatures(
    artifact_digests: Mapping[str, str],
    signatures: Mapping[str, str],
    public_key_pem: bytes | str,
) -> bool:
    """Verify every artifact digest has a valid Ed25519 signature.

    Signatures are URL-safe/base64 encodings of the signature over the exact
    lowercase SHA-256 digest string. No key or artifact name is embedded in the
    verifier; deployment supplies the trusted public key and manifest data.
    """
    if not artifact_digests or set(artifact_digests) != set(signatures):
        return False
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        key = serialization.load_pem_public_key(
            public_key_pem.encode() if isinstance(public_key_pem, str) else public_key_pem
        )
        if not isinstance(key, Ed25519PublicKey):
            return False
        for name, digest in artifact_digests.items():
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
                return False
            try:
                signature = base64.urlsafe_b64decode(signatures[name].encode())
                key.verify(signature, digest.lower().encode())
            except Exception:  # noqa: BLE001 - invalid external evidence fails closed
                return False
        return True
    except Exception:  # noqa: BLE001 - optional crypto/provider failure fails closed
        return False
