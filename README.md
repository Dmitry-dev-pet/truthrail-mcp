# Truthrail MCP

**One source of truth. Any AI.**

An open-source, vendor-neutral collection of MCP tool-plane adapters for **Truthrail / Alfred**. Truthrail keeps durable project context in GitHub; an MCP adapter lets authorized AI clients call independently owned tools without becoming a second agent or an unrestricted control plane.

> **Status: source/experimental reference only.** This public Git repository is not an active MCP endpoint. The Historical Atlas implementation and its working ChatGPT connection are separate private services. Successful tests of synthetic data do not mean that live tools are connected.

## Repository layout

| Package | Purpose | Status |
| --- | --- | --- |
| `shared_alfred_gateway/` | Narrow Python MCP adapter for the five Historical Atlas tool schemas and a disabled-by-default captions candidate | Source tests; not deployed from this public repo |
| `cloudflare_worker/` | Optional OAuth entrypoint plus a separate opt-in Bearer client edge, using one bounded MCP handler | Source tests; real owner-specific configuration is deliberately omitted |
| `experiments/fastmcp4/` | FastMCP 4.1.0 in-process five-tool proxy comparison | Synthetic smoke only |
| `tests/` | Schema, denial, output and provenance fixtures | No live data |

This public reference is a **clean-source export**. It does **not** preserve private infrastructure Git history, GitHub issues, Actions logs, OAuth bindings, runtime tokens, service hostnames or user-specific inventories.

## Design

```text
ChatGPT / another authorized MCP client
                   │
        separately authenticated edge
                   │
             MCP tool dispatcher
               /           \
     independent Atlas    future reviewed
     read-only backend    source/backend
```

The transport for ChatGPT may differ from the authentication used by other clients. Backend tool registration is deny-by-default and must be reviewed with the backend owner. Credentials must remain in the owning service. Ordinary tool calls do not require one GitHub Issue/Action per request.

The [minimal Cloudflare client guide](docs/CLOUDFLARE_BEARER_CLIENT.md) describes the separately disabled Bearer entrypoint, client-scoped hash registry, required private VPC Service binding and a standalone HTTPS test client. This is tested source, not an active external client connection.

## Run source checks

For the Python adapter (Python 3.12):

```sh
python -m pip install -r requirements-shared.txt
python -m unittest discover -s tests -v
```

For the separate Cloudflare Worker package (Node.js 24):

```sh
cd cloudflare_worker
npm install --no-audit --no-fund
npm test
npm run check
```

For the independent FastMCP experiment in a disposable environment (Python 3.14):

```sh
python -m pip install "fastmcp==4.1.0"
python experiments/fastmcp4/smoke.py
```

The FastMCP test is **synthetic** and intentionally has no network listener, personal account data, GitHub API credential, database, or production backend. Memory and latency reported by the smoke are measurements for its own process/environment, not proof of deployment feasibility on a small VPS.

## Deployment safety

The Worker example configuration is `cloudflare_worker/wrangler.example.jsonc`, **not** an active deployment configuration. Copy it to a private deployment repository and provide your own OAuth client, KV namespace, verified HTTPS origin and secrets using that provider's supported deployment flow. Do not commit credentials.

No source here authorizes access to an individual's Mac, VPS, Atlas dataset, Telegram session or subscriptions; publishing code and deploying a service are separate decisions. Read [SECURITY.md](SECURITY.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before using any adapter beyond synthetic tests.

## License

MIT (see [LICENSE](LICENSE)). Third-party packages retain their own licenses. Contributions must use sanitized fixtures and cannot broaden runtime privileges by changing only docs or examples.

