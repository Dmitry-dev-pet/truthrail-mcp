# Alfred Remote Shell — experimental source only

**Goal:** Use a normal terminal on macOS as keyboard + screen for a temporary
**ChatGPT Linux sandbox**. Shell commands are executed by ChatGPT's sandbox
tool, **not** on the Mac or VPS. AI/natural-language mode is deliberately out
of scope.

```text
Mac (Python client, no local shell execution)
  | authenticated HTTPS request: ls
  v
private VPS relay (mailbox only, NO shell)
  ^                    |
  | terminal_wait()    | terminal_reply(result)
  v                    ^
ChatGPT MCP tool calls + ChatGPT's OWN Linux sandbox shell execution
```

## Source-only proof

This folder is NOT deployed and NOT enabled in the live Truthrail/Atlas MCP.
The existing working Atlas OpenAI Secure MCP Tunnel, OAuth Worker and other
production services are not touched.

A self-contained local synthetic protocol test is available:

```bash
python -m unittest discover -s tests -p test_remote_shell.py -v
```

It checks command submission, 20s bounded lease/wait, result retrieval, role
separation, expiry, URL safety, Bash working-directory persistence and exit
codes. The synthetic executor in a unit test is **not** a real connected
ChatGPT session. Passing CI does not prove real remote functionality.

## Components

- `relay.py`: loopback HTTP mailbox, separate randomly generated Mac/agent
  bearer tokens, 5-minute command TTL, max 64 queued/leased commands.
- `mac_client.py`: Python stdlib CLI; does not execute Mac shell.
- `mcp_adapter.py`: MCP tools `terminal_wait` and `terminal_reply`.
  Agent token stays on the server. Needs `mcp==2.3.0`.
- `shell_session.py`: stateful Bash helper intended only inside an active
  ChatGPT Linux execution environment. Combines stderr into stdout.
  Not a true PTY; no interactive stdin, signals, full-screen terminal, or
  live streaming.

## Manual loopback-only exploration

Use only on your own development machine, without exposing the ports:

```bash
export ALFRED_RELAY_MAC_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
export ALFRED_RELAY_AGENT_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
python3 -m experiments.remote_shell.relay
```

From another terminal on **that same host**, with the same Mac token:

```bash
ALFRED_RELAY_URL=http://127.0.0.1:8768 python3 -m experiments.remote_shell.mac_client
```

The client will time out until an independently authorized MCP caller processes
the queue. A real Mac-to-VPS deployment requires reviewed TLS for the Mac
client, a separately authenticated ChatGPT MCP edge (e.g. a distinct secure
tunnel), and credential provisioning **outside GitHub and chat**.

MCP adapter startup is deliberately disabled unless
`ALFRED_EXPERIMENTAL_SHELL_MCP_ENABLED=1`, and loopback only. Nothing in
this source automatically registers a tool in ChatGPT.

## Next validation — NOT YET DONE

1. Security-review the new route and deploy a separate, isolated staging
   instance without touching the existing Atlas/MCP ingress.
2. Connect the staging MCP to a fresh ChatGPT session via the supported UI
   authorization flow and verify actual tool discovery.
3. Run `ls`, `pwd`, `cd`, `pwd` and collect stdout/exit code from the
   **ChatGPT sandbox**, displayed in a **real Mac terminal**.
4. Measure tool timeouts, inactivity, session exhaustion and container
   persistence. A Web ChatGPT session is NOT guaranteed to wait forever,
   autonomously reenter the loop or retain its Linux sandbox across sessions.

**Safety:** never expose the relay unauthenticated, publish credentials, give
the VPS a shell executor, or claim this prototype is a remote terminal until
the end-to-end test actually succeeds.
