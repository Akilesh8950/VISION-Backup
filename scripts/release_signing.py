"""Offline release-signing helper for VISION update JSON.

Examples (run on the release operator's machine, never in CI with the
private key checked in):

    python scripts/release_signing.py generate \
        --private-key D:\\VISION-release-keys\\2026-01.pem \
        --public-key D:\\VISION-release-keys\\2026-01.public.txt \
        --key-id 2026-01

    python scripts/release_signing.py sign \
        --private-key D:\\VISION-release-keys\\2026-01.pem \
        --key-id 2026-01 --purpose app-manifest-stable latest.json

    python scripts/release_signing.py sign \
        --private-key D:\\VISION-release-keys\\2026-01.pem \
        --key-id 2026-01 --purpose remote-config-stable updates\\config.json

Add only the generated public key to
``src/vision_app/updates/release_keys.py`` and rebuild VISION before
publishing JSON signed by that key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vision_app.updates.signing import (
    generate_private_key,
    load_private_key_pem,
    private_key_to_pem,
    public_key_b64,
    sign_json_payload,
    verify_signed_json,
)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _password(env_name: str) -> bytes | None:
    value = os.environ.get(env_name or "")
    return value.encode("utf-8") if value else None


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _read_json(path: Path) -> dict:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return parsed


def _write_json(path: Path, payload: dict) -> None:
    _atomic_write(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _load_public_key_file(path: Path) -> tuple[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    return values.get("key_id", ""), values.get("public_key", "")


def command_generate(args: argparse.Namespace) -> int:
    private_path = Path(args.private_key).expanduser()
    public_path = Path(args.public_key).expanduser()
    if _is_within(private_path, ROOT):
        raise SystemExit("Refusing to write a release private key inside the repository")
    if private_path.exists() and not args.force:
        raise SystemExit(f"Private key already exists: {private_path} (use --force only intentionally)")
    if public_path.exists() and not args.force:
        raise SystemExit(f"Public key file already exists: {public_path} (use --force only intentionally)")

    key = generate_private_key()
    _atomic_write(private_path, private_key_to_pem(key, _password(args.password_env)))
    try:
        os.chmod(private_path, 0o600)
    except OSError:
        pass
    public_text = (
        "# VISION Ed25519 release public key\n"
        f"key_id={args.key_id}\n"
        f"public_key={public_key_b64(key)}\n"
    )
    _atomic_write(public_path, public_text.encode("utf-8"))
    print(f"Wrote private key outside the repo: {private_path}")
    print(f"Wrote public key: {public_path}")
    print("Add the public key to src/vision_app/updates/release_keys.py, then rebuild VISION.")
    return 0


def command_sign(args: argparse.Namespace) -> int:
    private_path = Path(args.private_key).expanduser()
    key = load_private_key_pem(private_path.read_bytes(), _password(args.password_env))
    public_keys = {args.key_id: public_key_b64(key)}
    for raw_path in args.files:
        path = Path(raw_path)
        signed = sign_json_payload(
            _read_json(path), key, key_id=args.key_id, purpose=args.purpose
        )
        verify_signed_json(signed, public_keys, purpose=args.purpose)
        _write_json(path, signed)
        print(f"Signed {path} for {args.purpose}")
    return 0


def command_verify(args: argparse.Namespace) -> int:
    key_id = args.key_id
    public_key = args.public_key
    if args.public_key_file:
        file_key_id, file_public_key = _load_public_key_file(Path(args.public_key_file))
        key_id = key_id or file_key_id
        public_key = public_key or file_public_key
    if not key_id or not public_key:
        raise SystemExit("Provide --public-key-file or both --key-id and --public-key")
    public_keys = {key_id: public_key}
    for raw_path in args.files:
        path = Path(raw_path)
        verify_signed_json(_read_json(path), public_keys, purpose=args.purpose)
        print(f"Verified {path} for {args.purpose}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate an offline Ed25519 keypair")
    generate.add_argument("--private-key", required=True)
    generate.add_argument("--public-key", required=True)
    generate.add_argument("--key-id", required=True)
    generate.add_argument("--password-env", default="VISION_RELEASE_KEY_PASSWORD")
    generate.add_argument("--force", action="store_true")
    generate.set_defaults(func=command_generate)

    sign = subparsers.add_parser("sign", help="Sign one or more update JSON files in place")
    sign.add_argument("--private-key", required=True)
    sign.add_argument("--key-id", required=True)
    sign.add_argument("--purpose", required=True)
    sign.add_argument("--password-env", default="VISION_RELEASE_KEY_PASSWORD")
    sign.add_argument("files", nargs="+")
    sign.set_defaults(func=command_sign)

    verify = subparsers.add_parser("verify", help="Verify one or more signed update JSON files")
    verify.add_argument("--purpose", required=True)
    verify.add_argument("--key-id", default="")
    verify.add_argument("--public-key", default="")
    verify.add_argument("--public-key-file", default="")
    verify.add_argument("files", nargs="+")
    verify.set_defaults(func=command_verify)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
