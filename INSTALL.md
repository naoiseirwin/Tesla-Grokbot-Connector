# Install tesla-grokbot-connector for Grok Bot

This is a **read-only Tesla Fleet API MCP**. Each person runs it on **their
own Grok Bot computer**. Grok Bot starts it as a **stdio command**. There is
no public host and no marketplace tile.

**Compatible with Tesla.** This project is not affiliated with, endorsed by,
or sponsored by Tesla, Inc.

Repo: [github.com/naoiseirwin/Tesla-Grokbot-Connector](https://github.com/naoiseirwin/Tesla-Grokbot-Connector).

## 1. Prerequisites

You need all of these before Grok Bot can see the car.

### Your own Tesla developer app

1. A Tesla account with a verified email and MFA. Sign in to the Tesla mobile
   app at least once, or Fleet API calls return an unknown user.
2. Create an application **you own** at
   [developer.tesla.com](https://developer.tesla.com/). China uses
   [developer.tesla.cn](https://developer.tesla.cn/).
3. **Display name** must not imply an official Tesla product. Use something
   like **GrokBot Vehicle Bridge**.
4. Enable `openid`, `offline_access`, and `vehicle_device_data` only, unless
   you have decided to opt in to location or charging history.
   See [docs/partner-registration.md](docs/partner-registration.md).
5. Redirect URI: `http://localhost:8765/callback`. Add
   `https://<your-domain>/callback.html` as well if you want the paste fallback.
6. Host the partner public key and register the domain once per region.
   Generate the key **outside this repo**:

   ```bash
   scripts/generate-keypair.sh "$HOME/tesla-partner-keys"
   ```

7. In the Tesla developer dashboard, add a payment method and set a billing
   limit around **$10**. The default limit is $0 and disables the app.

This repo will not register a Tesla app for you.

Keep the **client id**, **client secret**, and **token key** off chat. You put
them on the computer in step 3.

### On the Grok Bot computer

- A Grok Bot cloud computer (the Bot runs commands there; files persist for
  your Cursor account).
- [uv](https://github.com/astral-sh/uv) (Python 3.11+).

## 2. Clone on the Grok Bot computer

Install **on the Grok Bot cloud computer**, not on your laptop. Checkout path:

`/workspace/tesla-grokbot-connector`

```bash
git clone https://github.com/naoiseirwin/Tesla-Grokbot-Connector.git /workspace/tesla-grokbot-connector
```

Later steps use `uv run --directory /workspace/tesla-grokbot-connector …`.

`uvx` form, after the repo is public:

```bash
uvx --from git+https://github.com/naoiseirwin/Tesla-Grokbot-Connector tesla-mcp
```

Tokens still live on this computer, under `/workspace/.tesla-mcp/`, not inside
the checkout.

## 3. Put the client id, secret, and token key on the computer

Never paste `TESLA_CLIENT_ID`, `TESLA_CLIENT_SECRET`, `TESLA_TOKEN_KEY`, or
tokens into Grok Bot chat.

Do one of these on the computer:

- A Grok Bot **secret-request**, if the Bot offers one, written onto the
  computer (not into the transcript).
- Write `/workspace/.tesla-mcp/config.env` (mode `600`) for the client id,
  secret, and region. **Do not put `TESLA_TOKEN_KEY` in that file.** Hold the
  key in the environment of the MCP process.

  ```
  TESLA_CLIENT_ID=your_client_id_here
  TESLA_CLIENT_SECRET=your_client_secret_here
  TESLA_REGION=eu
  ```

  ```bash
  chmod 700 /workspace/.tesla-mcp
  chmod 600 /workspace/.tesla-mcp/config.env
  ```

The shape is [.env.example](.env.example). Placeholders only.

Generate a key on a private terminal if you need one. Do not paste it into
chat:

```bash
uv run --directory /workspace/tesla-grokbot-connector tesla-mcp gen-key
```

Set `TESLA_REGION` to `na`, `eu`, or `cn`.

Optional, both default **off**:

```
TESLA_ENABLE_LOCATION=true
TESLA_ENABLE_CHARGING_HISTORY=true
```

`TESLA_ENABLE_CHARGING_HISTORY` requests `vehicle_charging_cmds`. That scope
lets an app start and stop charging. This server does not expose those calls.
Leave it unset unless you have decided the grant is acceptable.

## 4. Authorize: `tesla-mcp auth`

```bash
uv run --directory /workspace/tesla-grokbot-connector tesla-mcp auth
```

What happens:

- A browser **on this computer** opens Tesla’s consent screen.
- The wizard listens on `http://127.0.0.1:8765/callback` for five minutes.
- If Tesla asks for a password or MFA, use **Take over**. Do not paste the
  redirect URL or the code into chat.
- Tokens land in `/workspace/.tesla-mcp/tokens.json.enc` (mode `600`),
  encrypted with `TESLA_TOKEN_KEY`.

Paste fallback, if localhost is not an allowed redirect. Host
[docs/github-pages/callback.html](docs/github-pages/callback.html) on your
domain, set `TESLA_REDIRECT_URI` to that URL, then:

```bash
uv run --directory /workspace/tesla-grokbot-connector tesla-mcp auth --paste
```

Paste the code into **this terminal**, not into chat. The code is short-lived.

`tesla-mcp auth --stdio` is accepted and does the same thing. There is no HTTP
transport.

All Grok Bots on your Cursor account share this computer. The token file is
encrypted. Any Bot that also has `TESLA_TOKEN_KEY` can decrypt it. Isolation
is between Cursor users, not between your Bots.

## 5. Add the custom MCP command in Grok Bot chat

Do **not** open Add-connector / Plugins. In Grok Bot chat, send:

> Add a custom MCP server called tesla that runs: `uv run --directory /workspace/tesla-grokbot-connector tesla-mcp`

`uvx`:

> Add a custom MCP server called tesla that runs: `uvx --from git+https://github.com/naoiseirwin/Tesla-Grokbot-Connector tesla-mcp`

The Bot should repeat the **name**, **command**, and **args**. Click **Add it**.

The stdio process needs `TESLA_CLIENT_ID`, `TESLA_CLIENT_SECRET`, and
`TESLA_TOKEN_KEY` in its environment, or it needs to be able to read
`/workspace/.tesla-mcp/config.env` for the client id and secret. The token key
stays in the environment.

| Field | Value |
| --- | --- |
| Name | `tesla` |
| Transport | stdio command on the Grok Bot computer |
| Command (checkout) | `uv run --directory /workspace/tesla-grokbot-connector tesla-mcp` |
| Command (uvx) | `uvx --from git+https://github.com/naoiseirwin/Tesla-Grokbot-Connector tesla-mcp` |

## 6. Attach with `@` and ask

Type `@` and attach `tesla`. Then:

- “How charged is the car?”
- “How far did I drive this week?”

Those call `tesla_get_status` and `tesla_report`. Neither wakes the car.
If the car is asleep you get the last snapshot. Ask for a wake only when you
mean it. The Bot must pass `confirm=true` to `tesla_wake_and_refresh`.

Optional poll, every 1–2 hours, no wake:

```bash
uv run --directory /workspace/tesla-grokbot-connector tesla-mcp poll
```

After a month of polling, `tesla_usage` should still show a wake count of 0
unless you asked for a wake.

## 7. What not to do

- Do **not** use Grok Bot’s **Add-connector** / Plugins marketplace for this.
- Do **not** paste client secrets, the token key, access tokens, or refresh
  tokens into chat.
- Do **not** add a public URL for the MCP server. Cursor’s connector backend
  cannot use `localhost` on the computer as a remote connector. This project
  is a stdio command.
- Do **not** put this only in a laptop `mcp.json` and expect Grok Bot to pick
  it up. Attach it by asking Grok Bot to add the stdio **command**.
- Do **not** name the Tesla app as if it were an official Tesla product.
- Do **not** enable `vehicle_cmds` for the read-only `tesla` connector.
  That scope is only for the optional commands program, which is off by
  default. See [docs/COMMANDS.md](docs/COMMANDS.md).
- Do **not** commit `.env`, `*.pem`, or `/workspace/.tesla-mcp/`.

## 8. Revoke / uninstall

1. Revoke consent:
   `https://auth.tesla.com/user/revoke/consent?revoke_client_id=<your client id>`
   or Tesla app → Security & Privacy → Third-Party Apps.
2. On the computer:

   ```bash
   uv run --directory /workspace/tesla-grokbot-connector tesla-mcp logout --delete-history
   ```

3. Remove the custom MCP server named `tesla` from Grok Bot if you added one.
4. Optional: delete the checkout `/workspace/tesla-grokbot-connector`.

Details: [SECURITY.md](SECURITY.md).

## See also

- [README.md](README.md) — tools, cost, privacy
- [docs/partner-registration.md](docs/partner-registration.md) — app, key, billing
- [skills/grokbot-tesla/SKILL.md](skills/grokbot-tesla/SKILL.md) — the same
  install path for the Bot
