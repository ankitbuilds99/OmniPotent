from app.mcp.servers.terraform import server


def test_unimplemented_terraform_templates_are_explicitly_unsupported():
    missing = [
        name
        for name in server.MODULE_TEMPLATES
        if not server.MODULE_CODE_TEMPLATES.get(name)
    ]
    assert missing
    for name in missing:
        result = server.generate_terraform_module(name)
        assert result["status"] == "unsupported"
        assert result["simulated"] is False
        assert "error" in result
