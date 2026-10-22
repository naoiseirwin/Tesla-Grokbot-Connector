# Optional vehicle commands

The main connector, `tesla-mcp`, only reads the car. It cannot lock, honk,
start climate, or start charging.

This page is a **second program**, `tesla-commands-mcp`. You add it beside
the read-only one, under its own name, `tesla-commands`. It is **off by
default**. Until you turn it on, every command is refused and nothing is
sent to the car.

Read this whole page before you send a live command. Commands can lock the
car, honk the horn, turn climate on, start or stop charging, and turn
sentry mode on.

## What it can do

| Allowlist name | What the car does |
| --- | --- |
| `climate_on` | Turn cabin climate on |
| `climate_off` | Turn cabin climate off |
| `set_cabin_temperature` | Set cabin temperature, 15 to 28 °C |
| `seat_heater` | Set a seat heater, level 0 (off) to 3 |
| `steering_wheel_heat` | Steering wheel heater on or off |
| `set_charge_limit` | Charge limit, 50 to 100 percent |
| `charge_start` | Start charging |
| `charge_stop` | Stop charging |
| `flash_lights` | Flash the headlights |
| `honk_horn` | Honk the horn |
| `door_lock` | Lock the doors |
| `sentry_on` | Turn sentry mode **on** |

Seat numbers: 0 front left, 1 front right, 2 rear left, 3 rear center,
4 rear right, 5 third row. A number outside 0–5 is refused. If the car
uses a different number for a seat, Tesla's reply explains the rejection.
That reply is shown as Tesla wrote it.

You can put the Tesla endpoint name in the allowlist instead of the short
name. `auto_conditioning_start` is the same as `climate_on`.
`set_sentry_mode` is the same as `sentry_on`, and it still cannot turn
sentry off.

## What it will not do

There is no tool for any of these, and putting the name in the allowlist
does not create one:

- Unlock the doors
- Open the trunk or frunk
- Open or close the windows
- Remote start
- Summon or autopark
- Turn sentry mode off
- Valet mode
- Speed limit
- PIN changes
- Navigation
- Software updates

The read-only connector is unchanged. Running `tesla-mcp` does not add
these command tools, and `tesla-mcp auth` does not ask for permission to
command the car.

## Risks

- A live command changes the car. Honk makes noise. Lock can lock the car
  with the keys inside if that is how the car is set up. Climate and
  charging use energy. Sentry on keeps the cameras watching.
- This program cannot undo a lock by unlocking, and it cannot turn sentry off.
- Three switches have to be on before anything is sent: commands enabled,
  the command named in the allowlist, and dry-run turned off. Leave dry-run
  on until you have seen the reply you expect.
- The daily caps below stop a loop from commanding the car all day. They are
  not a substitute for the allowlist.
- Waking a sleeping car costs money (about $0.02) and uses the same 3-wakes-a-day
  budget as `tesla_wake_and_refresh`. A command to a car that is already
  online does not use a wake. If the wake budget is used up, the command is
  not sent.
- A command itself is about $0.001. Set a billing limit in the Tesla
  developer dashboard anyway.
- After you grant `vehicle_cmds`, the saved token can command the car
  together with the virtual key. The read-only connector still has no
  command tools. The private key is used only by the signing proxy, which
  you start yourself. This program never reads that key.
- To stop commands even if the token still exists, remove the key from the
  car. That is the last section of this page.

## Off until you opt in

| Variable | Default | Meaning |
| --- | --- | --- |
| `TESLA_ENABLE_COMMANDS` | `false` | Master switch. `false` refuses every command. |
| `TESLA_COMMANDS_DRY_RUN` | `true` | `true` checks the request and returns what would be sent. It does not call Tesla. |
| `TESLA_COMMANDS_ALLOWLIST` | empty | Comma-separated names from the table above. Empty means none are allowed. |
| `TESLA_COMMANDS_DAILY_CAP` | `10` | Most commands per day, across all names. `0` to `40`. |
| `TESLA_COMMANDS_PER_COMMAND_DAILY_CAP` | `3` | Most times one command can be sent per day. `0` to `10`. |
| `TESLA_COMMAND_PROXY_URL` | unset | Local signing proxy, for example `https://127.0.0.1:4443`. Required for a live send. |
| `TESLA_COMMAND_PROXY_CA` | unset | The proxy's TLS certificate file, so this program can trust it. |
| `TESLA_COMMAND_PROXY_INSECURE` | `false` | Skip TLS checking. Leave this false and set `TESLA_COMMAND_PROXY_CA` instead. |
| `TESLA_COMMAND_PROXY_ALLOW_REMOTE` | `false` | Leave false. The access token is sent to the proxy URL, so the proxy must be on this computer. |

A number above the maximum is rejected, so a typo cannot remove the cap.
The day resets at midnight in `TESLA_TIMEZONE` (default `UTC`).

Wakes started by a command count toward `TESLA_WAKE_DAILY_CAP` (default 3),
the same counter the read-only wake tool uses.

Copy the variables you need into the process environment or into
`/workspace/.tesla-mcp/config.env`. Do not commit real secrets.

Example, still in dry-run, lock only:

```bash
TESLA_ENABLE_COMMANDS=true
TESLA_COMMANDS_DRY_RUN=true
TESLA_COMMANDS_ALLOWLIST=door_lock
```

## Add the vehicle_cmds scope

Commands need the OAuth scope `vehicle_cmds`. The read-only sign-in never
requests it.

1. In the Tesla developer app, enable the `vehicle_cmds` scope. Leave
   `user_data` off. It is home address and contact info, and neither
   connector requests it.
2. On this computer, set `TESLA_ENABLE_COMMANDS=true`.
3. Sign in with the commands program, not the read-only one:

   ```bash
   uv run --directory /workspace/tesla-grokbot-connector tesla-commands-mcp auth
   ```

   Use Take over for Tesla login or MFA. Do not paste the code, the token,
   or the client secret into chat.

4. The sign-in page should list `vehicle_cmds`. If the token comes back
   without it, commands are refused with a message that names the missing
   scope. Enable the scope on the app and run the command above again.

`tesla-mcp auth` does not add this scope, even if `TESLA_ENABLE_COMMANDS`
is true. If you only run that command, the commands connector will tell
you the scope is missing and will not call the car.

Both programs share one token file,
`/workspace/.tesla-mcp/tokens.json.enc`. A commands sign-in replaces that
file. The read-only tools keep working. They still do not send commands.

## Pair the virtual key with the car

Modern Teslas ignore a command that is not signed by a key the car has
approved. Tesla calls this a virtual key. The public half is the same key
you already host for partner registration. For this project that domain is
`naoiseirwin.github.io`. If you registered a different domain, use that
name everywhere below.

The private half stays in a file on this computer, outside the git repo.
This program does not open that file. The signing proxy does.

1. Confirm the public key is on the web at
   `https://naoiseirwin.github.io/.well-known/appspecific/com.tesla.3p.public-key.pem`.
   Setup for that file is in
   [partner-registration.md](partner-registration.md).
2. Wake the car (open a door, or tap the key card).
3. On the phone that is logged into the same Tesla account, open:

   `https://tesla.com/_ak/naoiseirwin.github.io`

   The Tesla app should open. If you have more than one product, pick this
   car first.
4. Approve the request in the app.
5. The car screen asks you to confirm the new key. Tap your key card on
   the reader the screen points to (on Model 3 and Model Y that is the
   center console, behind the cup holders).
6. On the car: **Controls → Locks → Keys**. You should see a key named
   with the domain.

Until that key is listed, a live command fails. The reply includes Tesla's
own words, often that the command must be signed or that the vehicle
command protocol is required.

## Run the signing proxy

Tesla's supported signer is `tesla-http-proxy` from
[github.com/teslamotors/vehicle-command](https://github.com/teslamotors/vehicle-command).
It holds the private key, signs the command, and forwards it to Tesla.
This program only sends a normal HTTPS request to the proxy on this
computer, with the same OAuth token the read-only connector uses.

Do not point `TESLA_COMMAND_PROXY_URL` at Tesla's Fleet API. That would be
an unsigned request. Modern cars reject those, and the error text from
Tesla is returned as Tesla wrote it. The supported path is the proxy.

The proxy's TLS certificate and the car's command key are **two different
keys**. Tesla's proxy refuses to start if you use the command private key
as the TLS key. Generate the command key with `scripts/generate-keypair.sh`
outside the repo, as in partner registration. Generate the TLS certificate
only for the proxy.

Install the proxy (Go, or Docker if you already use it):

```bash
go install github.com/teslamotors/vehicle-command/cmd/tesla-http-proxy@latest
```

Create a TLS certificate for localhost. This is not the car key:

```bash
mkdir -p "$HOME/tesla-command-proxy"
openssl req -x509 -nodes -newkey ec \
  -pkeyopt ec_paramgen_curve:secp384r1 \
  -pkeyopt ec_param_enc:named_curve \
  -subj '/CN=localhost' \
  -keyout "$HOME/tesla-command-proxy/tls-key.pem" \
  -out "$HOME/tesla-command-proxy/tls-cert.pem" \
  -sha256 -days 3650 \
  -addext "extendedKeyUsage = serverAuth" \
  -addext "keyUsage = digitalSignature, keyCertSign, keyAgreement"
```

Start the proxy. `-key-file` is the command private key from
`scripts/generate-keypair.sh`, not the TLS key:

```bash
tesla-http-proxy \
  -tls-key "$HOME/tesla-command-proxy/tls-key.pem" \
  -cert "$HOME/tesla-command-proxy/tls-cert.pem" \
  -key-file "$HOME/tesla-partner-keys/private-key.pem" \
  -host 127.0.0.1 \
  -port 4443
```

Docker, same files mounted into the container:

```bash
docker run --security-opt=no-new-privileges:true \
  -v "$HOME/tesla-command-proxy:/tls" \
  -v "$HOME/tesla-partner-keys:/keys" \
  -p 127.0.0.1:4443:4443 \
  tesla/vehicle-command:latest \
  -tls-key /tls/tls-key.pem \
  -cert /tls/tls-cert.pem \
  -key-file /keys/private-key.pem \
  -host 0.0.0.0 \
  -port 4443
```

Then, for this program:

```bash
TESLA_COMMAND_PROXY_URL=https://127.0.0.1:4443
TESLA_COMMAND_PROXY_CA=$HOME/tesla-command-proxy/tls-cert.pem
```

Leave `TESLA_COMMAND_PROXY_INSECURE` unset.

The proxy listens only on this computer. Do not publish port 4443. The
access token is sent to whatever URL you configure, which is why a
non-local URL is rejected unless you set
`TESLA_COMMAND_PROXY_ALLOW_REMOTE=true`. Do not set that for normal use.

This project's default region is North America (`TESLA_REGION=na`). The
proxy has to talk to the same region as the account. If Tesla answers
HTTP 421, the reply says so. Check `tesla-http-proxy -h` on the version
you installed for a fleet-host flag, and set `TESLA_REGION` to `na`, `eu`,
or `cn` before you sign in again.

Pre-2021 Model S and Model X do not use the signed command protocol.
Sending through the proxy is still the setup this page describes. If a
command is rejected because it was not signed, you will see Tesla's
sentence in the tool reply and in the audit log.

## Register tesla-commands in Grok Bot

Leave the existing `tesla` connector in place. Add a second stdio command.

| Field | Value |
| --- | --- |
| Name | `tesla-commands` |
| Transport | stdio command on the Grok Bot computer |
| Command | `uv run --directory /workspace/tesla-grokbot-connector tesla-commands-mcp` |

The process needs `TESLA_CLIENT_ID`, `TESLA_CLIENT_SECRET`, and
`TESLA_TOKEN_KEY`, the same way the read-only connector does. Add the
command variables from the table above to that environment or to
`config.env`. Do not put the private key in the connector environment.
Only the proxy process gets `-key-file`.

Attach it with `@` the same way you attach `tesla`. The tool text says
**this acts on the car**, so the approval prompt is about a real action,
not a status check.

`tesla-commands-mcp --help` prints the switches. It does not start the
server.

## From dry-run to live

1. Pair the key and see the domain under Controls → Locks → Keys.
2. Start the proxy. Leave this window running.
3. Set `TESLA_ENABLE_COMMANDS=true`.
4. Set `TESLA_COMMANDS_ALLOWLIST` to the few commands you actually want.
   Start with one quiet command, such as `set_charge_limit`, not `honk_horn`.
5. Leave `TESLA_COMMANDS_DRY_RUN=true`.
6. Ask for that command. The reply describes the HTTP request and says
   nothing was sent. The car should not change.
7. Open the audit log (below) and confirm the line says `"result":"dry_run"`
   and `"live":false`.
8. Set `TESLA_COMMANDS_DRY_RUN=false` and restart the commands connector so
   it picks up the new value.
9. Ask again and approve the prompt. The car should do that one thing.
10. If the reply quotes Tesla, read that sentence. A signing failure means
    the key is not on the car, or the proxy is using a different private
    key than the public key on the domain.

Dry-run does not use a daily command slot and does not wake the car.
A live send does.

## Audit log

Every attempt is appended to:

```text
/workspace/.tesla-mcp/command-audit.jsonl
```

(`TESLA_MCP_DATA_DIR` moves it with the rest of the local data.)

Each line is one JSON object: time, command, parameters, masked VIN,
whether this was a dry run or a live send, the result, Tesla's reason
when there is one, and the refusal reason when it was refused. The file
is mode `600`. It is append-only.

The log does not contain the access token, the refresh token, the client
secret, or the full VIN. A VIN is stored the same way the read-only tools
show it, first three and last four characters.

`tesla-mcp logout` does not delete this file. Delete it yourself if you
want the history gone. Deleting it does not revoke the key on the car.

## Revoke

Stopping commands has two different meanings.

**Stop commands, keep read-only access.** On the car:
**Controls → Locks → Keys**, select the key named with your domain, and
remove it. The car will reject later commands. The read-only connector
can still read battery and mileage. You can also set
`TESLA_ENABLE_COMMANDS=false`, clear `TESLA_COMMANDS_ALLOWLIST`, or remove
the `tesla-commands` connector from Grok Bot. Do those as well. Removing
the key is what stops the car from accepting a signed command.

**Stop the app entirely, including read-only.** Revoke consent at
`https://auth.tesla.com/user/revoke/consent?revoke_client_id=<your client id>`
or in the Tesla app under Security & Privacy → Third-Party Apps. Then run
`tesla-mcp logout`. That removes data access for both connectors, because
they share one app and one token file.

Stop the proxy process when you are not using commands. The private key
file should stay out of git and out of chat.

## If something fails

| What you see | What it means |
| --- | --- |
| Vehicle commands are off | `TESLA_ENABLE_COMMANDS` is not `true`. |
| Not on `TESLA_COMMANDS_ALLOWLIST` | The name is missing, or the list is empty. |
| Does not include `vehicle_cmds` | Sign in with `tesla-commands-mcp auth`, not `tesla-mcp auth`. |
| Charge limit, temperature, or seat was refused | The number is outside Tesla's range. Nothing was sent. |
| Daily cap or per-command cap | The local limit for today is used. It resets at midnight. |
| Wake budget | The car was asleep and the shared 3-a-day wake cap blocked the wake. The command was not sent. |
| `TESLA_COMMAND_PROXY_URL` is not set | Dry-run is off, but the proxy URL is missing. |
| Tesla refused the command, followed by Tesla's sentence | The proxy or the car rejected it. Read that sentence. Signing problems name the vehicle command protocol or an unsigned command. |
| The proxy could not be reached | `tesla-http-proxy` is not running, or the URL or certificate path is wrong. |

A refused call is still written to the audit log, with the reason.
