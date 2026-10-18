
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
