# Agentgateway vs MCPHub: reversible source-only evaluation

Scope: whether an open-source gateway can replace a custom Alfred
Access & Capability Plane. **No VPS changes, no ChatGPT tunnel changes.**

## What is measured

- Official agentgateway **v1.6.0** (GitHub release, 2026-10-02):
  reproducibly downloaded and hash-verified as linux-amd64 release binary.
- Fake two-tool, MCP v2 HTTP backend (read-shaped \`dataset_info\`, write-shaped
  \`peer_echo\`) plus local ephemeral JWKS signing RSA JWTs in disposable CI.
- Test JWTs with separate exact \`atlas:read\` and \`chat:send\` scopes; deny a
  reader's discovery/invocation of write, invalid signature and audience.
- Add and remove one authorization rule while the **same** agentgateway
  process stays running; check discovered tools, deny and tool call.
- Record gateway process RSS before/after and backend RSS on GitHub runner.
  Never equate runner RSS with ~458 MiB VPS headroom or Docker cgroup memory.

All test issuer, keys and tokens are generated locally and disposed afterward.
No secrets from GitHub, Cloudflare, tunnel-client or local user apps.
No HTTP listener is intentionally exposed outside the ephemeral CI runner.
Tests explicitly **fail** if hot reload, JWT auth, tool hiding or rollback
does not work. This is an evaluation, not a deployment script.

## Static cross-project findings, source-verified 17 Oct 2026

| Decision criterion | Agentgateway | MCPHub |
| --- | --- | --- |
| Live routing edits | Watches local config; reloads routes, MCP backends and policies. Some top-level options need restart | Can add servers from UI/API without downtime; file edits may require restart |
| Auth | JWT/OIDC resource-server mode; tool-level policy with JWT claims | Bearer keys, OAuth, groups, per-user credentials and visibility |
| Scope granularity | MCP tool name/target allowed by CEL; tool *arguments* unavailable in \`mcpAuthorization\` (additional route authorization can handle them) | Unified per-invocation policy still proposed in open issue #1159 (2026-09-10) |
| Memory | **Measure in isolated CI; not VPS** | Not independently measured here; Node.js server may require more resources, not asserted |
| Tunnel compatibility | Streamable HTTP on one local port, likely compatible with existing tunnel-client's HTTP target **but not yet native-tested** | Streamable HTTP endpoint, likewise not native-tested |
| Tool handlers | Proxy/federate real MCP backend services and optionally OpenAPI; does not magically implement chat or Atlas | Connects to existing tool servers; does not remove backend runtimes |

Refs:
- https://agentgateway.dev/docs/standalone/latest/documentation/setup/update/
- https://agentgateway.dev/docs/standalone/latest/documentation/configuration/security/mcp-authz/
- https://agentgateway.dev/docs/standalone/latest/integrations/mcp/servers/http/
- https://github.com/agentgateway/agentgateway/releases/tag/v1.6.0
- https://github.com/samanhappy/mcphub
- https://github.com/samanhappy/mcphub/issues/1159
- https://github.com/openai/tunnel-client/blob/master/docs/connectors.md

Important architecture distinction:
  Native ChatGPT via **existing** Secure MCP Tunnel may have workspace-level
  access without an individual per-tab JWT in the MCP request. A JWT-gated
  gateway cannot safely infer distinct A/B tab identities. We must either
  configure actual connector OAuth or use a separate narrowly scoped
  native-tunnel principal (and for chat, room-bound tickets). Do NOT inject a
  shared operator Bearer with admin scopes merely to make it work.

Production: a gateway is an **additional running process** if placed in front
of the existing Atlas HTTP MCP. Even if gateway RSS fits, combined Atlas +
gateway + tunnel-client + Ops Hub + swap may not. Preserve the working
single-process Atlas until resource and identity path are verified. A Mac-hosted
gateway would need a separate reviewed private backhaul; no assumptions.

After CI, the only justified conclusion is whether the isolated v1.6.0
binary enforces the tested rules and hot-reloads. It is **not** proof of
compatible auth through ChatGPT's already connected tunnel, or that the
normal ChatGPT UI autonomously loops on \`wait\`.


## Reproducible result — 17 Oct 2026 (after test-harness correction)

- [GitHub Actions #39077387948](https://github.com/Dmitry-dev-pet/truthrail-mcp/actions/runs/39077387948):
  **SUCCESS**, official pinned `agentgateway` 1.6.0 checksum-verified.
- Synthetic scoped JWT tests: valid `atlas:read` token sees/calls `dataset_info`,
  cannot see or call `peer_echo`; `chat:send` token sees/calls `peer_echo` once
  its policy is activated. Wrong audience and bad signatures are rejected.
- **Hot policy reload and rollback actually worked** while retaining the same
  agentgateway PID. Prior run #39077287970 failed due to our test mistakenly
  treating a raised MCP denial as a failed authorization check; the denied
  call is an expected success condition. Re-run after correcting the test
  completed successfully.
- Measured `/proc/<pid>/status` **Agentgateway RSS**: 36.45 MiB before hot
  policy change, 38.47 MiB after. Synthetic Python MCP backend RSS:
  67.11 MiB. This is a GitHub-hosted runner snapshot, **not** VPS headroom,
  cgroup usage, sustained peak, or a comparison with MCPHub.
- All existing [public source checks #39077387950](https://github.com/Dmitry-dev-pet/truthrail-mcp/actions/runs/39077387950)
  completed SUCCESS.

**Decision from these observations:** prefer evaluating Agentgateway as an
upstream policy/router component, not replacing our project with unreviewed
homegrown identity/token machinery. Don't merge either gateway source or the
older Access Plane as a live runtime. To decide installation location, we
must still test the actual **existing** Secure MCP Tunnel's identity
semantics and 458 MiB VPS capacity under load. Tool-specific resource/room
authorization remains the owning backend's responsibility unless a separate
tested request-argument policy is added at the gateway.



## Peer Chat-only phase — Atlas is NOT a prerequisite (18 Oct 2026)

Superseding operator decision: [infra-access #470](https://github.com/Dmitry-dev-pet/infra-access/issues/470).
This second hosted-CI test replaces the synthetic Atlas mock with **one
standalone stateful Peer Chat backend**, while keeping the **same pinned
Agentgateway v1.6.0** and its JWT tool-scoping configuration.

- Two **separately authenticated MCP HTTP clients** with distinct exact JWT
  scope lists: A can create/send/wait/close; B can join/send/wait.
  Observer can only see a read-only status tool when the reviewed gateway
  config explicitly enables that scope. No `dataset_info` / Atlas target.
- A creates a disposable room with a role A ticket and a **one-use B invite**.
  B redeems the invite for a role B ticket. Backend verifies tickets (hashed
  at rest), room ID, role and cursor; only inert bounded text is accepted.
  Backend **cannot know the real ChatGPT tab identity**, even when the
  token scopes are distinct in this fixture.
- Three genuine bidirectional blocking `chat_wait` wake-ups across two
  persistent HTTP MCP connections; idle timeout and no cursor replay.
- JWT tool list visibility, attempt to invoke a disallowed tool, attempt to
  use invitation as send ticket, replayed invite, invalid room or ticket,
  close and denial after close.
- Hot-add the `chat_status` authorization policy **while both clients and
  backend remain running**, verify its call, revert the policy without
  replacing either process. Original room state must survive.
- Report both processes' resident RSS on disposable GitHub-hosted Linux.
  Does **not** measure VPS cgroup memory, actual ChatGPT tunnel identity or
  sustained swap load. Neither process is installed on VPS. The test
  does not imply that ordinary ChatGPT can issue repeated tool calls
  without another user message.

**Security boundary:** the backend binds **only to loopback** in the test.
Per-tool JWT checks happen at Agentgateway; room tickets belong to the
backend and cannot be replaced by simple `sender="A"` arguments. These
test tokens are generated ephemerally and never logged in the CI receipt.
Production must keep the backend private from the Internet and isolate
its network interface so that clients cannot bypass the JWT gateway.

Agentgateway remains a router and policy engine, not a Python runtime.
A tiny Peer Chat backend is still needed to store shared room state.
Adding independent backends later should require only a reviewed route
and policy update on the gateway; **new executable backend logic may still
need a separate backend release, but not a new Agentgateway image**.

This is an **off-VPS E2E MCP transport test**, not a live ChatGPT
connection or authorization to replace the existing app/tunnel.
