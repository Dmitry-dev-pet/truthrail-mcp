# Alfred Agentgateway core — strict native owner API key + external JWT scopes

**18 October 2026 — source/release candidate ONLY, not installed on VPS.**

Owner decision: [infra-access #470](https://github.com/Dmitry-dev-pet/infra-access/issues/470).
Full detailed architecture: [ALFRED_MCP_UNIFIED_OWNER_AUTH.md](https://github.com/Dmitry-dev-pet/infra-access/blob/main/docs/ALFRED_MCP_UNIFIED_OWNER_AUTH.md).

## Production topology (not deployed)

- **One app container, two processes:** pinned official Agentgateway v1.6.0
  (download verified by SHA-256 `496ed27e6a96b55a9ad7cabe5c2be7172511f44a3cb113e533d22bf93a7f079d`)
  and one internal loopback shared Peer Chat MCP server.
- Native **ChatGPT owner** uses the original private OpenAI Secure MCP Tunnel
  on `172.30.143.10:8765/mcp` with a distinct file-backed header
  `X-Alfred-Native-Owner-Key` injected on the final tunnel-client → MCP hop.
  Gateway policy requires `apiKey.mode: strict`, exactly one
  `keyHash: sha256:<digest>`, `metadata: {role: owner}`, and only
  `apiKey.role == "owner"` for owner tools. The raw key is NOT mounted
  in Agentgateway, GitHub source or image; the OpenAI tunnel runtime key
  is NOT reused as an application credential. No JWT signer is required
  for native owner access.
- **Optional external clients**, disabled in initial owner-only deployment,
  use an independent private interface `172.30.144.10:8766/mcp`, strict
  OAuth/JWT issuer and audience, local *PUBLIC* external JWKS and
  explicit `mcp.tool.name` / `jwt.scopes` rules. Cloudflare must not be
  able to route to the owner listener or present its owner key.
- The first backend is bounded, persistent-process Peer Chat with six
  tools: `chat_create`, `chat_join`, `chat_send`, `chat_wait`,
  `chat_status`, `chat_close`. A and B use distinct private room tickets,
  a one-use invitation and `chat_wait` up to 10 seconds. No shell, LLM,
  arbitrary outbound HTTP, files, Atlas import or GitHub tool is present.
- Non-root `10001:10001`, read-only-root compatible, no host MCP listener,
  Docker socket or privileged capabilities; IP and Host policy is exact
  RFC1918 only. Future targets can be added ONLY through a separately
  approved, validated config+allowlist change; new executable backends may
  still require their own reviewed release.

## Required operator-owned inputs

| Container input | Required content |
| --- | --- |
| `/etc/agentgateway/config.yaml` | Strict native owner API-key hash and rule; optional separately isolated external JWT/scopes |
| `/etc/agentgateway/approved-targets.json` | Versioned, exact internal MCP target inventory |
| `/run/alfred-auth/external-jwks.json` | PUBLIC verification keys, **only when external JWT ingress is enabled** |
| `ALFRED_PEER_TRUSTED_HOSTS` | One exact native private listener IP:8765; optional second external IP:8766 |

There is **no** `owner-jwks.json` dependency. Production native
`X-Alfred-Native-Owner-Key` raw value must be created and held only by
a separately authorized non-root or operator-controlled tunnel-client
secret file with restrictive ownership/mode. The app receives the SHA-256
hash only. A configuration with a plaintext key, extra owner roles, multiple
owner keys, permissive authentication or an external owner assertion is
rejected before Agentgateway starts.

The production preflight runs:

```text
python -m alfred_gateway_core.validate_policy \
  /etc/agentgateway/config.yaml \
  /etc/agentgateway/approved-targets.json \
  /run/alfred-auth/external-jwks.json-or-dash \
  '172.30.143.10:8765[,172.30.144.10:8766]'
```

The real binary also validates its own configuration. The operator must
apply this same strict source policy validation before each future
hot reload. No automatically trusted hot swap or arbitrary target admission.

## Hosted source and release acceptance

The reviewed non-root image is rebuilt on disposable GitHub Actions runners
with the **same, unbypassed production entrypoint**. The CI test creates a
random synthetic owner API key and synthetic external RSA signer outside
the image, places only key SHA-256 and external PUBLIC JWKS into the
container and tests:

1. No native key, wrong key, and an unrelated `Authorization` bearer
   without the custom owner header: **denied**. Correct owner header plus
   unrelated Authorization: **accepted**, six allowed tools.
2. External strict JWT issuer/audience/expiry and per-tool scopes:
   negative cases denied on list and call; external cannot create/close
   owner rooms or use the private native network.
3. Two HTTP MCP clients in independent isolated Docker networks exchange
   three concurrent A↔B wait/send round trips with ticket isolation.
4. Invalid policy/permissive native API key, weak hashed-owner schema,
   unknown backend and public owner bind fail closed; no host port is
   published. Image cgroup and non-root/read-only boundaries verified.
5. Any production signer, real key, HTTPS auth provider, Cloudflare
   configuration, connected ChatGPT app or VPS mutation is OUTSIDE CI.
   CI artifacts are ephemeral; do not expose fixture credentials in logs.

## Explicit remaining production gates

A green image release/CI is NOT production deployment. The currently installed
Atlas image and original OpenAI Tunnel remain active.

Before switching:
1. Independent exact-image digest + overlay/network + non-root resource
   operator approval and **working offline rollback** to cached Atlas.
2. Operator-owned random static native owner key (>=256-bit entropy),
   protected file and public SHA-256 in reviewed config; prove no
   ChatGPT/caller **same-name X-header override** through the pinned
   `tunnel-client v0.0.16`. File-backed extra headers resolve at tunnel
   config load; rotation needs reviewed sidecar configuration/recreate and
   actual on-wire old-key rejection tests.
3. Strict native ChatGPT tools/list, tools/call and real two-Web-session
   A/B wait behavior with prompt/turn limitations. The tunnel identity is
   a workspace-level identity; room tickets, not OAuth per-tab claims,
   distinguish participants. No guarantee of unattended re-polling.
4. Cloudflare external JWT ingress remains independently blocked, not
   a reason to disable native tool availability or make a public VPS port.

No newly agent-routable `[infra]` shell/deploy command is created here.
