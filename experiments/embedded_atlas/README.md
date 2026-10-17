# Combined Truthrail MCP and Atlas: CI-only Docker proof

The experiment builds one Docker image containing the generic Truthrail MCP
source and the original canonical Historical Atlas query layer/data.

The Atlas snapshot comes from the **immutable public source commit**
cb3c65853628607f2e03f30bc4e1a47063b13cd4, corresponding to the
recorded currently deployed Atlas image source. The SQLite database is
generated in an isolated Docker build stage. The final runtime contains
the direct AtlasQuery layer plus the shared MCP entrypoint, not a separate
Atlas MCP server or HTTP proxy.

The final image uses a non-root identity, immutable read-only SQLite,
one port (8765) and an opt-in entrypoint. The entrypoint refuses to start
without both separately reviewed enable flags. No private poster photo,
Archigos dataset, OAuth tokens, VPS SSH credentials or Mac user files are
copied into it.

The public CI only:
- checks out both public codebases at exact reviewed commits;
- builds the disposable combined Docker image;
- starts **one** container with cap-drop, read-only root, tmpfs and a
  176 MiB test-only memory ceiling;
- binds only host 127.0.0.1:18767;
- calls the five actual canonical read-only tools over MCP HTTP, verifying
  fixed schemas, data scope and PC03 source evidence, plus unknown-tool denial;
- verifies it is one container, checks a single Docker cgroup memory sample,
  stops and deletes the container.

CI tests no live VPS, credentials, OpenAI tunnel, Cloudflare, private data
or public ingress. Passing the CI memory limit does not override the separate
VPS shadow's protected 176 MiB headroom gate, because the hosted runner has
different total available memory. This image is **not published to GHCR**.

Any actual replacement of the running Atlas process must be proposed as
a separate, reversible, owner-approved deployment with old image retained,
single existing tunnel unchanged, and post-cutover real tool-call checks.
