import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/bearer.js";
import { audienceUrl, parseRegistry, verifyBearer } from "../src/bearer-policy.js";

const endpoint = "https://mcp.example.invalid/mcp";
// Synthetic fixtures only, never a deployable credential.
const token = "alf_" + "a".repeat(43);
const other = "alf_" + "b".repeat(43);
const now = Math.floor(Date.now() / 1000);
const digest = Buffer.from(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token))).toString("hex");
const base = { version: 1, audience: endpoint, clients: [{ client_id: "test-client", token_sha256: digest,
  scopes: ["atlas:read"], not_before: now - 10, expires_at: now + 300, revoked: false }] };
const clone = () => structuredClone(base);
function request(method = "tools/list", args, supplied = token, url = endpoint) {
  return new Request(url, { method: "POST", headers: { "Content-Type": "application/json",
    ...(supplied ? { Authorization: `Bearer ${supplied}` } : {}) },
  body: JSON.stringify({ jsonrpc: "2.0", id: 7, method, ...(args ? { params: args } : {}) }) });
}
function environment(data = clone()) {
  const calls = [], limits = [];
  const env = { ENABLE_BEARER_INGRESS: "1", ENABLE_ATLAS_BACKEND: "1", MCP_AUDIENCE: endpoint,
    CLIENT_GRANTS_JSON: JSON.stringify(data),
    CLIENT_RATE_LIMITER: { async limit({ key }) { limits.push(key); return { success: true }; } },
    ATLAS_VPC: { async fetch(url, init) {
      calls.push({ url, init });
      const input = JSON.parse(init.body);
      if (!Object.hasOwn(input, "id")) return new Response(null, { status: 202 });
      const result = input.method === "initialize" ? { protocolVersion: "2025-11-25", capabilities: {}, serverInfo: { name: "fixture", version: "1" } }
        : { content: [{ type: "text", text: JSON.stringify({ fixture: true, source_ids: ["FIXTURE-ONLY"] }) }] };
      return Response.json({ jsonrpc: "2.0", id: input.id, result });
    } },
  };
  return { env, calls, limits };
}

test("strict canonical HTTPS audience", () => {
  assert.equal(audienceUrl(endpoint), endpoint);
  for (const value of ["http://mcp.example.invalid/mcp", endpoint + "/", endpoint + "?token=x",
    endpoint + "#x", "https://user@mcp.example.invalid/mcp", "https://mcp.example.invalid:8080/mcp",
    "https://MCP.example.invalid/mcp", "https://mcp.example.invalid/mcp?"]) assert.throws(() => audienceUrl(value));
});

test("registry rejects wrong audience, scopes, duplicate grants and malformed configuration", () => {
  assert.equal(parseRegistry(JSON.stringify(base), endpoint).length, 1);
  const mutations = [
    d => { d.audience = "https://other.example.invalid/mcp"; },
    d => { d.clients[0].scopes = ["shell:write"]; },
    d => { d.clients[0].scopes.push("atlas:read"); },
    d => { d.clients[0].expires_at = d.clients[0].not_before; },
    d => { d.clients[0].expires_at = d.clients[0].not_before + 31 * 86400; },
    d => { d.clients[0].not_before = true; },
    d => { d.clients[0].revoked = "false"; },
    d => { d.clients[0].token_sha256 = token; },
    d => { d.clients[0].unexpected = true; },
    d => { d.clients.push({ ...d.clients[0], client_id: "another-client" }); },
    d => { d.clients.push({ ...d.clients[0], token_sha256: "0".repeat(64) }); },
    d => { d.clients = []; },
    d => { d.version = 2; },
  ];
  for (const mutate of mutations) { const data = clone(); mutate(data); assert.throws(() => parseRegistry(JSON.stringify(data), endpoint)); }
  for (const text of ["null", "[]", "{", "x".repeat(8193)]) assert.throws(() => parseRegistry(text, endpoint));
});

test("token expiry, future activation, revocation and wrong tokens deny independently", async () => {
  const grants = parseRegistry(JSON.stringify(base), endpoint);
  assert.equal((await verifyBearer(`Bearer ${token}`, grants, now)).client_id, "test-client");
  for (const header of [null, `Bearer ${other}`, token, `Bearer ${token}, ${other}`, `Basic ${token}`, `Bearer ${token} `]) {
    assert.equal(await verifyBearer(header, grants, now), null);
  }
  assert.equal(await verifyBearer(`Bearer ${token}`, grants, base.clients[0].expires_at), null);
  assert.equal(await verifyBearer(`Bearer ${token}`, grants, base.clients[0].not_before - 1), null);
  const data = clone(); data.clients[0].revoked = true;
  assert.equal(await verifyBearer(`Bearer ${token}`, parseRegistry(JSON.stringify(data), endpoint), now), null);
});

test("anonymous and wrong-token requests cannot reach the private backend", async () => {
  for (const supplied of [null, other]) {
    const { env, calls } = environment();
    const response = await worker.fetch(request("tools/list", null, supplied), env);
    assert.equal(response.status, 401); assert.equal(calls.length, 0);
    assert.match(response.headers.get("WWW-Authenticate"), /^Bearer /);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
  }
});

test("disabled ingress, missing binding/limiter and wrong audience fail closed", async () => {
  for (const mutate of [e => { delete e.ENABLE_BEARER_INGRESS; }, e => { delete e.CLIENT_RATE_LIMITER; },
    e => { delete e.ATLAS_VPC; }, e => { delete e.ENABLE_ATLAS_BACKEND; },
    e => { e.CLIENT_GRANTS_JSON = "{}"; }]) {
    const { env, calls } = environment(); mutate(env);
    assert.equal((await worker.fetch(request(), env)).status, 503); assert.equal(calls.length, 0);
  }
  const { env, calls } = environment();
  assert.equal((await worker.fetch(request("tools/list", null, token, "https://other.example.invalid/mcp"), env)).status, 404);
  assert.equal(calls.length, 0);
});

test("initialize and exact five-tool list succeed for an authorized client", async () => {
  const { env, calls } = environment();
  const init = await worker.fetch(request("initialize", { protocolVersion: "2025-11-25" }), env);
  assert.equal((await init.json()).result.protocolVersion, "2025-11-25");
  const response = await worker.fetch(request(), env);
  assert.deepEqual((await response.json()).result.tools.map(t => t.name).sort(),
    ["dataset_info", "search_rulers", "get_timeline", "compare_rulers", "get_evidence"].sort());
  assert.equal(calls.length, 0);
});

test("scoped call preserves sourced fixture; caller headers never reach origin", async () => {
  const { env, calls, limits } = environment();
  const req = request("tools/call", { name: "get_evidence", arguments: { record_id: "fixture" } });
  req.headers.set("Cookie", "must-not-forward=fixture"); req.headers.set("X-Secret", "fixture");
  const response = await worker.fetch(req, env);
  assert.equal((await response.json()).result.isError, false);
  assert.equal(calls.length, 3);
  for (const call of calls) {
    assert.equal(call.url, "http://atlas.internal/mcp");
    assert.equal(call.init.headers.has("Authorization"), false);
    assert.equal(call.init.headers.has("Cookie"), false);
    assert.equal(call.init.headers.has("X-Secret"), false);
    assert.equal(call.init.redirect, "manual");
  }
  assert.deepEqual(limits, ["bearer-edge", "client:test-client"]);
  assert.equal(JSON.stringify(calls).includes(token), false);
});

test("unknown tools, invalid arguments and cross-origin calls do not reach origin", async () => {
  for (const params of [{ name: "shell", arguments: {} },
    { name: "get_evidence", arguments: { record_id: "fixture", url: "http://other.invalid" } }]) {
    const { env, calls } = environment();
    const response = await worker.fetch(request("tools/call", params), env);
    assert.equal((await response.json()).error.code, -32602); assert.equal(calls.length, 0);
  }
  const { env, calls } = environment(); const req = request(); req.headers.set("Origin", "https://other.example.invalid");
  assert.equal((await worker.fetch(req, env)).status, 403); assert.equal(calls.length, 0);
});

test("revoking only this client's grant rejects its next request", async () => {
  const { env, calls } = environment();
  assert.equal((await worker.fetch(request(), env)).status, 200);
  const data = clone(); data.clients[0].revoked = true; env.CLIENT_GRANTS_JSON = JSON.stringify(data);
  assert.equal((await worker.fetch(request(), env)).status, 401); assert.equal(calls.length, 0);
});

test("aggregate and per-client rate limit failures deny without upstream calls", async () => {
  for (const denied of ["bearer-edge", "client:test-client"]) {
    const { env, calls } = environment(); env.CLIENT_RATE_LIMITER.limit = async ({ key }) => ({ success: key !== denied });
    const response = await worker.fetch(request(), env);
    assert.equal(response.status, 429); assert.equal(response.headers.get("Retry-After"), "60"); assert.equal(calls.length, 0);
  }
  const { env } = environment(); env.CLIENT_RATE_LIMITER.limit = async () => { throw Error("private-config"); };
  const response = await worker.fetch(request(), env); assert.equal(response.status, 503);
  assert.equal(await response.text(), "");
});
