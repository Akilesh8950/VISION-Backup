from __future__ import annotations

import asyncio
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from ..transport.client import ChatClient
from ..transport.endpoints import Endpoint

# Loading a 9B GGUF can legitimately take much longer than two 5-second
# health polls. Automatic restart decisions therefore use the same 180
# second envelope as wait_until_healthy while a managed process is alive.
STARTUP_GRACE_SECONDS = 180.0


async def check_health(client: ChatClient, base_url: str) -> bool:
    return await client.health(Endpoint("local", base_url))


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_health_sync(base_url: str, timeout: float = 3.0) -> bool:
    parsed = urlparse(str(base_url or ""))
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return False
    try:
        _port = parsed.port
    except ValueError:
        return False
    url = str(base_url).rstrip("/") + "/models"
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        # The scheme/host were validated above and redirects are refused;
        # this helper is used only for the configured local endpoint.
        with opener.open(url, timeout=timeout) as response:  # nosec B310
            return response.status == 200
    except Exception:
        return False


async def wait_until_healthy(
    client: ChatClient,
    base_url: str,
    *,
    timeout: float = 180.0,
    interval: float = 1.0,
) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if await check_health(client, base_url):
            return True
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(interval)


@dataclass
class HealthState:
    failures: int = 0
    restart_attempted: bool = False

    def reset(self, *, clear_restart_attempt: bool = True) -> None:
        self.failures = 0
        if clear_restart_attempt:
            self.restart_attempted = False

    def record(self, healthy: bool) -> str:
        """Return ok, failed_once, restart, or failover."""

        if healthy:
            self.reset()
            return "ok"
        self.failures += 1
        if self.failures < 2:
            return "failed_once"
        if not self.restart_attempted:
            self.restart_attempted = True
            return "restart"
        return "failover"
