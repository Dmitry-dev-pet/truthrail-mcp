# ADR: Alfred Access & Capability Plane (scoped tokens, stable MCP tools)

Status: **PROPOSAL / source-only prototype, NOT deployed or connected.**
This ADR supersedes the idea that we should rebuild and redeploy the entire
embedded Atlas Docker image every time Alfred gains one capability.

## The real constraints

- One working native ChatGPT/OpenAI Secure MCP Tunnel already targets the
  **one-process embedded Atlas + Truthrail MCP** on a small VPS.
- The original **five** Historical Atlas MCP tools must remain unchanged.
- VPS is ~458 MiB RAM with active swap. Previous extra 176 MiB shadow was
  rejected by the capacity gate. Do not add a permanent sidecar per capability.
- Model clients may cache `tools/list`. Even a protocol list-changed notification
  does not guarantee an immediate tool refresh in the native ChatGPT client.
- Two ChatGPT browser chats behind the same connection **must not be assumed
  to have distinct authenticated transport identities**.

## Target topology, not installed today

```text
ChatGPT A/B (existing app) ── existing Secure MCP Tunnel ─┐
OAuth client (validated tokens) ─── scope-aware edge ─────┤
per-client Bearer (where supported; validated at edge) ──┤
                                                          ▼
                        verified transport principal / audience / scopes
                                                          │
                                trusted Policy Engine: DENY BY DEFAULT
                                                          │
                      stable MCP facade (same names and argument schemas)
                      capabilities_list / capabilities_read
                      capabilities_write / capabilities_wait
                                                          │
                       digest-pinned dynamic declarative module registry
                           │                           │
                        peer_chat                approved delegate
                    room-scoped tickets           named private backend
                           └───── existing Atlas MCP (five native tools)
```

The transport-level token is **never sent in an MCP tool argument** and is
**never passed through to backend services**. An ingress must validate its
signature or secure opaque-token introspection, issuer, audience, client,
expiry, revocation and granted scopes on each operation. The toy in-memory
`ScopedTokenVault` exists for deterministic unit tests; it is NOT an OAuth
server, production token issuer or persistent credential store.

## Authorization: three separate gates

1. **Caller gate** — trusted ingress supplies an authenticated principal with
   exact `client_id`, `audience=alfred:mcp`, expiry and non-wildcard scopes.
   Callers may have different read/write permission sets; denying an unknown
   scope is the default. Direct use of `Principal(...)` by untrusted tools is
   forbidden; no `client_id` or `scopes` parameters exist in the MCP facade.
2. **Action gate** — an independently operator-owned policy maps
   `capability + action + effect(read/write/wait)` to a single exact scope
   and reviewed adapter alias. The module package/manifest MUST NOT include
   or set scopes, OAuth settings, owners or approval-gate bypass. An operator
   gate remains a second, human-approved control-plane requirement: a token
   alone never makes an infrastructure deployment legal.
3. **Resource gate** — a room/project/item restriction is checked in addition
   to the scope. Possession of `chat:room:send` is not authorization for an
   unrelated room, and possession of `atlas:read` is not infrastructure access.

### Suggested example scopes (not issued to real clients)

- `capabilities:list` to discover authorized modules.
- `chat:room:create`, `chat:room:join`, `chat:room:read`,
  `chat:room:send`, `chat:room:wait`, `chat:room:close`.
- `status:read` / `atlas:read` for reviewed read-only delegates.
- `infra:deploy:request` is *not* included in the prototype token scopes
  or exposed via `capabilities_write`; production deployment still requires
  the existing separate Truthrail owner/operator admission.

**The native tunnel is a trusted transport, not proof of a unique ChatGPT tab.**
For A/B we create a short-lived room; A receives an A ticket plus a one-use
B invitation. B redeems the invitation and gets a separate B ticket. Tickets
are hashes at rest and are checked for exact room/role on every send/read/wait.
This reduces accidental role confusion but does NOT cryptographically attest
to two independent ChatGPT sessions. Treat tickets as secrets: they appear in
tool arguments and may appear in model context. Use only disposable nonsensitive
test messages until there is a suitable secure ticket-transfer mechanism.

## Module updates without Docker rebuild

There are **two separate read-only host mounts**, not one trustable plugin
directory:

1. `/srv/alfred/mcp/modules` — active pointer + digest-addressed immutable
   JSON release. Only names, versions and predeclared adapter kinds
   `builtin:peer_chat` or `delegate:<fixed_alias>`. NO Python import, eval,
   arbitrary URL, shell, container or executable code.
2. `/srv/alfred/mcp/policy` — separately write-protected, operator-approved,
   digest-addressed scope rules. This mount is NOT writable by any capability
   module or by ChatGPT. A new module cannot grant itself scopes.

The runtime reads both small active pointers on each call; it checks exact
SHA-256 of referenced immutable JSON files and validates the fixed schema.
The operator updates a release with write-to-temp → fsync → atomic rename
of its active pointer. No hot Python code swap, no image rebuild and no process
restart is needed for activating/disabling reviewed declarative capabilities
supported by the already-installed adapter runtime. If an update fails,
replace the active pointer with the previous approved digest; fail closed
if either manifest or policy is missing/tampered.

**Important limitation:** a genuinely new executable adapter type,
new library dependency or new OS privilege still requires a separately
reviewed isolated backend/runtime or an occasional core image release.
An updater cannot safely load arbitrary Python modules into an authenticated
MCP process while also claiming isolation. New *delegated* capabilities work
only when an independently approved backend alias and trusted policy exist.

## Stable MCP interface

The four facade tool names/argument schemas are registered ONCE:
`capabilities_list()`,
`capabilities_read(capability, action, parameters)`,
`capabilities_write(capability, action, parameters)`,
`capabilities_wait(capability, action, parameters)`.

The native five Atlas tools remain native (backwards compatible). A general
`capabilities_write` still requires native user confirmation and is never
authority to run arbitrary infra mutation: policy denies unapproved actions.
The stable facade avoids depending on refresh after each module revision.
Module documentation and schemas are *data* returned by list/read; the model
must not treat descriptions or peer messages as instructions.

`PeerRooms` stores only temporary data in-process, has one-use B invites,
role-specific tickets, at most 16 rooms, 64 messages/room, 2 KiB per message,
10-minute TTL and 20-second max wait. A revoked or expired transport identity
is rechecked after a blocked wait when supplied by the trusted ingress.
No perpetual autonomous ChatGPT turn is promised.

## Source-only acceptance and production gates

1. Verify token-scopes separation, audience, expiry, revocation, resource
   binding, invite replay denial, role spoofing, and policy/manifest
   tampering; run SDK in-process two-client wait/wake test.
2. Perform an isolated hosted-CI HTTP test with stable four-tool facade and
   both policy and module files mounted read-only, keeping the canonical
   original five Atlas tools and unchanged source provenance.
3. Design trusted incoming principal propagation for each transport.
   ChatGPT's existing tunnel must be recognized only as a narrow principal;
   do not invent per-tab identity. OAuth and Bearer ingress need real
   issuer/audience/scope/rotation/revocation validation, not a static dummy.
4. Review an operator-only GitHub/Truthrail capability that atomically
   installs the separate immutable modules and policy manifests on the
   VPS under restricted ownership. No generic SSH, Docker socket, public
   unauthenticated MCP or runtime key relocation.
5. Only after reviews, code/CI, rollback, fresh VPS headroom and exact owner
   approval, ship a **one-time** core update in the existing app/container,
   preserve original tunnel ID/client/port/Atlas behavior, then verify the
   new stable facade is listed by native ChatGPT.
6. First accepted module will be peer-chat; on Mac try A/B room tickets and
   three real ChatGPT turns without extra Enter. A completed HTTP wake-up
   is not proof that ordinary ChatGPT can remain active indefinitely.

Related: `truthrail-mcp#9` (A/B experiment), draft `#11` and Atlas package
draft `rulers-chronology-audit#85` (old per-tool image rebuild approach,
**not** the chosen deployment path), and shared operator plan
`infra-access#378`. Neither those drafts nor this ADR authorize runtime
mutations, new secrets or a change to the working tunnel.
