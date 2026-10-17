# Peer Chat MCP: two ChatGPT Web sessions (experimental)

**Status: source-only, not deployed, no E2E ChatGPT validation.**
This is deliberately separate from the Shell Bridge (#7 / draft PR #8),
production Atlas, Cloudflare gateway, and existing services.

## Goal

Keep two ordinary ChatGPT Web conversations active *within their existing turns*
while they alternate `chat_send` and a **blocking `chat_wait` tool call**.
The waiting tool returns immediately after the opposite role posts a message,
or returns a bounded timeout (20 seconds maximum). No GUI automation, no LLM
API, and no shell execution. ChatGPT may still end a turn or hit its tool/time
limit; this is the actual behavior we want to measure, not assume away.

```
ChatGPT A -- chat_send(A) --> one-process, in-memory mailbox <-- chat_wait(B) -- ChatGPT B
ChatGPT A -- chat_wait(A) <---                           <--- chat_send(B) -- ChatGPT B
```

## Tools

- `chat_create()` creates a random 128-bit room ID (treat as a temporary secret).
- `chat_send(room_id, sender="A"|"B", message, reply_to=None)` appends an inert
  message and returns a globally increasing `id`.
- `chat_wait(room_id, role="A"|"B", after_id=0, wait_seconds=15)` **blocks** for
  the next message addressed to that role, with `id > after_id`. On timeout,
  it returns `{"status":"timeout"}` instead of ending the ChatGPT turn itself.
- `chat_close(room_id)` closes the room and wakes outstanding waits.

The caller advances `after_id` only when it has actually received a message.
Messages are retained to avoid missing a response that arrives before waiting.
The room is held only in the memory of **one service process**; restarting the
service loses the chat. It is not suitable for multiple replicas.

Bounded by 16 rooms, 64 messages per room, 2,048 UTF-8 bytes per message and 15
minutes of inactivity. Roles are self-declared; the room ID is a capability
identifier, not a per-role authentication mechanism. No confidential content.

## Source tests

Run from repo root:

```bash
python -m unittest discover -s tests -p test_peer_chat.py -v
```

Core tests need only Python stdlib. MCP tool-registration test uses the repo's
`mcp==2.3.0` when installed. Passing unit tests proves only that Python
threads can block and wake, **not** that ChatGPT Web continues after timeouts.

## Manual staging (not performed)

Only after review and secure provisioning, use a **new, isolated** MCP tunnel
with an OAuth/authenticated ChatGPT-facing edge, then on staging host:

```bash
python -m pip install -r requirements-shared.txt
ALFRED_EXPERIMENTAL_PEER_CHAT_ENABLED=1 python -m experiments.peer_chat.mcp_adapter
```

It binds `127.0.0.1:8775` only. Never expose it directly; the edge must be
independently authenticated, rate-limited and origin-controlled, with TLS.
Do not put access tokens or real room IDs in public Issues or repository files.

On the Mac, open two fresh ChatGPT Web conversations, both with the **same
reviewed staging MCP connection**. Start A first, copy its room ID privately to
B, then start B before A has exhausted its waiting tool calls. In both chats,
tell ChatGPT to keep invoking `chat_wait` on `timeout` while the original turn
remains active, and stop after exactly 3 round trips or a finite ceiling (e.g.
6 waits). This is a test instruction, not a guarantee that ChatGPT will comply.

Measure: timestamp of A's send, B's first receive, blocked wait duration, how
many `chat_wait` calls happen in a single turn, whether a new human Enter is
required, error/timeout reason, and whether either session unexpectedly ends.

## Explicit non-goals

No unattended perpetual runs, no browser automation, no automatic reply to GitHub
Issue #9, no execution of text as instructions, no remote shell, no production
changes. Every received `message` must be treated as untrusted data.
