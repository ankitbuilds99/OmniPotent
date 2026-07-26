"""Marketplace MCP server — discover, install, and manage agents.

Tools for browsing the agent registry, installing/uninstalling agents,
and managing agent configurations.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("marketplace")


@mcp.tool()
def list_marketplace_agents(
    category: str = "",
    verified_only: bool = False,
    search: str = "",
) -> str:
    """List agents available in the marketplace.

    Args:
        category: Filter by category (e.g., monitoring, cicd, security)
        verified_only: Only show verified agents
        search: Search term to filter agents by name, description, or tools
    """
    from app.marketplace.registry import get_registry

    registry = get_registry()
    agents = registry.list_agents(
        category=category or None,
        verified_only=verified_only,
        search=search or None,
    )

    return json.dumps(
        {
            "agents": [a.to_dict() for a in agents],
            "total": len(agents),
        },
        indent=2,
    )


@mcp.tool()
def list_marketplace_categories() -> str:
    """List all available agent categories with counts."""
    from app.marketplace.registry import get_registry

    registry = get_registry()
    categories = registry.list_categories()

    return json.dumps({"categories": categories}, indent=2)


@mcp.tool()
def get_agent_details(agent_id: str) -> str:
    """Get detailed information about a specific agent.

    Args:
        agent_id: The ID of the agent (e.g., prometheus-metrics)
    """
    from app.marketplace.manager import get_manager

    manager = get_manager()
    info = manager.get_agent_info(agent_id)

    if not info:
        return json.dumps({"error": f"Agent '{agent_id}' not found"})

    return json.dumps(info, indent=2)


@mcp.tool()
def install_agent(agent_id: str, config: str = "") -> str:
    """Install an agent from the marketplace.

    Args:
        agent_id: The ID of the agent to install
        config: Optional JSON configuration for the agent
    """
    from app.marketplace.manager import get_manager

    manager = get_manager()

    config_dict = {}
    if config:
        try:
            config_dict = json.loads(config)
        except json.JSONDecodeError:
            return json.dumps({"error": "Invalid config JSON"})

    result = manager.install(agent_id, config_dict)
    return json.dumps(result, indent=2)


@mcp.tool()
def uninstall_agent(agent_id: str) -> str:
    """Uninstall an installed agent.

    Args:
        agent_id: The ID of the agent to uninstall
    """
    from app.marketplace.manager import get_manager

    manager = get_manager()
    result = manager.uninstall(agent_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def enable_agent(agent_id: str) -> str:
    """Enable a disabled agent.

    Args:
        agent_id: The ID of the agent to enable
    """
    from app.marketplace.manager import get_manager

    manager = get_manager()
    result = manager.enable(agent_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def disable_agent(agent_id: str) -> str:
    """Disable an enabled agent without uninstalling.

    Args:
        agent_id: The ID of the agent to disable
    """
    from app.marketplace.manager import get_manager

    manager = get_manager()
    result = manager.disable(agent_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def configure_agent(agent_id: str, config: str) -> str:
    """Update configuration for an installed agent.

    Args:
        agent_id: The ID of the agent
        config: JSON configuration to merge with existing config
    """
    from app.marketplace.manager import get_manager

    manager = get_manager()

    try:
        config_dict = json.loads(config)
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid config JSON"})

    result = manager.configure(agent_id, config_dict)
    return json.dumps(result, indent=2)


@mcp.tool()
def list_installed_agents(enabled_only: bool = False) -> str:
    """List all installed agents.

    Args:
        enabled_only: Only show enabled agents
    """
    from app.marketplace.manager import get_manager

    manager = get_manager()
    agents = manager.list_installed(enabled_only=enabled_only)

    return json.dumps(
        {
            "agents": [a.to_dict() for a in agents],
            "total": len(agents),
        },
        indent=2,
    )


if __name__ == "__main__":
    mcp.run()
