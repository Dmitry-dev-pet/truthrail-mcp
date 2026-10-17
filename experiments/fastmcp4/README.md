# FastMCP 4.1.0 — synthetic five-tool experiment

This code creates an in-memory Historical Atlas **fixture** and a mounted FastMCP proxy. It checks five tool names, input-schema requirements, matching mock responses, test-only provenance, denial of unknown tools and approximate process memory.

It does **not** call real Atlas/ChatGPT/VPS services, install a server, open an HTTP port, use credentials, read personal files, or change the owner’s MCP connection.

Run in an isolated Python 3.14 environment after installing `fastmcp==4.1.0`:

```sh
python experiments/fastmcp4/smoke.py
```

Results apply only to this synthetic process; a live HTTP authorization check and real-data parity test are required before any deployment. This example must not be used as a generic shell/URL proxy.
