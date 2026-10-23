from __future__ import annotations

import sys
from typing import Protocol

SERVICE = "VISION"


class CredentialStore(Protocol):
    def get(self, key: str) -> str: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class MemoryCredentialStore:
    """Non-Windows/test fallback. Values live only in this process."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, key: str) -> str:
        return self._values.get(key, "")

    def set(self, key: str, value: str) -> None:
        if value:
            self._values[key] = value
        else:
            self._values.pop(key, None)

    def delete(self, key: str) -> None:
        self._values.pop(key, None)


class WindowsCredentialStore:
    def _target(self, key: str) -> str:
        return f"{SERVICE}/{key}"

    def get(self, key: str) -> str:
        try:
            import win32cred  # type: ignore

            item = win32cred.CredRead(win32cred.CRED_TYPE_GENERIC, self._target(key), 0)
            blob = item.get("CredentialBlob", "")
            if isinstance(blob, bytes):
                for encoding in ("utf-16-le", "utf-8"):
                    try:
                        return blob.decode(encoding).rstrip("\x00")
                    except UnicodeDecodeError:
                        continue
                return ""
            return str(blob)
        except Exception:
            return ""

    def set(self, key: str, value: str) -> None:
        if not value:
            self.delete(key)
            return
        import win32cred  # type: ignore

        win32cred.CredWrite(
            {
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": self._target(key),
                "UserName": SERVICE,
                "CredentialBlob": value,
                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
                "Comment": "Stored by VISION",
            },
            0,
        )

    def delete(self, key: str) -> None:
        try:
            import win32cred  # type: ignore

            win32cred.CredDelete(win32cred.CRED_TYPE_GENERIC, self._target(key), 0)
        except Exception:
            pass


def get_credential_store() -> CredentialStore:
    if sys.platform == "win32":
        try:
            import win32cred  # noqa: F401  # type: ignore

            return WindowsCredentialStore()
        except Exception:
            return MemoryCredentialStore()
    return MemoryCredentialStore()
