"""Trusted VISION release-signing public keys.

Keys are raw Ed25519 public keys encoded with standard Base64, keyed by
an operator-chosen id such as ``"2026-01"``.

This mapping is intentionally empty until the release operator generates
an offline private key with ``scripts/release_signing.py`` and commits
only its public half here.  While it is empty, update verification fails
closed and unsigned app/config update data is rejected.
"""

from __future__ import annotations

RELEASE_PUBLIC_KEYS: dict[str, str] = {}

__all__ = ["RELEASE_PUBLIC_KEYS"]
