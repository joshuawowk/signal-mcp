# signal-mcp

MCP server + reply subscriber that lets any Claude session message
Josh on Signal. Fronts the host's existing `signal-cli` daemon
(`signal-cli-daemon.service`, account `+14013752241`) without
re-registering or bind-mounting its data directory.

## Architecture

```
Claude Desktop ──stdio──> docker exec ──> python3 /app/server.py ─┐
                                                                  │ HTTP JSON-RPC
host: signal-cli-daemon.service  ◀── 127.0.0.1:8080 (synchronous)─┤  (send, version)
                                  ◀── 127.0.0.1:7583 (TCP stream)─┤  (receive notifications)
                                                                  │
       docker container (signal-mcp, restart=unless-stopped)      │
       PID 1: subscriber.py ── persistent TCP socket ─────────────┘
                            └─> append /data/inbox.jsonl  ◀── signal_check_replies reads
```

- **No bind-mount of signal-cli data.** The container is purely a
  client of the host daemon. Signal identity keys never enter the
  container.
- **No re-registration.** The Signal identity stays on the host.
- **Two-channel daemon.** Synchronous calls (send, version, health)
  go over HTTP on `:8080`. Incoming-message notifications stream over
  a persistent TCP socket on `:7583`. The HTTP daemon's `receive`
  method is permanently locked by signal-cli's internal auto-receive
  loop, so TCP is the only path that works for replies.
- **One daemon flag added.** The `--tcp 127.0.0.1:7583` arg was added
  to the existing `signal-cli-daemon.service` ExecStart (see
  "Daemon configuration" below). HTTP behavior for any other
  consumer is unchanged.

## Setup

```bash
cd ~/Repos/signal-mcp
cp .env.example .env       # edit if you need to change defaults
docker compose build
docker compose up -d
docker compose logs -f signal-mcp   # watch the subscriber start up
```

Verify the daemon is reachable from the container:

```bash
docker exec signal-mcp python3 -c \
  "import rpc; print(rpc.ping())"
# expected: 0.14.3
```

## Register the MCP server with Claude Desktop

Merge `claude_desktop_config.snippet.json` into
`~/.config/Claude/claude_desktop_config.json` under `mcpServers`:

```json
"signal-josh": {
  "command": "sg",
  "args": ["docker", "-c", "exec docker exec -i signal-mcp python3 /app/server.py"]
}
```

Restart Claude Desktop.

### Why the `sg` wrapper

`sg docker -c "…"` runs the command under the `docker` supplementary
group. This is required when the Claude Desktop process was started
before `jwowk` was added to the `docker` group — existing sessions
inherit a stale supplementary-group list and `docker exec` returns
`permission denied while trying to connect to the docker API at
unix:///var/run/docker.sock`, which surfaces in Claude Desktop's logs
as `write EPIPE / Server transport closed unexpectedly`.

The `exec` inside the `-c` string replaces the temporary bash spawned
by `sg` with the `docker exec` process, so SIGTERM/SIGPIPE from Claude
Desktop reach the MCP server cleanly when the connection closes.

If you log out + back in (or reboot), all new processes inherit the
`docker` group and the wrapper isn't strictly needed — but it's
harmless to keep, and it makes the config portable across hosts where
the group situation may vary. The `signal-josh` server will appear in the
MCP server list and its tools (`signal_send_message`,
`signal_send_question`, `signal_send_permission_request`,
`signal_check_replies`, `signal_health`) become available to any
session.

## Install the companion skill

The skill lives in `skill/SKILL.md`. Symlink it into the user skills
directory so it's discovered alongside Josh's other skills:

```bash
SKILLS=~/.config/Claude/local-agent-mode-sessions/skills-plugin/60ec4dd2-88cd-4679-a53c-1593176d1ac9/58636bed-2152-4b76-8f90-55b982ef8e3d/skills
mkdir -p "$SKILLS/signal-josh"
ln -sf ~/Repos/signal-mcp/skill/SKILL.md "$SKILLS/signal-josh/SKILL.md"
```

(That path is session-bound, so an alternative is to copy the skill
folder there directly and update it from the repo whenever it changes.)

## End-to-end test from a future Claude session

```
Use signal_health to confirm the daemon is up, then signal_send_message
with text="signal-mcp end-to-end test from session XYZ" and
session_id="signal-mcp-test-001". Then wait 30 seconds and call
signal_check_replies with session_id="signal-mcp-test-001".
```

Josh replies with anything; the next `signal_check_replies` call
should return his message.

## Tools

| Tool | When to call |
| --- | --- |
| `signal_send_message(text, recipient?, session_id?)` | Task-done pings, FYI updates. No reply expected. |
| `signal_send_question(question, options?, recipient?, session_id?)` | Blocked, need an answer. `options` accepts a list `["A","B"]`, a JSON-array string `'["A","B"]'`, or newline/comma-separated text — all normalized server-side. Response echoes `options_parsed`. |
| `signal_send_permission_request(action, context, recipient?, session_id?)` | About to do something irreversible. Wait for explicit YES. |
| `signal_check_replies(since?, from_recipient?, session_id?, limit?)` | Poll for replies. Filter by session_id. |
| `signal_health()` | Full health: send path (daemon HTTP), receive path (TCP port + subscriber process liveness), and inbox status. `ok` is true only when send AND receive are healthy. An empty inbox is normal. Run first if anything fails. |

### Reliability notes (Jun 2026)

- The inbox file (`/data/inbox.jsonl`) is created at subscriber startup and is never deleted by retention pruning — an empty inbox stays as a 0-byte file. Previously, pruning removed the file entirely once all records aged out (14-day retention), which made `signal_check_replies` / `signal_health` look like the subscriber was down. `signal_check_replies` and `signal_health` also ensure the file exists on every call.
- `signal_health` and `signal_check_replies` report `subscriber_running` (scans `/proc` for `subscriber.py`) and `receive_channel_ok` (TCP connect to the daemon's `:7583`), so a fresh session can distinguish "no replies yet" from "receive path broken".
- `signal_send_question` tolerates clients that serialize the `options` list into a string (see tools table).

## Config (.env)

| Variable | Meaning |
| --- | --- |
| `SIGNAL_SENDER` | E.164 of the registered signal-cli account. Default: `+14013752241`. |
| `SIGNAL_DEFAULT_RECIPIENT` | E.164 to ping by default. Default: Josh's number. |
| `SIGNAL_RPC_URL` | Daemon endpoint as seen from the container. With `network_mode: host`, `127.0.0.1:8080` works. |
| `SIGNAL_POLL_INTERVAL` | Subscriber poll cadence in seconds. |
| `SIGNAL_INBOX_RETENTION_DAYS` | Days of replies kept in `inbox.jsonl`. |
| `SIGNAL_INBOX_PATH` | Inbox file path inside the container. |

## Operational

```bash
docker compose ps                       # status
docker compose logs -f signal-mcp       # subscriber output
docker exec signal-mcp tail -f /data/inbox.jsonl   # raw replies
docker compose restart signal-mcp       # bounce the container
docker compose down                     # stop
```

If the host daemon is restarted, the subscriber will reconnect on its
next poll (errors back off up to 60 s).

## Notes & limitations

- **Linux host only.** `network_mode: host` is a no-op on Docker
  Desktop for macOS/Windows. To move it: switch to a bridge network,
  set `SIGNAL_RPC_URL=http://host.docker.internal:8080/api/v1/rpc`,
  and add `extra_hosts: ["host.docker.internal:host-gateway"]`.
- **HTTP daemon polling.** signal-cli's HTTP mode does not stream
  receive events; we poll. A TCP-mode daemon with WebSocket
  subscription would be lower-latency, but would require Josh to
  change his existing systemd service.
- **Attachments are skipped.** The subscriber only persists text
  bodies. Reactions, typing indicators, and read receipts are
  ignored.
- **The phone number is not memorized.** It lives only in `.env` and
  the registration snippet, both inside this project.


## Daemon configuration

This project requires the host's `signal-cli-daemon.service` to expose
both HTTP (for synchronous calls) and TCP (for streaming receive
notifications). The unit file at
`~/.config/systemd/user/signal-cli-daemon.service` should have:

```
ExecStart=/usr/local/bin/signal-cli -a +14013752241 daemon --http 127.0.0.1:8080 --tcp 127.0.0.1:7583 --receive-mode on-start
```

To apply changes:

```bash
systemctl --user daemon-reload
systemctl --user restart signal-cli-daemon.service
```

A backup of the original unit file is written next to the live one
with a `.bak.YYYYMMDD-HHMMSS` suffix the first time signal-mcp is set
up.

### Why not just use HTTP for receive?

signal-cli's HTTP daemon mode is synchronous-only. `receive` over
HTTP always errors with `"Receive command cannot be used if messages
are already being received"` because the daemon's internal receive
loop owns the queue and HTTP has no way to deliver push events. TCP
mode opens a persistent newline-delimited JSON-RPC stream and pushes
`receive` notifications as they arrive — that's the path
`subscriber.py` uses.
