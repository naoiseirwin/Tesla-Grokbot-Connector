# Changelog

## Unreleased

- `tesla_get_charging_history` accepts a date or an ISO 8601 time, defaults to the last 365 days, and loads ranges longer than a year in consecutive 365-day windows (up to 10 years). A rejected Tesla request includes Tesla's explanation.
- Optional `tesla-commands-mcp` connector for a short list of vehicle commands. It is a separate entry point, off by default, and dry-run unless you opt in. The read-only `tesla-mcp` tool list is unchanged. See [docs/COMMANDS.md](docs/COMMANDS.md).

## 0.1.0

First read-only release.

- Stdio MCP server for the Tesla Fleet API, modeled on the Grok Bot Strava bridge.
- `tesla-mcp auth` with a localhost callback and a paste fallback.
- AES-GCM token file and single-use refresh rotation.
- Vehicle, history, and report tools. Location and charging history are opt-in.
- `tesla-mcp poll` reads cars that are already online and does not wake them.
- Wake budget: 3 per day, 30 minutes apart, and never more than 3 per minute.
