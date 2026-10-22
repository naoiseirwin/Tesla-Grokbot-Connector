"""Opt-in Tesla vehicle commands for Grok Bot.

This package is a separate stdio MCP server (`tesla-commands-mcp`). It is off
unless TESLA_ENABLE_COMMANDS=true, and it dry-runs unless
TESLA_COMMANDS_DRY_RUN=false. The read-only `tesla_mcp` server does not import
this package.
"""

__version__ = "0.1.0"
