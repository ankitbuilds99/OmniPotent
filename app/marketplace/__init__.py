"""Agent Marketplace — discover, install, and manage add-on agents.

Agents are packaged as MCP servers. Installing one records it and, from the next
session, `app.mcp.client` launches it alongside the built-in servers so its tools
reach the LLM; disabling it takes them away again.

Key components:
- Registry: catalogue of agents, each declaring the MCP server module it needs
- Manager: install, enable, disable and configure — persisted under the data dir

There is deliberately no installer/downloader. An agent is installable only when
its module already resolves in this deployment; fetching and executing code from
a remote registry is a supply-chain surface we do not open. Catalogue entries
without an implementation report `available: false` and refuse to install,
rather than recording a row and reporting success for tools that do not exist.
"""

from __future__ import annotations
