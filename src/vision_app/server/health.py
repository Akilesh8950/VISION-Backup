from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..transport.client import ChatClient
from ..transport.endpoints import Endpoint


async def check_health(client: ChatClient, base_url: str) -> bool:
    return await client.health(Endpoint("local", base_url))


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

    def record(self, healthy: bool) -> str:
        """Return ok, failed_once, restart, or failover."""
        if healthy:
            self.failures = 0
            self.restart_attempted = False
            return "ok"
        self.failures += 1
        if self.failures < 2:
            return "failed_once"
        if not self.restart_attempted:
            self.restart_attempted = True
            return "restart"
        return "failover"
