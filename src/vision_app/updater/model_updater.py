from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..constants import GGUF_FILENAME, MODEL_REPO_ID


def compare_sha(installed: str, remote: str) -> str:
    if not remote:
        return "unknown"
    if not installed:
        return "newer_available"
    return "current" if installed == remote else "different"


@dataclass
class UpdateCheck:
    status: str
    installed_sha: str = ""
    remote_sha: str = ""
    size: int = 0
    error: str = ""


def _value(item: Any, *names: str) -> Any:
    for name in names:
        value = item.get(name) if isinstance(item, dict) else getattr(item, name, None)
        if value is not None:
            return value
    return None


def _as_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        dumped = item.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    if isinstance(item, dict):
        return item
    return {
        "rfilename": _value(item, "rfilename", "filename", "path") or "",
        "size": _value(item, "size") or 0,
        "lfs": _value(item, "lfs"),
    }


def fetch_remote_model_info(token: str | None = None, api_factory=None) -> dict[str, Any]:
    if api_factory is None:
        from huggingface_hub import HfApi  # type: ignore

        api = HfApi(token=token or None)
    else:
        api = api_factory(token or "")
    info = api.model_info(MODEL_REPO_ID, files_metadata=True)
    if hasattr(info, "model_dump"):
        dumped = info.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    if isinstance(info, dict):
        return info
    siblings = [_as_dict(item) for item in _value(info, "siblings") or []]
    return {"sha": _value(info, "sha") or "", "siblings": siblings}


def _sibling_size(sibling: Any) -> int:
    direct = _value(sibling, "size")
    lfs = _value(sibling, "lfs")
    nested = _value(lfs, "size") if lfs is not None else None
    for value in (direct, nested):
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 0


def check_for_update(installed_sha: str, token: str | None = None, api_factory=None) -> UpdateCheck:
    try:
        info = fetch_remote_model_info(token, api_factory)
    except Exception as exc:
        return UpdateCheck(status="offline_or_error", installed_sha=installed_sha, error=str(exc))
    remote_sha = str(info.get("sha") or "") if isinstance(info, dict) else ""
    size = 0
    siblings_raw = info.get("siblings") if isinstance(info, dict) else []
    siblings = siblings_raw if isinstance(siblings_raw, list) else []
    for raw_sibling in siblings:
        sibling = _as_dict(raw_sibling)
        filename = str(sibling.get("rfilename") or sibling.get("filename") or "").replace("\\", "/")
        if filename == GGUF_FILENAME or filename.rsplit("/", 1)[-1] == GGUF_FILENAME:
            size = _sibling_size(sibling)
            break
    return UpdateCheck(
        status=compare_sha(installed_sha, remote_sha),
        installed_sha=installed_sha,
        remote_sha=remote_sha,
        size=size,
    )
