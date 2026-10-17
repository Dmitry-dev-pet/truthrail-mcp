# Persistent localhost HTTP MCP memory comparison

Unlike the earlier in-process benchmark, this launches a **real HTTP MCP server
process** for each SDK and measures its *server-only* VmRSS and VmHWM on Linux.
The same separate `mcp==2.3.0` client drives both servers. Tests are sequential,
each runs five read-only synthetic tools with 20 warm cycles (100 measured
HTTP tool calls), five seconds idle before and after load, and bounded timeouts.

- Both servers bind strictly `127.0.0.1`, with stateless Streamable HTTP
  and JSON responses. The fixed ports are 18765/18766 and aren't public.
- No real Atlas, authentication, TLS, backhaul, GitHub tokens, public ingress,
  Docker, VPS or Mac access, nor third-party HTTP calls by tools.
- RSS/HWM reads are limited to the ephemeral child process `/proc/<pid>/status`.
  No process ID, command line or host identity is included in results.
- The experiment does **not** measure Docker cgroups, customer data,
  real authentication overhead, high concurrency or many-hour memory leaks.
  The observation interval is a short-lived source test, not uptime proof.
- SDKs are installed in **two isolated venvs** using the same Python 3.14
  version, and a single client controls both. The result reports startup,
  idle, post-load and peak RSS and median *HTTP* call latency.

The runner's CI process controls and terminates each child. **Never run on
the production 458 MiB VPS**, reuse private Atlas credentials or disable the
existing admission gates to satisfy this test.
