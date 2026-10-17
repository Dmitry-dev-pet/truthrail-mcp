# Embedded Atlas provider: one container, one MCP process

The shared Alfred/Truthrail dispatcher can use the canonical AtlasQuery
module directly, inside the SAME Python process. Atlas remains a separate
capability/backend owner; the generic shared MCP is the caller-facing server.

The source-only module is shared_alfred_gateway/atlas_local.py. It publishes
the five original read-only Atlas tool names without an internal HTTP proxy,
without a second container or listener, and without the private Archigos
tool. Captions and future backends remain disabled.

## Pinned, real-source parity check

The public CI checks out the exact immutable public source revision
cb3c65853628607f2e03f30bc4e1a47063b13cd4 of rulers-chronology-audit,
the recorded revision behind the existing deployed Historical Atlas image.
It generates the canonical atlas.sqlite locally and compares tools/list
input signatures and the real data result of all five calls against the
native Atlas server, including PC03 source provenance. No Atlas VPS HTTP,
ChatGPT tunnel, personal original photo, raw private dataset or Archigos is
accessed. A CI pass validates SNAPSHOT parity, not live VPS connectivity.

## Activation boundaries

An eventual operator-reviewed combined Docker image may include the
truthrail-mcp shared MCP code plus the pinned original AtlasQuery module and
generated SQLite. It would REPLACE the current Atlas application process
in the existing container/service, not run a second MCP shadow alongside it.
That replacement and rollback require a separate reviewed change.

Runtime entrypoint main() fails closed unless BOTH environment flags
ALFRED_SHARED_MCP_ENABLED=1 and ALFRED_EMBEDDED_ATLAS_REVIEWED=1 are explicitly
set. The Docker network must remain private; the one existing ChatGPT
Secure MCP Tunnel is preserved until full independent cutover acceptance.

Required subsequent proofs: isolated real-dataset HTTP image smoke,
resource and container security review, exact image rollback, the same five
sourced tool calls from ChatGPT after a separately authorized cutover, and
the preexisting runner/access control admission gates.

Nothing in this source PR authorizes VPS deployment, tunnel retargeting,
Cloudflare OAuth manipulation, adding media tools, changing an existing
memory threshold, or calling the live Atlas with a new credential.
