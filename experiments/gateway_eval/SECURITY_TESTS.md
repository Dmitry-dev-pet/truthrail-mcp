
## Security experiment: native GPT owner vs Cloudflare-style external scopes

Review only, NOT installed anywhere. The user requires native ChatGPT
to see the whole set of reviewed Alfred tools while external clients through
Cloudflare only see assigned scopes. The new hosted-CI step tests one upstream
Agentgateway v1.6.0 process with **TWO independently named gateway listeners**,
distinct JWT trust roots (two freshly generated RS256 keys), distinct audiences,
and independent MCP route policies, both forwarding to ONE shared-state Peer Chat
server. A synthetic inert owner-only `owner_marker` tool is loaded only by the
fixture environment; it never performs privileged operations or contacts VPS.

Expected hard negative tests include:

- Native-owner route lists/calls all seven approved synthetic tools.
- External writer sees only four allowed chat operations; read-only client
  sees only chat_status, NOT owner_marker/chat_create/close.
- An EXTERNAL-signed token containing the literal `alfred:owner` scope and
  fake `X-Alfred-Role: owner` / forwarded identity headers still cannot
  discover or invoke owner_marker.
- Direct connections to the owner route with external JWT, unknown audience,
  expired token, malformed token or no token must fail. Owner token cannot
  authenticate on the external route; both routes accept their correct tokens.
- External client guessing the full name of hidden owner_marker gets denied
  during tools/call, not merely excluded from tools/list.
- Owner and external sessions can exchange an inert message through one
  shared room; the backend separately enforces room-specific tickets.
- Changing ONLY external chat_send scope policy revokes external calls,
  while the owner retains the full tool list; policy rollback restores it
  without restarting either process or losing room state.

**Critical limitation:** Both CI listeners are reachable from the *same*
runner network namespace. Validating two listener JWT/policy boundaries does
NOT establish that the Cloudflare private backhaul lacks an independent
network path to the native route in a future Compose deployment. Separate
network namespace/address/firewall/VPC binding acceptance is a later
operator-reviewed gate. The test must explicitly report
`physical_network_isolation_tested=false`, even if all JWT checks pass.
Likewise the real OpenAI Secure MCP Tunnel and Cloudflare Worker are not
connected here, and the local fixture issuer has no production key material.

Reference design: [infra-access ADR #471](https://github.com/Dmitry-dev-pet/infra-access/pull/471).


### Disposable two-interface Docker network isolation (CI-only)

This second independent network test creates two disjoint **internal-only**
Docker bridge networks. One unprivileged container runs the SAME pinned
Agentgateway and Peer Chat processes on two assigned NIC addresses. Owner and
external gateway listeners each bind only to their own NIC's fixed IPv4
address. One disposable probe container joins ONLY owner network, another
ONLY external network. Both permitted local connections must succeed first.

Six attempts then verify that cross-network/other-port requests and direct
backend TCP access are denied. No TCP port is published to the host, Docker
socket is not mounted to the application, and root filesystem is read-only.
No real Cloudflare/ChatGPT tunnel, user token, secret or VPS is used.

**Do not deploy its deliberately no-auth CI config.** This validates
network topology on a disposable runner, not the exact future production
Docker Compose networks or Cloudflare VPC service. The separately tested
two-issuer JWT scopes and routes must BOTH remain enforced in production.


## Verified CI results — 18 October 2026

- Head revision `c40f1def8f29dab8b801cb60777f437b775febd0`.
- [Gateway/source test run 39082440906](https://github.com/Dmitry-dev-pet/truthrail-mcp/actions/runs/39082440906):
  two jobs PASS (gateway and independently pinned upstream tunnel-client test).
- [Public source run 39082440515](https://github.com/Dmitry-dev-pet/truthrail-mcp/actions/runs/39082440515):
  all seven project tests PASS.
- ONE real Agentgateway v1.6.0 with TWO named listeners and distinct
  ephemeral RSA signing keys and JWT issuers/audiences:
  owner`7/7` tools allowed, external writer`4/7`,
  external observer`1/7`. Ten missing/wrong-cross-route/wrong-audience/
  expired/bad-signature token probes rejected, guessed owner-only
  `tools/call` denied, forged role headers ignored. Revoking
  `chat_send` only on external route leaves owner authorized; hot
  rollback retains both processes and a shared room.
- The first cross-ingress run **intentionally failed** for a near-expired
  token (expired 10s before the test): agentgateway allows ~60s clock
  tolerance as documented for JWTs. The test now issues logically
  consistent JWTs expired **300s before** the current clock and verifies
  they are rejected. Do not interpret the 60s skew grace as immediate
  revocation; very short token TTLs need design review.
- Separate **disposable two-NIC network fixture**:
  two internal-only Docker bridge networks; both allowed TCP connections
  work; SIX attempts to access the other NIC/port or direct backend
  are denied. No host ports exposed; backend stays loopback-only
  inside the same container, running with UID/GID 10001 and read-only
  root filesystem. This fixture deliberately lacks JWT and MUST NOT
  be copied to a production environment.
- The above are **two independent acceptance experiments**, not one
  combined production deployment. Combining exact private NIC binding,
  native-tunnel authentication, external scoped OAuth, no public
  owner-route connectivity and role-ticket authorization in a single
  real Docker Compose deployment is STILL NOT TESTED.
- No VPS, Cloudflare, OpenAI tunnel, production keys or user tokens
  were changed. No real external agent was given credentials or access.
