# Contributing

The `tesla-mcp` connector is read-only. Vehicle commands belong only in the
optional `tesla_commands` package (`tesla-commands-mcp`), which is off by
default. Do not add a command tool, a virtual key, or `tesla-http-proxy` to
`src/tesla_mcp`.

## Setup

```bash
uv sync --locked
make test
make lint
```

Python 3.11 or newer. Tests use respx and never call Tesla. Do not add a test
that needs a live car. `scripts/live_smoke.py` is opt-in, billed, and stays
out of CI.

## Secrets

- Do not commit `.env`, PEM files, or `.tesla-mcp/`.
- `.env.example` is placeholders only.
- CI runs `gitleaks detect` and `pytest`, including a scan that
  `src/tesla_mcp` contains no command endpoint strings other than `wake_up`.
  `src/tesla_commands` is the only package allowed to name those endpoints.

## Pull requests

- Keep the `tesla-mcp` public surface read-only. `tesla_wake_and_refresh` is
  the only wake path on that server, and it stays behind `confirm=true` and
  the wake budget. A command that wakes the car must use that same budget.
- Location and charging history stay opt-in and off by default.
- If you touch token refresh, keep the rule: persist the new refresh token
  atomically before anything uses the new access token.
- Run `make can-release` before asking for review.

## License

By contributing you agree that your changes are under the MIT license in
[LICENSE](LICENSE).
