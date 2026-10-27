# Tesla developer app and partner registration

This connector does not register an app for you. Create your own app at
[developer.tesla.com](https://developer.tesla.com/). Do not give it a name that
sounds like an official Tesla product. "GrokBot Vehicle Bridge" is a reasonable
display name.

China uses a separate app on [developer.tesla.cn](https://developer.tesla.cn/)
and needs a +86 phone number.

## Scopes to enable on the app

Request only what you will use:

| Scope | When |
| --- | --- |
| `openid`, `offline_access` | Always. Required for sign-in and refresh. |
| `vehicle_device_data` | Always. Live vehicle data. |
| `vehicle_location` | Only if you set `TESLA_ENABLE_LOCATION=true`. |
| `vehicle_charging_cmds` | Only if you set `TESLA_ENABLE_CHARGING_HISTORY=true`. This scope also allows charge start and stop. This server still has no charge-control tool. |
| `user_data` | Do not enable. It exposes home address and contact info. |
| `vehicle_cmds` | Leave off for the read-only connector. Enable it only if you are setting up the optional commands program. [docs/COMMANDS.md](COMMANDS.md). |

Allowed origins must include the root of the domain you register. Redirect
URIs:

- `http://localhost:8765/callback` for the normal sign-in. A Tesla maintainer
  has said localhost works for personal use. It is not in Tesla's official
  redirect examples, so confirm it on your app.
- `https://<domain>/callback.html` if you use the paste fallback.

## Public key

Tesla requires a P-256 public key at:

`https://<domain>/.well-known/appspecific/com.tesla.3p.public-key.pem`

The domain must match the root of an allowed origin. Generate the key outside
the repo:

```bash
scripts/generate-keypair.sh "$HOME/tesla-partner-keys"
```

Host the public key only. See [github-pages/README.md](github-pages/README.md)
for a GitHub Pages layout, including `.nojekyll`. `github.io` may be rejected
because it is a public-suffix domain. That is unconfirmed. A domain you own is
the fallback.

The read-only server does not read the private key. The optional commands
program does not read it either. Tesla's `tesla-http-proxy` does, if you
follow [docs/COMMANDS.md](COMMANDS.md). Do not commit the private key.

## Register once per region

Use a client-credentials token, then register the domain. Client id and secret
come from the environment, not from the chat transcript.

```bash
export TESLA_CLIENT_ID=...
export TESLA_CLIENT_SECRET=...
export TESLA_REGION=eu   # na, eu, or cn
uv run --frozen --directory /workspace/tesla-grokbot-connector tesla-mcp register-partner \
  --domain naoiseirwin.github.io
```

Repeat with the other region only if you have a car there. The command prints
HTTP status codes and the first line of the public key Tesla stored. It does
not print the partner token.

Check: `GET /api/1/partner_accounts/public_key?domain=<domain>` is what the
command calls after registration.

## Billing

Add a card in the developer dashboard and set a limit around **$10**. The
default limit is $0, and an app with no payment method is disabled. Alerts
fire at 80% and 100%. Going over the limit suspends the API. Payment works in
the US and most of Europe, including IE and GB.

Authentication calls are not billed. Data calls are 500 per $1. Wakes are 50
per $1. Every response under HTTP 500 is billed. There is no sandbox.

## Region base URLs

| Code | Base URL |
| --- | --- |
| `na` | `https://fleet-api.prd.na.vn.cloud.tesla.com` |
| `eu` | `https://fleet-api.prd.eu.vn.cloud.tesla.com` |
| `cn` | `https://fleet-api.prd.cn.vn.cloud.tesla.cn` |

A wrong region returns HTTP 421. This server does not retry another region.
The account must have signed in to the Tesla mobile app at least once.
