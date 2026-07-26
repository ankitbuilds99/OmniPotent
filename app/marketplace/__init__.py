"""Agent Marketplace — discover, install, and manage third-party agents.

The marketplace provides a plugin system for extending DevOps Copilot with
community-contributed agents, tools, and integrations. Agents are packaged
as MCP servers that can be installed from a registry or local paths.

Key components:
- Registry: Central catalog of available agents
- Installer: Download and configure agent packages
- Manager: Enable, disable, and configure installed agents
"""

from __future__ import annotations
