# Truthrail MCP — contributor and agent instructions

This is a **public, reusable source reference**, not a live authorization server or operational entrypoint. A public URL does not grant rights to a user's systems.

## Security invariants

1. Never put user/private instance state, real Cloudflare KV identifiers, OAuth client secrets, GitHub PATs, SSH keys, tunnel keys, real Telegram/browser sessions, local file-system profiles, production databases, or private transcripts into this repository, issues or Actions.
2. Restrict tool exposure and upstream origins explicitly. Deny unknown tools; do not add arbitrary URL/SSH/shell/Docker features, raw SQL, universal execution, or GitHub Actions trigger tools.
3. Keep authentication at each transport boundary. Never forward a caller's Authorization header to another backend by default.
4. Preserve least-privilege scopes, output limits, sanitized errors, evidence/source provenance, and opt-in backend registration.
5. Production-capable changes require separate review by the owning private deployment/control plane. `executed != verified`.
6. FastMCP synthetic parity does not establish real Atlas parity, Mac runner access, actual HTTP authentication, resource headroom or deployment readiness.
7. Avoid embedding fixed owner-specific domains or GitHub identity in reusable Worker code; example domains must be reserved/non-routable.
8. No personal subscription OAuth through an inference/MCP proxy.

## Contribution workflow

Use branch → reviewable PR → CI → merge. Keep Python, Worker and experimental dependencies independent. Only GitHub-hosted read-only CI is authorized; never attach a self-hosted Mac runner, deployment secrets, GitHub package publishing or Cloudflare deploy to untrusted PRs.

Code and fixtures are public. Any actual private backend must be configured outside this repository and admitted by the owner's separate control plane.
