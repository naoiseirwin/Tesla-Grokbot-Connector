# Changelog

## Unreleased

- A command whose proxy call times out, returns HTTP 408 or 5xx, or is interrupted after the request may have been sent is reported as `sent: "unknown"`. The rate-limit slot is kept, the attempt is written to the audit log, and it is not retried. The slot is released only when the request never left, for example a connection error or connect timeout. Other HTTP 4xx answers stay refusals and still keep the slot.
- `TESLA_FLEET_API_BASE`, `TESLA_TOKEN_URL`, and `TESLA_AUTH_URL` must be the exact https endpoint for that setting and `TESLA_REGION` (port 443 only). The URL is rebuilt from its parsed parts. `tesla.cn` is allowed only as the China Fleet host, and only when the region is `cn`. Token and sign-in stay on tesla.com in every region. A `.env` file in the working directory is not loaded. `TESLA_COMMAND_PROXY_INSECURE` logs a startup warning and is refused unless the proxy URL is loopback. A remote proxy must use https.
- Command and wake caps count a rolling 24-hour window. The numbers are unchanged. Out-of-range `TESLA_WAKE_DAILY_CAP` and `TESLA_WAKE_MIN_INTERVAL_SECONDS` values are rejected, not clamped. The wake daily cap cannot be set above 10. The interval cannot be set above 86400. Defaults stay 3 and 1800.
- `make deps` and CI install with `uv sync --locked`. Test, lint, typecheck, and run use `uv run --frozen`. GitHub Actions are pinned to commit SHAs. The gitleaks download is checked against the official SHA-256. Gitleaks allowlists the placeholder strings only, not whole files. HTTP clients set `follow_redirects=False` and `trust_env=False`.
- The command budget closes its SQLite connection after each use.
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
