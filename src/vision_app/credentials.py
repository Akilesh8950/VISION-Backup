from __future__ import annotations

import logging
import sys
from typing import Protocol

SERVICE = "VISION"
logger = logging.getLogger(__name__)
_ERROR_NOT_FOUND = 1168


def _is_not_found(exc: BaseException) -> bool:
    if getattr(exc, "winerror", None) == _ERROR_NOT_FOUND:
        return True
    args = getattr(exc, "args", ())
    return bool(args and args[0] == _ERROR_NOT_FOUND)


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
                encodings = ("utf-16-le", "utf-8") if b"\x00" in blob else ("utf-8", "utf-16-le")
                for encoding in encodings:
                    try:
                        return blob.decode(encoding).rstrip("\x00")
                    except UnicodeDecodeError:
                        continue
                return ""
            return str(blob)
        except Exception as exc:
            if not _is_not_found(exc):
                logger.warning("Could not read credential %s from Windows Credential Manager", key)
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
        except Exception as exc:
            if not _is_not_found(exc):
                logger.warning("Could not delete credential %s from Windows Credential Manager", key)


def get_credential_store() -> CredentialStore:
    if sys.platform == "win32":
        try:
            import win32cred  # noqa: F401  # type: ignore

            return WindowsCredentialStore()
        except Exception:
            logger.warning(
                "Windows Credential Manager is unavailable; secrets will not persist after this run",
                exc_info=True,
            )
            return MemoryCredentialStore()
    return MemoryCredentialStore()
