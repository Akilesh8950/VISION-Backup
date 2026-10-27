from vision_app.extensions.connectors import (
    ConnectorConfig,
    ConnectorConfigFile,
    connector_as_server,
    load_connectors,
    save_connectors,
)


def test_connector_round_trip_and_server_conversion(tmp_path):
    path = tmp_path / "connectors.json"
    connector = ConnectorConfig(
        id="notes", display_name="Notes", transport="streamable_http", url="https://example.com/mcp",
        auth_type="bearer", header_refs={"Authorization": "CONNECTOR/notes/token"},
    )
    save_connectors(path, ConnectorConfigFile(connectors=[connector]))
    loaded = load_connectors(path).connectors[0]
    assert loaded.auth_type == "bearer"
    converted = connector_as_server(loaded)
    assert converted.url == "https://example.com/mcp"
    # Stored MCP and connector IDs have separate namespaces. Their live MCP
    # sessions must not collide if both records use the same stored ID.
    assert converted.id == "connector-notes"
    assert loaded.id == "notes"
