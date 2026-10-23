from vision_app.extensions.connectors import ConnectorConfig, connector_as_server, load_connectors, save_connectors, ConnectorConfigFile


def test_connector_round_trip_and_server_conversion(tmp_path):
    path = tmp_path / "connectors.json"
    connector = ConnectorConfig(
        id="notes", display_name="Notes", transport="streamable_http", url="https://example.com/mcp",
        auth_type="bearer", header_refs={"Authorization": "CONNECTOR/notes/token"},
    )
    save_connectors(path, ConnectorConfigFile(connectors=[connector]))
    loaded = load_connectors(path).connectors[0]
    assert loaded.auth_type == "bearer"
    assert connector_as_server(loaded).url == "https://example.com/mcp"
