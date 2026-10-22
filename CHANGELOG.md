# Changelog

## 0.1.0

First read-only release.

- Stdio MCP server for the Tesla Fleet API, modeled on the Grok Bot Strava bridge.
- `tesla-mcp auth` with a localhost callback and a paste fallback.
- AES-GCM token file and single-use refresh rotation.
- Vehicle, history, and report tools. Location and charging history are opt-in.
- `tesla-mcp poll` reads cars that are already online and does not wake them.
- Wake budget: 3 per day, 30 minutes apart, and never more than 3 per minute.
