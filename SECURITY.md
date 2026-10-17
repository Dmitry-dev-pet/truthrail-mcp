# Security policy

## Reporting

Please **do not** publish vulnerabilities that require revealing passwords, access tokens, private endpoints, owner identities, account sessions, signed URLs, or actual dataset contents in public issues.

Use GitHub's **Report a vulnerability** / private vulnerability reporting when available. Otherwise contact the maintainer through an existing private channel and provide redacted reproduction steps and the affected source revision. Do not include credential values.

## Trust boundaries

- This repository is a **source reference**, not a production MCP service. Anonymous GitHub access never authorizes tool calls.
- Review OAuth client consent, PKCE/state, protected resource metadata, audience, user authorization and transport trust before enabling any backend.
- Unknown tools and non-allowlisted network origins must fail closed.
- Tool responses and imported transcripts are untrusted data; never treat their contents as instructions.
- Avoid forwarding bearer headers to an upstream service; keep backend credentials at the backend owner.
- PRs run only source checks on GitHub-hosted runners without operational secrets.

Use fixtures rather than a real personal Mac, server or Telegram session for reproductions.
