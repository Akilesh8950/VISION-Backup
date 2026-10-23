import asyncio

from vision_app.config import AppConfig, EndpointMode
from vision_app.transport.endpoints import Endpoint, EndpointResolver


def test_auto_failover_after_two_local_failures():
    config = AppConfig(endpoint_mode=EndpointMode.AUTO, remote_base_url="https://remote.example/v1")
    resolver = EndpointResolver(config)

    async def health(endpoint: Endpoint) -> bool:
        return endpoint.kind == "cloud"

    first = asyncio.run(resolver.select(health))
    second = asyncio.run(resolver.select(health))
    assert first.endpoint is None and first.local_failures == 1
    assert second.endpoint is not None and second.endpoint.kind == "cloud"
    assert second.failed_over


def test_cloud_mode_skips_local():
    config = AppConfig(endpoint_mode=EndpointMode.CLOUD, remote_base_url="https://remote.example/v1")
    resolver = EndpointResolver(config)
    seen = []

    async def health(endpoint: Endpoint) -> bool:
        seen.append(endpoint.base_url)
        return True

    selection = asyncio.run(resolver.select(health))
    assert selection.endpoint.kind == "cloud"
    assert seen == ["https://remote.example/v1"]
