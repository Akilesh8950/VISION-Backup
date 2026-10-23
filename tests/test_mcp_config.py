from vision_app.extensions.mcp_config import export_mcp_servers, import_mcp_servers


def test_import_extracts_secrets_and_export_redacts_them():
    configs, secrets = import_mcp_servers({
        "mcpServers": {
            "Files": {
                "command": "mcp-files",
                "args": ["C:/data"],
                "env": {"API_KEY": "secret-value"},
            }
        }
    })
    assert configs[0].env_refs["API_KEY"].endswith("API_KEY")
    assert list(secrets.values()) == ["secret-value"]
    assert "secret-value" not in str(export_mcp_servers(configs))
