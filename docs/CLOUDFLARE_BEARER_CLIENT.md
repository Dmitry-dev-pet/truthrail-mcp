# Minimal independent MCP client through Cloudflare

Status: tested source candidate, not a provisioned endpoint. This change grants
no Cloudflare or VPS authority and does not activate the example configuration.

`cloudflare_worker/src/bearer.js` is a separate Worker entrypoint. It reuses the
bounded MCP handler and the five Atlas schemas, and leaves `src/index.js` and
its existing OAuth configuration alone. Both entrypoints can point to the same
privately owned MCP process. This is client-to-tool access, not a model gateway.

## Deployment-owned configuration

Create a private deployment configuration from `wrangler.bearer.example.jsonc`.
Choose an account-unique rate-limit namespace; its sample value is not an
instruction to reuse an existing namespace. Keep both enable flags off until
the owning infrastructure control plane admits the exact operation.

The required private bindings are:

| Binding | Purpose |
| --- | --- |
| `MCP_AUDIENCE` | Exact canonical HTTPS URL ending in `/mcp`, with no query, credentials, fragment or non-default port |
| `CLIENT_GRANTS_JSON` | Private secret containing only client names, token SHA-256 digests, scopes and lifecycle timestamps |
| `CLIENT_RATE_LIMITER` | Required Cloudflare rate-limit binding; aggregate and named-client keys, no token values |
| `ATLAS_VPC` | Fixed Workers VPC **Service** binding to the already managed MCP host/port |

The registry schema is version 1 with exactly `version`, `audience`, `clients`.
Each client has exactly `client_id`, `token_sha256`, `scopes`, `not_before`,
`expires_at`, `revoked`. Maximum eight clients, each with only `atlas:read` and
at most 30 days of validity. A short first-test lease is preferable. Tokens use
the `alf_` prefix and at least 32 cryptographically random bytes encoded as
base64url. Mint and consume a real token inside its owning private executor;
never return it in a chat, log, issue, artifact or source file.

The example is deliberately missing real identifiers and grants. The handler
fails closed if a required binding or enable flag is absent. There is no public
fetch fallback, arbitrary origin, shell, SSH, SQL, provider credential or write tool.

## Private connectivity is a separate prerequisite

The working ChatGPT/OpenAI tunnel is not a Cloudflare VPC route. The owning
`infra-access` control plane must independently verify Cloudflare permission
metadata and any existing connector/service, then prepare a narrow reviewed
provisioning path when absent. Reuse a suitable verified shared connector;
otherwise review one fixed connector to this tool plane, not a tunnel per tool.
Do not invoke retired Jevspin/IT Mentor bootstrap routes, expose an anonymous
VPS port, repurpose the OpenAI runtime key, or use a generic remote shell.

Cloudflare VPC Service bindings pin an upstream host/port independently of the
URL in Worker code. The private deployment must pin the actual existing MCP,
not the reusable source's example hostname.

## Minimal external acceptance

The standalone stdlib client is `tools/mcp_client_smoke.py`. Its owning executor
supplies `MCP_ENDPOINT` and `MCP_CLIENT_TOKEN` as private environment variables,
not command-line arguments. The only command is:

```sh
python tools/mcp_client_smoke.py
```

It requires all of the following: anonymous and wrong-token HTTP 401; MCP
initialize and notification success; exactly five Atlas tools; canonical dataset
metadata; `get_evidence(power-p03)` returning source `PC03`. It refuses redirects,
does not retry, and prints only bounded proof metadata, never source text or keys.
It does not independently verify the historical truth of imported source claims.

For a real revocation proof, the owning executor keeps the short-lived token
only in memory, revokes **that** client in the private registry, waits for the
deployment to become effective, then runs:

```sh
python tools/mcp_client_smoke.py --revoked-only
```

The normal smoke truthfully reports `revocation_verified: false`; a successful
second probe is required separately. Registry replacement is a deployment change;
revocation takes effect as that deployment propagates, not instantly everywhere.
The native rate limiter is per Cloudflare location and approximate, not a global
quota or spending gate. No paid model call is part of either test.

Activation is accepted only with real HTTPS client proof, sanitized operation
receipt, and the original ChatGPT connection still returning sourced data.
Rollback disables/removes the new client edge and revokes its test grant through
the owning narrow operation. The existing MCP app and ChatGPT tunnel need no
cutover for this additional client lane.

## Source verification

```sh
node --test cloudflare_worker/tests/bearer.test.mjs cloudflare_worker/tests/mcp.test.mjs
python -m unittest discover -s tests -p 'test_minimal_mcp_client.py' -v
```

These tests use synthetic data and fake private bindings. They do not establish
Cloudflare deployment, VPC reachability, live Atlas fidelity or a live client grant.

References:
- https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/
- https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/
