// Independent opt-in entrypoint. The existing OAuth entrypoint is unchanged.
import { BACKENDS } from "./catalog.js";
import { handleAuthenticatedMcp } from "./mcp.js";
import { parseRegistry, verifyBearer } from "./bearer-policy.js";

function reply(status) {
  const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
  if (status === 401) headers["WWW-Authenticate"] = 'Bearer realm="truthrail-mcp"';
  if (status === 429) headers["Retry-After"] = "60";
  return new Response(null, { status, headers });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/mcp" || url.search || url.hash) return reply(404);
    if (env.ENABLE_BEARER_INGRESS !== "1") return reply(503);
    let grants;
    try {
      grants = parseRegistry(env.CLIENT_GRANTS_JSON, env.MCP_AUDIENCE);
      if (request.url !== env.MCP_AUDIENCE) return reply(404);
      if (typeof env.CLIENT_RATE_LIMITER?.limit !== "function") return reply(503);
      // Fixed aggregate key also bounds unauthenticated attempts. Never use a token as a rate key.
      if (!(await env.CLIENT_RATE_LIMITER.limit({ key: "bearer-edge" })).success) return reply(429);
    } catch { return reply(503); }
    let client;
    try { client = await verifyBearer(request.headers.get("Authorization"), grants); }
    catch { return reply(503); }
    if (!client) return reply(401);
    try {
      if (!(await env.CLIENT_RATE_LIMITER.limit({ key: `client:${client.client_id}` })).success) return reply(429);
    } catch { return reply(503); }
    // This lane is restricted to Atlas reads even if other catalog entries change later.
    if (env.ENABLE_ATLAS_BACKEND !== "1" || typeof env.ATLAS_VPC?.fetch !== "function") return reply(503);
    const origin = request.headers.get("Origin");
    if (origin && origin !== url.origin) return reply(403);
    const backends = BACKENDS.filter((row) => row.id === "historical-atlas")
      .map((row) => ({ ...row, enabled: true }));
    // Only the handler's fresh, fixed protocol headers reach the private binding.
    // Caller Authorization, cookies and metadata are never forwarded upstream.
    try { return await handleAuthenticatedMcp(request, env, backends); }
    catch { return reply(502); }
  },
};
