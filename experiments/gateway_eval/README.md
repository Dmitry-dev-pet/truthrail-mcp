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
