---
name: grokbot-tesla
description: >-
  Attach this user's Tesla account to Grok Bot via a read-only stdio MCP
  command on the cloud computer. Use when the user wants battery, charging,
  climate, odometer, or trip history from their car. Do not use the
  Add-connector marketplace. Never ask for Tesla tokens in chat. Do not send
  vehicle commands. Do not wake the car unless the user explicitly confirms.
---

# Grok Bot Tesla connector

Read-only Tesla Fleet API MCP. The product is a **stdio command Grok Bot
starts on the cloud computer**. It is not a marketplace connector and not a
public HTTPS host.

Compatible with Tesla. Not affiliated with Tesla. The developer app must not
be named like an official Tesla product (example: "GrokBot Vehicle Bridge").

## Hard rules

- Do **not** open Add-connector / Plugins marketplace.
- Do **not** add a public URL, ngrok tunnel, or hosted MCP endpoint.
- Do **not** ask for an access token, refresh token, client secret, token key,
  or authorization code in chat.
- Do **not** register a Tesla developer application on the user's behalf.
- Do **not** call anything except the read tools and, when the user explicitly
  confirms a wake, `tesla_wake_and_refresh` with `confirm=true`.
- Do **not** enable `TESLA_ENABLE_CHARGING_HISTORY` unless the user
  understands that `vehicle_charging_cmds` also allows charge control at
  Tesla's API. This server still has no charge-control tool.
- Do **not** enable location unless the user asks. Default precision is coarse.
- Do **not** poll in a loop that wakes the car. `tesla-mcp poll` never wakes.
- For Tesla login or MFA, tell the user to **Take over** the computer
  browser. They must not paste passwords or the OAuth code into chat.
- Client id, client secret, and `TESLA_TOKEN_KEY`: secret-request or env on
  the MCP server entry. Never the transcript. Do not run `tesla-mcp gen-key`
  and paste the key into chat.

## What the Bot should do

1. Confirm the user already has (or will create) **their own** Tesla developer
   app. Scopes: `openid`, `offline_access`, `vehicle_device_data`. Redirect:
   `http://localhost:8765/callback`. Billing limit about $10. Partner public
   key hosted at `/.well-known/appspecific/com.tesla.3p.public-key.pem`.
   Steps: `docs/partner-registration.md` in this repo.
2. Clone onto the computer if it is not already there:

   ```bash
   git clone https://github.com/naoiseirwin/Tesla-Grokbot-Connector.git /workspace/tesla-grokbot-connector
   ```

3. Put `TESLA_CLIENT_ID`, `TESLA_CLIENT_SECRET`, and `TESLA_TOKEN_KEY` on the
   computer. Region in `/workspace/.tesla-mcp/config.env` (`na`, `eu`, or
   `cn`). Then:

   ```bash
   uv run --directory /workspace/tesla-grokbot-connector tesla-mcp auth
   ```

   A browser on **this computer** opens. Tell the user to Take over if Tesla
   asks for a password or MFA. Tokens land in
   `/workspace/.tesla-mcp/tokens.json.enc`. Do not print token values.

4. Ask the user to send this chat message (or send it yourself as the add
   request and wait for their confirmation):

   > Add a custom MCP server called tesla that runs: `uv run --directory /workspace/tesla-grokbot-connector tesla-mcp`

5. Repeat the **name**, **command**, and **args**. Wait for them to click
   Add it.
6. After tools appear, tell them to type `@` and attach `tesla`.
   Smoke with `tesla_list_vehicles`, then `tesla_get_status`. If the car is
   asleep, report the cached snapshot and do not wake it.

## Attach details

| Field | Value |
| --- | --- |
| Name | `tesla` |
| Transport | stdio command on the Grok Bot computer |
| Command | `uv` `run` `--directory` `/workspace/tesla-grokbot-connector` `tesla-mcp` |
| Token file | `/workspace/.tesla-mcp/tokens.json.enc` |
| Default scopes | `openid offline_access vehicle_device_data` |

## Wakes and reports

- Status, climate, odometer, software, history, and reports do not wake the car.
- `tesla_wake_and_refresh` needs `confirm=true`. The default budget is 3 wakes
  a day and one every 30 minutes. Do not retry in a loop when the budget says no.
- A scheduled poll is `tesla-mcp poll` every 1–2 hours. It must not be wrapped
  in a wake.
- Low charge at 21:00 uses `tesla_report` type `low_charge` and the last
  snapshot only.
- `tesla_get_charging_history` is opt-in. `start` and `end` can be `YYYY-MM-DD`
  or ISO 8601. Omit them for the last 365 days. A range longer than a year is
  fetched in consecutive 365-day windows, up to 10 years. It does not wake the
  car and it does not start or stop charging. A Tesla rejection includes
  Tesla's explanation.

## Shared computer

All Bots on this Cursor account share the computer. Say that once. The token
file is encrypted with `TESLA_TOKEN_KEY`. Another Bot that has the key can
read it. Scopes are read-only.
