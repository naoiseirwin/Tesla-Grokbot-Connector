# Security

This MCP talks to the Tesla Fleet API with **your** developer application and
**your** OAuth tokens. Treat that material as private.

This project is not affiliated with Tesla, Inc.

## What is private

- Tesla client id and client secret
- `TESLA_TOKEN_KEY`
- Access tokens and refresh tokens
- Vehicle data returned by the tools, including location if you opted in

Do not commit `.env`, `.env.*` (except `.env.example`), `*.pem`, `*.key`, or
`.tesla-mcp/`. Do not paste those values into Grok Bot chat. If Grok Bot
offers a secret-request, use that for the client id, client secret, and token
key. Never send an access token, refresh token, or authorization code to the
Bot.

CI runs gitleaks and a test that rejects private-key PEM headers in the tree.

## Token file

Default location on a Grok Bot computer:

```
/workspace/.tesla-mcp/tokens.json.enc
```

The directory is mode `700`. The file is mode `600`. Contents are AES-GCM.
The key is `TESLA_TOKEN_KEY` (32 bytes, urlsafe base64) from the environment,
not from the file and not from git.

Refresh tokens are single-use and expire after about 3 months. Tesla keeps
the previous refresh token valid for 24 hours. This server writes the new
refresh token atomically (temp file, fsync, rename) before it uses the new
access token. If that write fails, it retries once with the refresh token it
just presented.

Access tokens are refreshed when they are within 60 seconds of `expires_in`.
Tesla does not document a single lifetime in the token guide; 8 hours is the
value this server assumes when `expires_in` is missing.

Every Grok Bot on your Cursor account shares that computer. A Bot that can
read the token file **and** `TESLA_TOKEN_KEY` can decrypt the tokens.
Encryption limits casual exposure. It does not isolate one of your Bots from
another. The read-only connector does not request command scopes. The
optional commands program can request `vehicle_cmds`, and only when
`TESLA_ENABLE_COMMANDS=true`. After that grant, the token is enough to
command the car if the virtual key is also paired and the signing proxy is
running. That is a different risk from the read-only install. See
[docs/COMMANDS.md](docs/COMMANDS.md). Removing the key from the car stops
commands even if the token file remains.

`history.db` is mode `600`. It stores VINs and vehicle snapshots. Location is
stored only when `TESLA_ENABLE_LOCATION` is on, and only rounded to about
1 km. Precise coordinates are not stored. Logs do not include tokens, raw
payloads, or coordinates.

## Scopes

Requested by default: `openid`, `offline_access`, `vehicle_device_data`.

`vehicle_location` and `vehicle_charging_cmds` are off unless you set the
matching environment flag and sign in again. `vehicle_charging_cmds` is the
scope Tesla uses for charging history **and** for starting and stopping a
charge. The read-only server has no charge-control function. `user_data` is
never requested. `vehicle_cmds` is not requested by `tesla-mcp auth`. The
optional commands connector requests it only when `TESLA_ENABLE_COMMANDS=true`.

The server reads the `scp` claim at startup and does not register a tool
whose scope is missing.

The read-only Fleet client refuses any path that is not list, one vehicle,
`vehicle_data`, `wake_up`, or charging history. `wake_up` is rejected unless
the caller is the wake tool. The poller constructs a client that cannot wake.
Command sends, when you opt in, go to a local `tesla-http-proxy` from the
separate `tesla-commands` connector. They are not added to the read-only
client. See [docs/COMMANDS.md](docs/COMMANDS.md).

## Revoke access

1. Open `https://auth.tesla.com/user/revoke/consent?revoke_client_id=<your client id>`
   or Tesla app → Security & Privacy → Third-Party Apps.
2. `tesla-mcp logout` deletes the token file. Add `--delete-history` to
   delete `history.db` as well.
3. Remove the custom MCP server from Grok Bot if you added one.
4. The billing limit in the Tesla developer dashboard is a hard spend cap.
   Set it around $10 before the first live call.

## Reporting

Please do not file vehicle dumps, live tokens, or key material in GitHub
issues. Describe the endpoint and the HTTP status. If you believe you found
a way for this server to send a vehicle command other than `wake_up`, open
an issue with the file and the call, without tokens.
