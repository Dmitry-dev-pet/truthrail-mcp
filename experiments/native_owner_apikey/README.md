# Native owner without a self-hosted JWT issuer — off-VPS proof

**18 October 2026. OFF-VPS EXPERIMENT ONLY. Nothing is deployed.**

This experiment addresses a specific uncertainty in [infra-access #470](https://github.com/Dmitry-dev-pet/infra-access/issues/470): can the existing OpenAI Secure MCP Tunnel supply an owner credential that Agentgateway **v1.6.0** validates itself, without an hourly JWT issuer/RS256 signer/JWKS/host-Python runtime?

## Proposed owner transport

```text
ChatGPT custom MCP app (existing workspace-level Tunnel permission)
       |
       v
Existing OpenAI Secure MCP Tunnel / tunnel-client
  MCP_EXTRA_HEADERS = X-Alfred-Native-Owner-Key: file:/protected/owner.key
       |
       v
Agentgateway private owner NIC:8765 (no host listener)
  apiKey.mode = strict
  apiKey.location.header.name = x-alfred-native-owner-key
  apiKey.keys[0].keyHash = sha256:<SHA256-of-random-key>
  apiKey.keys[0].metadata.role = owner
  mcpAuthorization = 'apiKey.role == "owner"'
       |
       v
Shared Peer Chat MCP backend, six approved tools, no shell/infra ops
```

This identifies the trusted **native transport principal**, not a separate identity for each ChatGPT conversation. Per-room A/B ticketing in the existing core still isolates rooms. The original OpenAI Tunnel runtime key is **NOT** the owner app credential and is never forwarded as one.

The dedicated `X-Alfred-Native-Owner-Key` header avoids collision with ChatGPT's possible forwarded `Authorization`. `openai/tunnel-client@v0.0.16` supports file-backed `MCP_EXTRA_HEADERS` on only the configured MCP origin; connector-supplied headers apply afterward, so a conflicting **same-name** header could still override it. The real native connector behavior, including whether it forwards any extra owner header, has NOT been tested. Never infer that ordinary Atlas tool success proves header provenance.

The gateway's policy uses **strict** API-key validation. The public, reviewed config contains only a SHA-256 hash of a sufficiently random key, not its value. Only a future independently admitted trusted operator may generate/store/revoke the real key in a 0600 root/deploy-owned file readable by the tunnel-client, and update the public hash using atomic versioned config. Static keys need explicit lifecycle/rotation and sidecar restart/reload checks; they are simpler than a JWT issuer, not free of security obligations.

## What the CI experiment verifies

- Use the SAME official Agentgateway 1.6.0 release code and pinned production Dockerfile on GitHub-hosted Docker. No VPS, GHCR publish, runtime signing key, new tunnel, Cloudflare access, or extra published port.
- Generate an unpredictable **synthetic test key** on the disposable runner, record only its SHA-256 under `apiKey.keys[*].keyHash`. Mount raw key only into the disposable client (NOT the Agentgateway container).
- Strictly deny missing X header, fake X header, and connector-style `Authorization` alone. A correct X header plus unrelated `Authorization` must authenticate and expose exactly the six approved Peer Chat tools.
- Verify actual `chat_create` and `chat_close` on the shared in-process backend, non-root user, private Docker bridge, no published host port and bounded cgroup.
- Deliberately bypass current **JWT-only** production entrypoint in *CI only*. A passing smoke does NOT change the deployed image's production validation behavior.

## External scoped agents stay separate

The existing strict external-JWT tests in `release/agentgateway-peer/ci/smoke_production.sh` remain in place and continue to ensure only scoped external tools are visible through a **different private listener/network**. Cloudflare external backhaul/authorization is independently reviewed, not enabled by this experiment. The first native rollout could expose ONLY the owner listener, with external access still absent.

## Before live use

1. CI proof plus independent review; migrate the **production** config/entrypoint structural validator to accept exactly this non-JWT owner mode while retaining externally scoped JWT policy. Re-test both modes and negative cross-network cases on disposable Docker before publishing a new immutable image.
2. Independently approve a protected static-key producer/file custody and revocation/rotation procedure, no credential values in GitHub, ChatGPT, workflow logs, Cloudflare or image layers.
3. Confirm actual OpenAI Tunnel `MCP_EXTRA_HEADERS` behavior, including load-time resolution and connector header precedence, with the pinned sidecar **and native ChatGPT**; no unauthorized sidecar change.
4. Separately owner-approve exact OCI digest + private Compose/service replacement, fresh VPS preflight, offline old-Atlas recovery and real native `tools/list/tools/call`. No generic SSH/shell permission.
5. Conduct actual two ChatGPT sessions A/B with `chat_wait`; the ten-second wait does not guarantee that ChatGPT starts a new tool call by itself after timeout.

This experiment creates **no production credential** and does not grant a new `[infra]` capability.
