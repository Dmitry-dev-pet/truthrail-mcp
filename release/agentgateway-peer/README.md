# Alfred Agentgateway core v1 — source-only production image candidate

Status: **DRAFT / NOT RELEASED / NOT DEPLOYED**. Draft
[PR #16](https://github.com/Dmitry-dev-pet/truthrail-mcp/pull/16) is for code
review. This image is not a public MCP endpoint, an OAuth issuer or an
operator-approved VPS deployment.

## What ships

- Agentgateway v1.6.0 Linux AMD64 binary, **exact** SHA-256
  `496ed27e6a96b55a9ad7cabe5c2be7172511f44a3cb113e533d22bf93a7f079d`,
  built on a digest-pinned Python 3.12 base.
- Private shared-state Python MCP Peer Chat server with these six tools:
  `chat_create`, `chat_join`, `chat_send`, `chat_wait`,
  `chat_status`, `chat_close`.
- Strict Host and Origin checks on the backend. Only loopback plus one or
  two **exact, operator-configured RFC1918 IP:port** pairs are accepted.
  `0.0.0.0`, wildcard ports and public IPs are rejected.
- Nonroot UID/GID 10001, read-only-root compatible entrypoint, no
  built-in LLM inference, shell executor, Atlas, Cloudflare Worker, key
  issuer, token or test `owner_marker`.
- Initial policy admission: each gateway listener must bind a distinct
  private NIC, use `mode: strict`, verify exact JWT issuer/audience
  via PUBLIC local JWKS and present explicit MCP tool rules. Backend
  target URLs must be in the separate operator-owned
  `approved-targets.json`. Unknown private/public origins and
  arbitrary command adapters are forbidden.

## What MUST be mounted before starting

| Container location | Contents / owner |
| --- | --- |
| `/etc/agentgateway/config.yaml` | Operator-reviewed Agentgateway route and strict JWT policies |
| `/etc/agentgateway/approved-targets.json` | Separately reviewed exact backend URL inventory |
| `/run/alfred-auth/owner-jwks.json` | Owner identity's PUBLIC RSA verification keys, never a signer |
| `/run/alfred-auth/external-jwks.json` | OPTIONAL external issuer's PUBLIC RSA keys, only when its own private network is reviewed |
| `ALFRED_PEER_TRUSTED_HOSTS` | One exact owner internal IP:8765; optional second external IP:8766 |

The JWT **signer/private key and raw owner token MUST be external to the
Agentgateway image and container**. The private owner token file belongs
only to the existing `tunnel-client` sidecar through its supported
`MCP_EXTRA_HEADERS=Authorization: file:/run/secrets/...` feature, or the
existing ChatGPT connector's reviewed OAuth flow. The OpenAI tunnel
runtime API key is NOT reused as an application bearer.

The operator MUST run
`python -m alfred_gateway_core.validate_policy <config> <inventory> <owner_public_jwks> <external_public_jwks_or_-> <HostList>`
before accepting each later atomic route/policy change; runtime validation
on startup alone does not protect against a malicious future hot-reload.
Operator-writable policy files are NEVER mounted writable into the MCP
container.

**Source of operator release/rollback review**:
[infra-access #470](https://github.com/Dmitry-dev-pet/infra-access/issues/470),
[draft PR #471](https://github.com/Dmitry-dev-pet/infra-access/pull/471).
No direct owner VPS operation is bundled with this repo.

## Tests / release gates

The dedicated `verify-agentgateway-peer-core.yml` runs on hosted GitHub
Actions for PRs; it has `contents: read` only and cannot push an image.

1. Test private `Host` validation and removal of all synthetic test tools.
2. Build the source-pinned, nonroot, **no-issuer** candidate Docker image.
3. Generate two independent temporary JWT-signing keys **OUTSIDE** the
   candidate image, mount only PUBLIC JWKS into the server and keep the
   test bearer strings on runner-private, short-lived files.
4. Reject permissive authentication and a public owner bind during
   config preflight.
5. In one container, verify owner and external **strict JWT** policies
   with two separate private Docker network interfaces.
6. Two independent real HTTP MCP clients concurrently exchange three
   A/B round trips; external clients cannot reach owner TCP or the
   direct Peer Chat backend. Wrong issuer/audience/expiry/absent tokens
   must be rejected. Verify no published host ports and record cgroup
   memory. No actual tunnel or VPS.
7. Never build a production release containing a CI-only issuer or
   temporary test credentials.

Successful CI is NOT authorization to publish or replace the current VPS
application. A reviewed release must use an immutable image manifest
digest, independently provision a revocable owner identity with tested
rotation, preserve and verify the current tunnel ID/runtime key and
guarantee same-sidecar or explicitly approved sidecar-recreate rollback.
The VPS has ~458 MiB total RAM and swap usage; CPU/architecture and
fresh memory headroom must be checked just before cutover.

## Adding tools later without rebuilding the gateway

Agentgateway reads its separately approved routing and authorization
configuration dynamically. New *registered MCP backends* can be attached
by operator-owned, versioned, exact target policy with no Agentgateway
image rebuild, provided a suitable backend is already available. A new
executable backend still needs its own independently reviewed release;
no arbitrary Python module hot-loading or Docker socket is exposed to
ChatGPT.

Atlas is **optional** and can later be added as a read-only target with
`atlas:read` after source/provenance checks, without rebuilding the
gateway core.
