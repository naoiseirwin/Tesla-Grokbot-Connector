# ADR 0001: Token store, poll interval, and history

## Token store

Refresh tokens are single-use and the previous one stays valid for 24 hours.
`TokenStore` holds a file lock, exchanges the refresh token, and writes the
new record with a temp file, `fsync`, and `rename` before any caller receives
the new access token. A failed write retries the exchange once with the
refresh token that was just presented, not with the unsaved replacement.

The file is AES-GCM with a 32-byte key from `TESLA_TOKEN_KEY`. The key is an
environment variable or a secret, not a file next to the tokens and not
anything in git. The data directory is mode `700` and the token file is mode
`600`.

## Poll interval

`tesla-mcp poll` reads `GET /api/1/vehicles` and calls `vehicle_data` only when
`state` is `online`. The default interval for `--loop` is 2 hours and values
under 1 hour are raised to 1 hour. A scheduled routine should invoke the
one-shot command every 1–2 hours instead of leaving a loop running.

Whether a `vehicle_data` call on an already-awake car delays sleep is
unconfirmed, which is why the default is conservative.

## History

SQLite at `$TESLA_MCP_DATA_DIR/history.db` (mode `600`) stores snapshots, API
usage, and wake outcomes. Location columns are written only when location is
opted in, and only after rounding to about 1 km. Precise coordinates are
returned by `tesla_get_location` when asked and are not stored.

Odometer gaps are spread evenly across the days between readings. Normalized
range is `battery_range_miles * 100 / state_of_charge`.
