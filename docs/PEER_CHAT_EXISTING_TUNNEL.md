# Two native ChatGPT Web sessions on the existing Atlas Secure MCP Tunnel

**Source-only experiment. Not yet deployed.** The current live combined
Truthrail + canonical Atlas container still advertises the original five
read-only Atlas tools. Neither the existing tunnel ID, the active Atlas app,
nor its read-only credentials are modified by this PR.

## Why integrate into the embedded server?

The user's existing VPS has ~458 MiB RAM, nontrivial swap usage, and an
extra 176 MiB shadow container was already blocked by a memory safety gate.
The newer deployed single-process MCP package provides Atlas **inside the same
process**. Do not run a second shared gateway process or proxy Atlas through
another HTTP hop merely to add chat. The older optional separate-gateway draft
at `infra-mcp-gateway PR #18` remains source reference only.

```text
Mac window A: ChatGPT ─┐
                      ├─ same connected ChatGPT app → same OpenAI Secure MCP tunnel
Mac window B: ChatGPT ─┘                            │
                                       existing tunnel-client sidecar
                                                   │
                                    existing combined Truthrail MCP app
                                    ├─ canonical Atlas, 5 read-only tools
                                    └─ opt-in in-memory chat, 4 tools
```

## Activation contract

- Default `ALFRED_PEER_CHAT_MCP_ENABLED` unset: exactly five Atlas tools
  and unchanged input schema, responses and source/provenance fields.
- Enabled only in a **reviewed release** by setting
  `ALFRED_PEER_CHAT_MCP_ENABLED=1`: add `chat_create`, `chat_send`,
  `chat_wait` and `chat_close` in the **same** MCPServer process.
- `chat_wait(room_id, role, after_id, wait_seconds)` blocks up to 20 seconds
  and wakes upon an opposite-role message. The caller can try another bounded
  tool call in the *same* ChatGPT turn, but ChatGPT may still stop. It is not
  a push notification and does not prove infinite autonomous execution.
- Only inert text messages, no code execution, tool-call dispatch, shell,
  GitHub polling, new network route, LLM API or Mac automation.
- Per-process ephemeral memory only (restart loses rooms), at most 16 rooms,
  64 messages/room, 2048 UTF-8 message bytes, 15-minute idle TTL; multiple
  workers/replicas would not share state.
- The shared room ID is an access capability, **not per-role identity auth**.
  The transport must remain the existing authenticated ChatGPT app/tunnel.
  Do not publish room IDs or sensitive peer messages to public repositories.
  Incoming peer text is untrusted data, never an instruction.

`chat_send` and `chat_close` are **write** operations and may require native
ChatGPT approval. Do not mislabel them read-only to bypass user confirmation.

## Validation

- Source CI tests exact original five input schemas and all five pinned,
  real-public-Atlas data replies with the flag **off and on**.
- Hosted CI launches a source-built non-root combined image, verifies five
  tools over real loopback HTTP with flag off, removes it, then launches a
  separate **disposable** combined image with chat enabled, checks nine tool
  names and a two-independent-client blocking wait/send wake-up via HTTP.
- This is not evidence of actual ChatGPT Web session lifecycle behavior.

## Release/deployment gates

1. Review this PR and its CI, with all existing native Atlas tests still green.
2. Publish a separately pinned **new** release using the already reviewed
   one-container Atlas package path. Never reuse the current release tag or
   assume an unmerged source SHA is current live image.
3. Review the exact immutable image digest, memory headroom, non-root flags,
   last known-good image and tunnel-preserving app-only rollback. The current
   `infra-access` release guard pins an *older exact candidate* and must not
   silently accept a different image SHA/labels. New guard review required.
4. Only after separate explicit owner approval for the **exact new image**
   use existing operator-gated deployment, keeping the existing app, port 8765,
   tunnel-client ID and original runtime credentials.
5. Refresh existing ChatGPT app tool discovery, verify all five real Atlas
   tools and four chat tools, then test three A↔B rounds from two Mac ChatGPT
   Web windows with no further Enter. Record early turn endings and timeouts.
6. Roll back only via a reviewed, exact tunnel-preserving prior-image deploy
   if a live tool call or resource check regresses. No raw Docker shell,
   new tunnel, downgraded headroom gate or public MCP endpoint.

Initial GitHub Issue #9 only proved **manual comment exchange**, not a
blocked wait. The source-only peer-chat prototype lives at
`truthrail-mcp PR #10`. This PR is the embedded runtime candidate; no new
connection or runtime mutation is implied.
