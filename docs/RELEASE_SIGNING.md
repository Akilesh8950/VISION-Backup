# VISION release signing

VISION verifies public app-update manifests and remote configuration files
with an Ed25519 signature before using them. Only the public key is embedded
in the application. The matching private key stays offline with the release
owner and must never be committed, uploaded, placed in CI secrets for this
workflow, or stored inside the repository.

Signing these JSON files does not replace Windows Authenticode signing of
`VISION.exe` and the installer. Authenticode remains a separate public-release
step if the owner wants to reduce SmartScreen publisher warnings.

## Current release blocker

`src/vision_app/updates/release_keys.py` intentionally contains no trusted
public key yet. While `RELEASE_PUBLIC_KEYS` is empty, VISION fails closed and
rejects public update manifests and remote configurations. Complete the
one-time key ceremony below, embed only the public key, rebuild VISION, and
distribute that build before publishing the first signed update JSON.

## One-time key ceremony (owner only)

Use a trusted Windows machine and storage outside the repository, preferably
an encrypted offline folder or removable drive. Choose a stable key id such
as `2026-01`; do not reuse an old id for a different key.

Set a strong private-key passphrase in the current PowerShell process from
your password manager. Do not paste the passphrase as a permanent command in
scripts or documentation.

```powershell
$env:VISION_RELEASE_KEY_PASSWORD = "<paste the passphrase for this session only>"
New-Item -ItemType Directory -Force D:\VISION-release-keys | Out-Null
python scripts\release_signing.py generate `
  --private-key D:\VISION-release-keys\2026-01.pem `
  --public-key D:\VISION-release-keys\2026-01.public.txt `
  --key-id 2026-01
Remove-Item Env:VISION_RELEASE_KEY_PASSWORD
```

The signing helper refuses to write a newly generated private key inside the
repository. Back up the encrypted `.pem` file and its passphrase separately.
If both are lost, that key cannot be recovered.

Open the generated `.public.txt` file and copy only its `key_id` and
`public_key` values into `src/vision_app/updates/release_keys.py`:

```python
RELEASE_PUBLIC_KEYS: dict[str, str] = {
    "2026-01": "<Base64 public key from 2026-01.public.txt>",
}
```

Commit only that public-key change. Rebuild and smoke-test VISION before
publishing any JSON signed by the new key.

## Signature purposes

A signature is valid only for its exact channel and document type:

| File | Purpose |
| --- | --- |
| `latest.json` | `app-manifest-stable` |
| `latest-dev.json` | `app-manifest-dev` |
| `updates/config.json` | `remote-config-stable` |
| `updates/config-dev.json` | `remote-config-dev` |

This prevents a signed dev file or remote config from being replayed as a
stable app manifest.

## Normal release flow

Pass the offline key path and key id to `scripts\release.ps1`. Set the key
passphrase in the process environment first when the PEM is encrypted.

```powershell
$env:VISION_RELEASE_KEY_PASSWORD = "<paste the passphrase for this session only>"
.\scripts\release.ps1 `
  -BuildFrontend `
  -SigningKey D:\VISION-release-keys\2026-01.pem `
  -SigningKeyId 2026-01 `
  -SignConfig `
  -Notes "..."
Remove-Item Env:VISION_RELEASE_KEY_PASSWORD
```

Review the generated ZIPs and manifest. `-Publish` deliberately refuses to
run without `-SigningKey` and `-SigningKeyId`. After the release assets upload
successfully, commit and push the signed manifest—and the signed config when
it changed—using the owner's normal credential flow.

## Manual signing and verification

Sign a file in place:

```powershell
python scripts\release_signing.py sign `
  --private-key D:\VISION-release-keys\2026-01.pem `
  --key-id 2026-01 `
  --purpose app-manifest-stable `
  latest.json
```

Verify with the generated public-key file:

```powershell
python scripts\release_signing.py verify `
  --public-key-file D:\VISION-release-keys\2026-01.public.txt `
  --purpose app-manifest-stable `
  latest.json
```

Use `remote-config-stable` or the corresponding `-dev` purpose for config
files. Signing an already signed payload replaces its signature fields after
the helper verifies the newly produced signature before writing it.

## Rotation and compromise response

For planned rotation, generate a new key id, ship a VISION build that trusts
both the old and new public keys, then switch signing to the new private key.
Remove the old public key only in a later release after the transition build
is widely installed.

If a private key is lost, generate a replacement and distribute a build that
trusts it through the installer or another already trusted channel; existing
builds cannot learn a new key from an update signed by an unknown key.

If a private key may be compromised, stop publishing with it immediately.
Create a new key, distribute a replacement build through a trusted installer
or manual download, and remove the compromised public key from that build.
Do not rely on an update signed by the compromised key to revoke itself.

## Pre-publication checklist

- The private key is outside the repository and backed up encrypted.
- Only the matching public key and key id were committed.
- The distributed VISION build contains that public key.
- `latest.json` or `latest-dev.json` verifies with the exact purpose.
- Any changed remote config verifies with its exact purpose.
- `scripts\release.ps1 -Publish` was given both signing options.
- Signed JSON was committed only after the referenced assets uploaded.
- No private key, passphrase, access token, or credential appears in Git,
  release notes, logs, or shell history.
