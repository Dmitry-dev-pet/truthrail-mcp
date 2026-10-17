# Architecture and limits

## Source code versus a live instance

- **Public:** generic MCP transport/adapters, fixed tool schemas, synthetic data fixtures, CI tests and documentation.
- **Private:** user-specific Truthrail context, credential routes, actual OAuth/KV bindings, protected network tunnels, backend ownership/permissions, live datasets, current service state and operational deployment decisions.

GitHub is the source of truth for state and reviewed contracts. MCP is a client-to-tool protocol and does not replace the backend implementation or data store.

## Three independent adapter tracks

1. `shared_alfred_gateway/`: conservative Python MCP server, five Atlas read-only functions, optional future media/caption route off by default. The code's fixed internal sample origins are intentionally local/container-only.
2. `cloudflare_worker/`: optional OAuth server, generic stateless MCP handler, all backend entries disabled, no owner's live Worker variables or KV binding. Its fixed `mcp.example.invalid` origin is a **placeholder** to replace in a separate reviewed deployment package.
3. `experiments/fastmcp4/`: in-process comparison of a mock Atlas backend and mounted FastMCP proxy, no HTTP listener, no real Atlas database, and no claim of lower memory use on a VPS.

The final dispatcher may share one logical tool registry across multiple separately authenticated transports, not necessarily one universal URL or one provider's OAuth product.

## Acceptance before connecting a real service

- Document one backend's owner, data/control boundary, fixed tool names, schemas and permissions.
- Verify clients and backend separately: no authorization → deny; wrong audience → deny; unrecognized tool/URL → deny.
- Test actual sourced replies and provenance for all five Atlas functions versus the current trusted service, not just synthetic fixture equality.
- Measure actual process peak memory, startup time and call latency on the intended host. Account for OS, TLS/tunnel/auth and headroom, not just a container's memory limit.
- Require separate human approval for any cutover; keep the existing service and a verified rollback path until acceptance is complete.

This repository does not grant such approval by its existence.
