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
    def __init__(
        self,
        config: AppConfig,
        remote_api_key: str = "",
        *,
        remote_api_key_provider: Callable[[], str] | None = None,
    ):
        self.config = config
        self.remote_api_key = remote_api_key
        self.remote_api_key_provider = remote_api_key_provider
        self.local_failures = 0

    def _current_remote_api_key(self) -> str:
        if self.remote_api_key_provider is not None:
            try:
                return str(self.remote_api_key_provider() or "")
            except Exception:
                return ""
        return self.remote_api_key

    def local_endpoint(self, model: str = "") -> Endpoint:
        return Endpoint("local", self.config.local_base_url, model or self.config.local_model_override)

    def cloud_endpoint(self, model: str = "") -> Endpoint | None:
        if not self.config.remote_base_url.strip():
            return None
        return Endpoint(
            "cloud",
            self.config.remote_base_url.strip().rstrip("/"),
            model or self.config.remote_model_override,
            self._current_remote_api_key(),
        )

    @staticmethod
    async def _healthy(health_check: HealthCheck, endpoint: Endpoint) -> bool:
        try:
            return bool(await health_check(endpoint))
        except Exception:
            return False

    async def select(self, health_check: HealthCheck) -> EndpointSelection:
        mode = EndpointMode(self.config.endpoint_mode)
        if mode == EndpointMode.CLOUD:
            cloud = self.cloud_endpoint()
            if cloud and await self._healthy(health_check, cloud):
                return EndpointSelection(cloud, self.local_failures)
            return EndpointSelection(None, self.local_failures, error="Remote endpoint is unavailable or not configured")

        local = self.local_endpoint()
        if await self._healthy(health_check, local):
            self.local_failures = 0
            return EndpointSelection(local, 0)

        self.local_failures += 1
        cloud = self.cloud_endpoint()
        if (
            mode == EndpointMode.AUTO
            and self.local_failures >= 2
            and cloud
            and await self._healthy(health_check, cloud)
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
