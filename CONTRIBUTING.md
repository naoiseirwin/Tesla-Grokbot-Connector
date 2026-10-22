# Contributing

This is a read-only Tesla Fleet API connector. Changes that add a vehicle
command, a virtual key, or `tesla-http-proxy` do not belong here.

## Setup

```bash
uv sync
make test
make lint
```

Python 3.11 or newer. Tests use respx and never call Tesla. Do not add a test
that needs a live car. `scripts/live_smoke.py` is opt-in, billed, and stays
out of CI.

## Secrets

- Do not commit `.env`, PEM files, or `.tesla-mcp/`.
- `.env.example` is placeholders only.
- CI runs `gitleaks detect` and `pytest`, including a scan that the `src/`
  tree contains no command endpoint strings other than `wake_up`.

## Pull requests

- Keep the public surface read-only. `tesla_wake_and_refresh` is the only
  wake path, and it stays behind `confirm=true` and the wake budget.
- Location and charging history stay opt-in and off by default.
- If you touch token refresh, keep the rule: persist the new refresh token
  atomically before anything uses the new access token.
- Run `make can-release` before asking for review.

## License

By contributing you agree that your changes are under the MIT license in
[LICENSE](LICENSE).
