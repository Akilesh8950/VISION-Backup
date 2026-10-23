from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..config import AppConfig, EndpointMode


@dataclass(frozen=True)
class Endpoint:
    kind: str  # local or cloud
    base_url: str
    model: str = ""
    api_key: str = ""

    @property
    def label(self) -> str:
        return "LOCAL" if self.kind == "local" else "CLOUD"


HealthCheck = Callable[[Endpoint], Awaitable[bool]]


@dataclass
class EndpointSelection:
    endpoint: Endpoint | None
    local_failures: int = 0
    failover_offered: bool = False
    failed_over: bool = False
    error: str = ""


class EndpointResolver:
    def __init__(self, config: AppConfig, remote_api_key: str = ""):
        self.config = config
        self.remote_api_key = remote_api_key
        self.local_failures = 0

    def local_endpoint(self, model: str = "") -> Endpoint:
        return Endpoint("local", self.config.local_base_url, model or self.config.local_model_override)

    def cloud_endpoint(self, model: str = "") -> Endpoint | None:
        if not self.config.remote_base_url.strip():
            return None
        return Endpoint(
            "cloud",
            self.config.remote_base_url.strip().rstrip("/"),
            model or self.config.remote_model_override,
            self.remote_api_key,
        )

    async def select(self, health_check: HealthCheck) -> EndpointSelection:
        mode = EndpointMode(self.config.endpoint_mode)
        if mode == EndpointMode.CLOUD:
            cloud = self.cloud_endpoint()
            if cloud and await health_check(cloud):
                return EndpointSelection(cloud, self.local_failures)
            return EndpointSelection(None, self.local_failures, error="Remote endpoint is unavailable or not configured")

        local = self.local_endpoint()
        if await health_check(local):
            self.local_failures = 0
            return EndpointSelection(local, 0)

        self.local_failures += 1
        cloud = self.cloud_endpoint()
        if (
            mode == EndpointMode.AUTO
            and self.local_failures >= 2
            and cloud
            and await health_check(cloud)
        ):
            return EndpointSelection(
                cloud, self.local_failures, failover_offered=True, failed_over=True
            )
        return EndpointSelection(
            None,
            self.local_failures,
            failover_offered=self.local_failures >= 2 and cloud is not None,
            error="Local endpoint health check failed",
        )
