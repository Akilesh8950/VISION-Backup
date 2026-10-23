from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..constants import MODEL_REPO_ID


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


def fetch_remote_model_info(token: str = "", api_factory=None) -> dict[str, Any]:
    if api_factory is None:
        from huggingface_hub import HfApi  # type: ignore

        api = HfApi(token=token or None)
    else:
        api = api_factory(token)
    info = api.model_info(MODEL_REPO_ID, files_metadata=True)
    if hasattr(info, "model_dump"):
        return info.model_dump()  # type: ignore[no-any-return]
    if isinstance(info, dict):
        return info
    siblings = []
    for item in getattr(info, "siblings", []) or []:
        siblings.append(item.model_dump() if hasattr(item, "model_dump") else dict(item))
    return {"sha": getattr(info, "sha", ""), "siblings": siblings}


def check_for_update(installed_sha: str, token: str = "", api_factory=None) -> UpdateCheck:
    try:
        info = fetch_remote_model_info(token, api_factory)
    except Exception as exc:
        return UpdateCheck(status="offline_or_error", installed_sha=installed_sha, error=str(exc))
    remote_sha = str(info.get("sha") or "")
    size = 0
    for sibling in info.get("siblings") or []:
        if str(sibling.get("rfilename") or "").endswith(".gguf"):
            size = int(sibling.get("size") or (sibling.get("lfs") or {}).get("size") or 0)
            break
    return UpdateCheck(
        status=compare_sha(installed_sha, remote_sha),
        installed_sha=installed_sha,
        remote_sha=remote_sha,
        size=size,
    )
