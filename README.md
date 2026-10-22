# tesla-grokbot-connector

Read-only [Tesla Fleet API](https://developer.tesla.com/) MCP for **Grok Bot**.
Each person runs this on **their own Grok Bot computer**. The Bot starts it as
a **stdio command**. There is no public host and no marketplace tile.

**Compatible with Tesla.** This project is not affiliated with, endorsed by,
or sponsored by Tesla, Inc. Do not name your developer app anything that
implies an official Tesla product (use something like “GrokBot Vehicle Bridge”).

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**Install and attach:** follow **[INSTALL.md](INSTALL.md)**. Put the client id,
client secret, and token key on the computer, run `tesla-mcp auth` with Take
over, then add a custom stdio command in chat.

## What it does

Ask how charged the car is, how far it drove this week, or whether range is
dropping. The server can:

- list vehicles (VIN masked)
- read battery, charging, climate, odometer, and software when the car is
  already online
- return the last local snapshot when the car is asleep, without waking it
- keep that history in SQLite and answer weekly mileage, range trend, and a
  low-charge check from it
- wake the car only through `tesla_wake_and_refresh`, and only inside a small
  budget
- optionally poll every 1–2 hours, and only if the car is already online

## What it does not do

- It does not lock, unlock, honk, start climate, or start and stop charging.
  Those endpoints are not in the source. The only Fleet API write is
  `wake_up`, and only from `tesla_wake_and_refresh`.
- It does not use a virtual key or `tesla-http-proxy`.
- It does not stream Fleet Telemetry and it does not talk to Tesla Energy
  products.
- It does not request `vehicle_cmds` or `user_data`.
- It does not wake the car for a status question, a report, or `tesla-mcp poll`.

Charging history is off. Tesla only exposes it through `vehicle_charging_cmds`,
which also allows charge control. Turn it on only if you accept that grant.
This server still will not call charge control.

## How this compares to other Tesla MCP servers

Several open-source Tesla MCP servers already exist. The notes below are from
their READMEs on 22 Oct 2026.

- [scald/tesla-mcp](https://github.com/scald/tesla-mcp) is a small TypeScript
  MCP server for Claude Desktop. Its tools wake the car and return basic
  vehicle info (`wake_up`, `refresh_vehicles`, `debug_vehicles`).
- [ysrdevs/tesla-mcp](https://github.com/ysrdevs/tesla-mcp) is Python. The
  README describes 96 tools covering the Fleet API, including lock/unlock,
  climate, and charging commands.
- [MyTeslaMate/mcp-tesla](https://github.com/MyTeslaMate/mcp-tesla) is mainly
  a hosted service: you sign in through `https://mcp.myteslamate.com/mcp`.
  The README describes full vehicle-command access plus TeslaMate tools
  (98 Fleet API tools and 9 TeslaMate tools). Self-hosting is documented as
  an advanced option.

This project is built for Grok Bot. It is strictly read-only: the source has
no command tools, no virtual key, and no `tesla-http-proxy`. It runs locally
on the Grok Bot computer as a stdio process, with no hosted server. Wakes go
through one tool with a local budget, so Fleet API wake charges stay capped.
Mileage, range, and low-charge answers come from a SQLite history on that
same computer.

## Cost and billing

Prices from the [Fleet API pricing page](https://developer.tesla.com/), checked
22 Oct 2026:

| Category | Price | Each |
| --- | --- | --- |
| Data | 500 / $1 | $0.002 |
| Wakes | 50 / $1 | $0.02 |
| Commands (unused) | 1,000 / $1 | $0.001 |

Authentication calls are not billed. Responses under HTTP 500 are billed,
including 408 when the car is asleep. There is no sandbox. A live call hits
your car and your bill.

A 2-hour poll is about 360 state checks a month, plus about 180 data reads if
the car is online half the time ($1.08). About 10 questions a day add $0.60.
One wake a day adds $0.60. That is about **$2.30 / month**, inside the **$10
monthly discount**, so the net is often $0. Which category
`GET /vehicles/{vin}` and charging history use is per-endpoint in Tesla's docs
and is treated as data here until you confirm it.

**Set a billing limit around $10** and add a card. The dashboard default is
$0, and an app with no payment method is disabled. Alerts arrive at 80% and
100%. Crossing the limit suspends the API. Payment works in the US and most of
Europe, including IE and GB.

Wakes are also capped by Tesla at **3 per minute**. A loop at that cap is
about $86 a day. This server defaults to **3 wakes a day** and **1 wake per
30 minutes**, and it will not let you configure more than 3 per minute.

`tesla_usage` shows the local estimate. It is not Tesla's invoice.

## Tools

| Tool | What you get |
| --- | --- |
| `tesla_list_vehicles` | Masked VIN, name, model when Tesla sends it, `state` |
| `tesla_get_status` | Battery %, range, charging, plugged in, locked. Live or cached. |
| `tesla_get_climate` | Inside and outside °C, climate on, set points |
| `tesla_get_odometer` | Odometer miles, optional `7d` / `30d` / `this_week` from history |
| `tesla_get_software` | Installed version and pending update fields |
| `tesla_query_history` | Local time series |
| `tesla_report` | `weekly_mileage`, `range_trend`, `charging_summary`, `low_charge` |
| `tesla_wake_and_refresh` | Fresh snapshot. Requires `confirm=true`. Budget-gated. |
| `tesla_usage` | Calls this month, estimated USD, wake budget |
| `tesla_get_location` | Opt-in. Coarse (~1 km) unless `precision=precise`. |
| `tesla_get_charging_history` | Opt-in. Sessions from the charging history API. |

`vin` is optional when the account has one car. Pass a display name or the
masked VIN when there are several. Distances from the Fleet API are miles.
Temperatures are Celsius. Every tool is marked read-only. `tesla_wake_and_refresh`
is also marked non-idempotent because it costs money and wakes the car.

## Scopes

| Scope | Default |
| --- | --- |
| `openid`, `offline_access`, `vehicle_device_data` | On |
| `vehicle_location` | Off. Set `TESLA_ENABLE_LOCATION=true` and sign in again. |
| `vehicle_charging_cmds` | Off. Set `TESLA_ENABLE_CHARGING_HISTORY=true` and sign in again. |
| `vehicle_cmds`, `user_data` | Never requested |

Granted scopes are read from the access token at startup. A tool whose scope
was not granted is not registered. Run `tesla-mcp scopes` to see the diff.
Changing scopes needs `tesla-mcp auth` again (`prompt_missing_scopes` is set).

## Poll and routines

`tesla-mcp poll` lists vehicles and, only when `state` is `online`, reads
`vehicle_data`. Asleep and offline cars get a state-only row. It never calls
`wake_up`.

Ask the Bot to run this every 1–2 hours:

```bash
uv run --directory /workspace/tesla-grokbot-connector tesla-mcp poll
```

`tesla-mcp poll --loop` stays in the foreground. Intervals under 1 hour are
raised to 1 hour. A routine that runs the one-shot command is the better fit.

Example routines, all of them using the last snapshot rather than a wake:

- **Weekly mileage (Monday 08:00):** `tesla_report` with `weekly_mileage`.
  Gaps while the car slept are split evenly between readings.
- **Range trend:** `tesla_report` with `range_trend`. Rated range is scaled
  to 100% charge (`range / SoC`) over 30 and 90 days.
- **Low charge (21:00):** `tesla_report` with `low_charge`. Alerts under 30%
  when the car is unplugged. If the snapshot is older than 6 hours, the report
  says so.
- **Charging summary (1st of the month):** only after the charging-history
  opt-in. Compares the previous month with the one before it.

## Sign-in and token file

| Place | Path |
| --- | --- |
| Data directory on the Grok Bot computer | `/workspace/.tesla-mcp/` |
| Encrypted tokens | `/workspace/.tesla-mcp/tokens.json.enc` (mode 600) |
| History | `/workspace/.tesla-mcp/history.db` (mode 600) |
| Config | `/workspace/.tesla-mcp/config.env` or the process environment |
| Override | `TESLA_MCP_DATA_DIR` |

Client id and secret come from a secret-request or from `config.env`. The AES
key comes from `TESLA_TOKEN_KEY` and is not written into the token file.
Refresh tokens are single-use. The new refresh token is saved atomically
before the new access token is used. The previous refresh token stays valid
for 24 hours. A routine at least once a week keeps the chain alive (the poll
does this whenever it runs). `401 login_required` means run `tesla-mcp auth`
again.

Every Bot on your Cursor account shares this computer. Encryption keeps the
tokens unreadable without the key. It does not isolate one Bot from another
Bot that can read the same secret. That is acceptable here because the scopes
are read-only. Say so once.

Regions: `TESLA_REGION=na`, `eu`, or `cn`. A wrong region returns HTTP 421.
Details: [docs/partner-registration.md](docs/partner-registration.md).

## Privacy

- Location is off unless you opt in. The default precision is about 1 km.
  Precise coordinates are not written to SQLite. Logs do not contain
  coordinates or tokens.
- Logs are tool name, masked VIN, status, and cost category.
- `tesla-mcp logout` deletes the token file. Add `--delete-history` to delete
  snapshots as well.
- Revoke the app at
  `https://auth.tesla.com/user/revoke/consent?revoke_client_id=<id>`
  or in the Tesla app under Security & Privacy → Third-Party Apps.
- The billing limit is a hard spend cap.

See [SECURITY.md](SECURITY.md).

## Development

```bash
uv sync
make test
make lint
```

Tests mock the Fleet API. They do not call Tesla. `scripts/live_smoke.py`
refuses to run unless `TESLA_LIVE_SMOKE=1`, and it still does not wake the car.
Expect a few data calls, well under $1.

## Before you turn this on

1. Create the developer app, host the public key, and register the partner
   domain. [docs/partner-registration.md](docs/partner-registration.md).
2. Add a card and set a billing limit around $10.
3. Decide region (`na`, `eu`, or `cn`), whether location is in, and whether
   the charging-history scope is worth the broader grant.
4. Confirm localhost is accepted as a redirect URI. If it is not, use
   `tesla-mcp auth --paste` with the Pages `callback.html`.

Unconfirmed with Tesla, as of this version: `github.io` as a partner domain,
the exact `vehicle_data` `endpoints` names, software-update field names on
every firmware, whether home charging appears in charging history, and whether
reading an already-awake car delays sleep. Virtual-key-free `vehicle_data` is
what Tesla's proxy documents for that endpoint; confirm it on your car.

## License

MIT. Copyright (c) 2026 Naoise Irwin. See [LICENSE](LICENSE).
