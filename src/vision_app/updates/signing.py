"""Ed25519 signing for VISION update JSON.

Only public keys belong in the application.  The corresponding private
key is generated and used by the release operator outside the repository
and outside the packaged app (see ``scripts/release_signing.py``).
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

SIGNATURE_FIELD = "signature"
SIGNATURE_KEY_ID_FIELD = "signature_key_id"
SIGNATURE_ALGORITHM_FIELD = "signature_algorithm"
SIGNATURE_FIELDS = {
    SIGNATURE_FIELD,
    SIGNATURE_KEY_ID_FIELD,
    SIGNATURE_ALGORITHM_FIELD,
}
_SIGNING_DOMAIN = b"VISION signed JSON v1\n"


class SignatureError(ValueError):
    """Signed update data is missing, malformed, or not trusted."""


def canonical_signed_json_bytes(payload: dict[str, Any], *, purpose: str) -> bytes:
    clean = {
        key: value
        for key, value in payload.items()
        if key not in SIGNATURE_FIELDS
    }
    try:
        encoded = json.dumps(
            clean,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SignatureError("Signed JSON cannot be canonicalized") from exc
    clean_purpose = str(purpose or "").strip()
    if not clean_purpose or len(clean_purpose) > 80:
        raise SignatureError("Invalid signing purpose")
    return _SIGNING_DOMAIN + clean_purpose.encode("utf-8") + b"\n" + encoded


def _decode_base64(value: object, *, expected_length: int, label: str) -> bytes:
    text = str(value or "").strip()
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureError(f"Invalid {label}") from exc
    if len(decoded) != expected_length:
        raise SignatureError(f"Invalid {label} length")
    return decoded


def verify_signed_json(
    payload: dict[str, Any],
    public_keys: dict[str, str],
    *,
    purpose: str,
) -> dict[str, Any]:
    if not public_keys:
        raise SignatureError("Release signing is not configured in this build")
    algorithm = str(payload.get(SIGNATURE_ALGORITHM_FIELD) or "Ed25519")
    if algorithm != "Ed25519":
        raise SignatureError("Unsupported signature algorithm")
    key_id = str(payload.get(SIGNATURE_KEY_ID_FIELD) or "").strip()
    if key_id not in public_keys:
        raise SignatureError("Unknown release signing key")
    signature = _decode_base64(
        payload.get(SIGNATURE_FIELD), expected_length=64, label="signature"
    )
    public_key_bytes = _decode_base64(
        public_keys[key_id], expected_length=32, label="release public key"
    )
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except Exception as exc:  # pragma: no cover - packaging error
        raise SignatureError("Signature verification is unavailable") from exc
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            signature,
            canonical_signed_json_bytes(payload, purpose=purpose),
        )
    except InvalidSignature as exc:
        raise SignatureError("Signature verification failed") from exc
    return payload


def public_key_b64(private_key: Any) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def sign_json_payload(
    payload: dict[str, Any],
    private_key: Any,
    *,
    key_id: str,
    purpose: str,
) -> dict[str, Any]:
    clean_key_id = str(key_id or "").strip()
    if not clean_key_id or len(clean_key_id) > 64:
        raise SignatureError("Invalid signing key id")
    signed = {
        key: value
        for key, value in payload.items()
        if key not in SIGNATURE_FIELDS
    }
    signature = private_key.sign(
        canonical_signed_json_bytes(signed, purpose=purpose)
    )
    signed[SIGNATURE_ALGORITHM_FIELD] = "Ed25519"
    signed[SIGNATURE_KEY_ID_FIELD] = clean_key_id
    signed[SIGNATURE_FIELD] = base64.b64encode(signature).decode("ascii")
    return signed


def generate_private_key() -> Any:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    return Ed25519PrivateKey.generate()


def private_key_to_pem(private_key: Any, password: bytes | None = None) -> bytes:
    from cryptography.hazmat.primitives import serialization

    encryption = (
        serialization.BestAvailableEncryption(password)
        if password
        else serialization.NoEncryption()
    )
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )


def load_private_key_pem(data: bytes, password: bytes | None = None) -> Any:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    key = serialization.load_pem_private_key(data, password=password)
    if not isinstance(key, Ed25519PrivateKey):
        raise SignatureError("Release private key is not Ed25519")
    return key


__all__ = [
    "SIGNATURE_ALGORITHM_FIELD",
    "SIGNATURE_FIELD",
    "SIGNATURE_FIELDS",
    "SIGNATURE_KEY_ID_FIELD",
    "SignatureError",
    "canonical_signed_json_bytes",
    "generate_private_key",
    "load_private_key_pem",
    "private_key_to_pem",
    "public_key_b64",
    "sign_json_payload",
    "verify_signed_json",
]
