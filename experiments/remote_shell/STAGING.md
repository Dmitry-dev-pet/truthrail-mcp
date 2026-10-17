# Staging checklist: ChatGPT sandbox as a Mac terminal

**Not deployed.** This is a separate experiment and does not authorize modifying
the existing Historical Atlas Secure MCP Tunnel, the Cloudflare OAuth Worker,
`infra-access` gateways, or the VPS Docker/networking policy.

## Intended paths

```
Mac terminal (HTTP client; no command execution)
    -- scoped authenticated channel --> staging relay, VPS loopback :8768
    <-- scoped authenticated channel -- result
                          ^
                          | separate agent role
                    MCP adapter, stdio
                          ^
           separate OpenAI Secure MCP Tunnel
                          ^
             authorized ChatGPT web chat
                 sandbox shell tool
```

The ChatGPT model is the *only* executor. The VPS stores JSON messages and the
Mac is just a display + keyboard. ShellSession is an optional helper intended to
run in the ChatGPT sandbox; **do not** instantiate it on the VPS.

The adapter now defaults to `stdio` to work with the documented OpenAI
`tunnel-client --mcp-command` route. No HTTP MCP port is needed in this profile.
It still needs a **separate** tunnel ID and runtime key, and normal ChatGPT
permission to add the plugin; the existing Atlas tunnel must remain untouched.

## Blockers before first live deployment

1. Owner/operator read-only check after the unrelated `infra-access #454`
   bootstrap failure. Verify recovery and VPS RAM/disk headroom; no automatic
   repeated provision actions while recovery is unverified.
2. Reviewed deployment mechanism for the **localhost-only** staging mailbox.
   It must not be published on a public HTTP port, and cannot use the forced
   `infra-access` SSH gateway as a general shell or forwarding tunnel.
3. Independently authenticated, TLS-protected Mac-to-mailbox path with scoped
   Mac-only token. **No free-form URL** forwarding, no GitHub Action per shell
   command, no credential values in repo/chat/issue.
4. Review/provision a separate OpenAI Secure MCP Tunnel from VPS to the adapter
   using an environment-provided runtime key. Do not replace or update Atlas
   `tunnel_id` or use a shared unscoped credential.
5. In ChatGPT web, deliberately connect the new tunnel through the Plugins UI,
   check exactly `terminal_wait` + `terminal_reply` appear, then grant only
   the approved test conversation access.
6. On the Mac, start the lightweight client, enter `pwd`, `ls`, `cd /tmp`,
   `pwd`. Confirm the output is from the **ChatGPT Linux sandbox** and the
   Mac working directory has not changed.
7. Measure latency and idle waits, then stop/delete staging credentials and
   processes if the experiment is unsuccessful.

## Explicit SDK/test notes

- `mcp==2.3.0` provides `MCPServer.run(transport="stdio")`, which is the default
  adapter transport.
- OpenAI Secure MCP Tunnel has an **absolute 120-second MCP request deadline**,
  including queue time. `terminal_wait` waits at most 20 seconds, and the
  chat's decision to call it again is *not* automatically scheduled.
- Our tests check shell state, queue expiry, auth role separation, HTTP relay,
  MCP schemas and an in-process MCP client round trip.
- They do **not** establish persistent ChatGPT execution, real Mac forwarding,
  or zero-cost/no-API access to a private tunnel.
- Future AI mode and true PTY/interactive stdin are explicitly out of scope.

References:
- https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt
- https://github.com/Dmitry-dev-pet/truthrail-mcp/issues/7
