"""A real (tiny) MCP server used by test_extensions_spec via stdio.

Runs the official SDK's MCPServer so tests exercise the actual
initialize handshake, capability negotiation and tool calls — not a mock
of the protocol.
"""

import os

from mcp.server.mcpserver import MCPServer

server = MCPServer("fake-tools")


@server.tool()
def echo_text(text: str) -> str:
    """Echo the given text back."""
    return f"echo:{text}"


@server.tool()
def weather_lookup(city: str) -> str:
    """Look up the weather for a city."""
    return f"sunny in {city}"


@server.tool()
def delete_everything(path: str) -> str:
    """Delete everything at a path (VISION must hard-block this by name)."""
    return f"deleted {path}"


@server.tool()
def env_probe(name: str) -> str:
    """Report whether an environment variable is visible to the server."""
    return os.environ.get(name, "<missing>")


@server.tool()
def cwd_probe() -> str:
    """Report the server process working directory."""
    return os.getcwd()


if __name__ == "__main__":
    server.run(transport="stdio")
