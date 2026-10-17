# Matched five-tool SDK benchmark (synthetic)

We compare **mcp==2.3.0** and **fastmcp==4.1.0** in *separate fresh processes*
and virtual environments using **the same Python interpreter and machine**.

Each process registers exactly five read-only synthetic Historical Atlas tools,
verifies their names and required input fields, executes the exact same mock
arguments and JSON response fixtures, denies an unknown tool, and measures
10 x 5 **in-process** tool calls after one warmup cycle. Reports are bounded
metadata-only JSON. The process peak RSS includes SDK imports and dependencies.

## Run source-only in isolated Linux CI

The repository CI creates separate virtualenvs under the runner temp directory,
installs pinned packages, and invokes the same \`benchmark.py\` in each. No
tokens, Docker containers, HTTP servers or user data are available.

The results describe in-process source SDK overhead **not** HTTP transport
overhead, a persistent daemon's idle RSS, real Atlas parity, concurrency,
authentication, TLS, backhaul or swap behavior on a 458 MiB VPS.

The earlier 106 MiB Mac FastMCP 4.1.0 result used a **different synthetic
proxy topology** and cannot be directly subtracted from these numbers. For
an Apple M4 paired comparison, both measurements must be made in the same
narrowly approved, immutable, one-time Mac Action and on the same Python
interpreter. A successful hosted Linux run is preflight evidence, **not**
permission to execute anything on a personal runner.

See the [public source boundary](../../SECURITY.md).
