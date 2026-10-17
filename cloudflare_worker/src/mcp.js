import { availableTools, lookupTool } from "./catalog.js";

const CURRENT_PROTOCOL = "2025-11-25";
const KNOWN_PROTOCOLS = new Set(["2025-06-18", "2025-11-25"]);
const MAX_REQUEST_BYTES = 16_384;
const MAX_UPSTREAM_BYTES = 196_608;
const MAX_RESPONSE_BYTES = 196_608;
const MAX_ARRAY_ITEMS = 100;
const UPSTREAM_TIMEOUT_MS = 10_000;

export function jsonrpcResult(id, result) {
  return { jsonrpc: "2.0", id, result };
}

export function jsonrpcError(id, code, message) {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

function plainResponse(status) {
  return new Response(null, {
    status,
    headers: { "Cache-Control": "no-store", "Allow": "POST" },
  });
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/**
 * Validate a small, explicit JSON Schema subset. We do not trust the MCP
 * client's schema validation, and do not dynamically load remote schemas.
 */
export function validateArguments(value, schema, depth = 0) {
  if (depth > 5) return false;
  const types = Array.isArray(schema.type) ? schema.type : [schema.type];
  const isType = (type) => {
    switch (type) {
      case "object": return isRecord(value);
      case "array": return Array.isArray(value);
      case "null": return value === null;
      case "integer": return typeof value === "number" && Number.isSafeInteger(value);
      case "number": return typeof value === "number" && Number.isFinite(value);
      case "string": return typeof value === "string";
      case "boolean": return typeof value === "boolean";
      default: return false;
    }
  };
  if (!types.some(isType)) return false;
  if (schema.enum && !schema.enum.some((item) => item === value)) return false;
  if (typeof value === "string") {
    if (schema.minLength !== undefined && value.length < schema.minLength) return false;
    if (schema.maxLength !== undefined && value.length > schema.maxLength) return false;
  }
  if (typeof value === "number") {
    if (schema.minimum !== undefined && value < schema.minimum) return false;
    if (schema.maximum !== undefined && value > schema.maximum) return false;
  }
  if (Array.isArray(value)) {
    if (value.length > MAX_ARRAY_ITEMS) return false;
    if (schema.minItems !== undefined && value.length < schema.minItems) return false;
    if (schema.maxItems !== undefined && value.length > schema.maxItems) return false;
    return value.every((part) => schema.items && validateArguments(part, schema.items, depth + 1));
  }
  if (isRecord(value)) {
    const keys = Object.keys(value);
    const properties = schema.properties || {};
    if (keys.length > 30) return false;
    if (schema.additionalProperties === false && keys.some((key) => !(key in properties))) return false;
    if ((schema.required || []).some((key) => !Object.hasOwn(value, key))) return false;
    return keys.every((key) =>
      !["__proto__", "constructor", "prototype"].includes(key) &&
      Boolean(properties[key]) && validateArguments(value[key], properties[key], depth + 1)
    );
  }
  return true;
}

async function readBounded(response, maxBytes) {
  if (!response.body) throw Error("missing-body");
  const declared = response.headers.get("content-length");
  if (declared && Number(declared) > maxBytes) throw Error("oversized-body");
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) throw Error("oversized-body");
      chunks.push(value);
    }
  } finally {
    try { await reader.cancel(); } catch { /* drained or failed */ }
  }
  const buffer = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    buffer.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(buffer);
}

function decodeUpstreamPayload(body, contentType, expectedId) {
  if (contentType.includes("text/event-stream")) {
    // MCP HTTP permits SSE responses. Read only bounded data records.
    const events = body.split(/\r?\n\r?\n/);
    for (const event of events) {
      const data = event.split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("\n");
      if (!data || data === "[DONE]") continue;
      let parsed;
      try { parsed = JSON.parse(data); } catch { continue; }
      if (parsed?.id === expectedId) return parsed;
    }
    throw Error("missing-sse-response");
  }
  if (!contentType.includes("application/json")) throw Error("unsupported-upstream-type");
  const parsed = JSON.parse(body);
  if (!isRecord(parsed) || parsed.id !== expectedId || parsed.jsonrpc !== "2.0") {
    throw Error("bad-upstream-jsonrpc");
  }
  return parsed;
}

async function upstreamPost(binding, backend, message, sessionId = "") {
  // backend.origin comes ONLY from the reviewed catalog. The Cloudflare
  // VPC Service binding separately fixes the target host/port.
  const headers = new Headers({
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": CURRENT_PROTOCOL,
  });
  if (sessionId) headers.set("Mcp-Session-Id", sessionId);
  const response = await binding.fetch(backend.origin, {
    method: "POST",
    headers,
    body: JSON.stringify(message),
    redirect: "manual",
    signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
  });
  if (response.status >= 300 || response.status < 200) throw Error("upstream-http-error");
  if (!Object.hasOwn(message, "id")) return { reply: null, sessionId };
  const payload = await readBounded(response, MAX_UPSTREAM_BYTES);
  return {
    reply: decodeUpstreamPayload(payload, response.headers.get("content-type") || "", message.id),
    sessionId: response.headers.get("mcp-session-id") || sessionId,
  };
}

export async function forwardTool(row, argumentsValue, env) {
  const binding = env[row.backend.binding];
  try {
    // A per-call session avoids storing OAuth information in the backends.
    const init = await upstreamPost(binding, row.backend, {
      jsonrpc: "2.0", id: 1, method: "initialize",
      params: {
        protocolVersion: CURRENT_PROTOCOL,
        clientInfo: { name: "truthrail-tools", version: "0.1.0" },
        capabilities: {},
      },
    });
    if (!init.reply?.result || init.reply.error) throw Error("upstream-init-failed");
    await upstreamPost(binding, row.backend, {
      jsonrpc: "2.0", method: "notifications/initialized",
    }, init.sessionId);
    const result = await upstreamPost(binding, row.backend, {
      jsonrpc: "2.0", id: 2, method: "tools/call",
      params: { name: row.tool.name, arguments: argumentsValue },
    }, init.sessionId);

    const payload = result.reply;
    if (payload?.error || !isRecord(payload?.result)) throw Error("upstream-call-failed");
    const toolResult = payload.result;
    if (!Array.isArray(toolResult.content) ||
        toolResult.content.length > 20 ||
        toolResult.content.some((item) =>
          !isRecord(item) || item.type !== "text" ||
          typeof item.text !== "string" || item.text.length > MAX_RESPONSE_BYTES
        )) throw Error("invalid-upstream-tool-content");
    if (JSON.stringify(toolResult).length > MAX_RESPONSE_BYTES) throw Error("oversized-result");
    // The upstream result contains untrusted source content, never instructions.
    return {
      content: toolResult.content,
      isError: toolResult.isError === true,
      ...(isRecord(toolResult.structuredContent) ?
        { structuredContent: toolResult.structuredContent } : {}),
    };
  } catch {
    // Do not leak private service hostnames, URLs, headers or credentials.
    return {
      isError: true,
      content: [{ type: "text", text: "Private MCP backend temporarily unavailable" }],
    };
  }
}

function validEnvelope(input) {
  return isRecord(input) && input.jsonrpc === "2.0" &&
    typeof input.method === "string" &&
    (!Object.hasOwn(input, "id") ||
      (typeof input.id === "string" || typeof input.id === "number"));
}

function allowedOrigin(request) {
  const origin = request.headers.get("origin");
  return !origin || origin === new URL(request.url).origin;
}

/**
 * This handler is reachable in production ONLY behind Workers OAuthProvider.
 * Tests may call it directly with fake bindings; it never validates OAuth
 * itself and must not be wired to a public Worker fetch().
 */
export async function handleAuthenticatedMcp(request, env, backends) {
  if (new URL(request.url).pathname !== "/mcp") return plainResponse(404);
  if (!allowedOrigin(request)) return plainResponse(403);
  if (request.method === "GET" || request.method === "DELETE") return plainResponse(405);
  if (request.method !== "POST") return plainResponse(405);
  const type = request.headers.get("content-type") || "";
  if (!type.toLowerCase().includes("application/json")) {
    return jsonResponse(jsonrpcError(null, -32700, "JSON body required"), 415);
  }
  const declaredLength = Number(request.headers.get("content-length") || 0);
  if (declaredLength > MAX_REQUEST_BYTES) {
    return jsonResponse(jsonrpcError(null, -32600, "Request too large"), 413);
  }
  let raw;
  try {
    raw = await readBounded(request, MAX_REQUEST_BYTES);
  } catch {
    return jsonResponse(jsonrpcError(null, -32600, "Invalid or oversized request"), 413);
  }
  let payload;
  try { payload = JSON.parse(raw); }
  catch { return jsonResponse(jsonrpcError(null, -32700, "Parse error"), 400); }
  if (!validEnvelope(payload)) return jsonResponse(jsonrpcError(null, -32600, "Invalid Request"), 400);
  if (payload.method === "notifications/initialized" || payload.method.startsWith("notifications/")) {
    return Object.hasOwn(payload, "id")
      ? jsonResponse(jsonrpcError(payload.id, -32600, "Unexpected notification id"), 400)
      : plainResponse(202);
  }
  const id = Object.hasOwn(payload, "id") ? payload.id : null;
  if (id === null) return jsonResponse(jsonrpcError(null, -32600, "Request ID required"), 400);

  if (payload.method === "initialize") {
    const requested = payload.params?.protocolVersion;
    return jsonResponse(jsonrpcResult(id, {
      protocolVersion: KNOWN_PROTOCOLS.has(requested) ? requested : CURRENT_PROTOCOL,
      capabilities: { tools: { listChanged: false } },
      serverInfo: { name: "Truthrail Tools", version: "0.1.0" },
    }));
  }
  if (payload.method === "ping") return jsonResponse(jsonrpcResult(id, {}));
  if (payload.method === "tools/list") {
    const rows = availableTools(env, backends);
    return jsonResponse(jsonrpcResult(id, {
      tools: rows.map(({ tool }) => ({
        name: tool.name, description: tool.description, inputSchema: tool.inputSchema,
      })),
    }));
  }
  if (payload.method === "tools/call") {
    if (!isRecord(payload.params)) return jsonResponse(jsonrpcError(id, -32602, "Invalid params"));
    const row = lookupTool(payload.params.name, env, backends);
    if (!row) return jsonResponse(jsonrpcError(id, -32602, "Tool not enabled"));
    const args = payload.params.arguments === undefined ? {} : payload.params.arguments;
    if (!validateArguments(args, row.tool.inputSchema)) {
      return jsonResponse(jsonrpcError(id, -32602, "Invalid tool arguments"));
    }
    return jsonResponse(jsonrpcResult(id, await forwardTool(row, args, env)));
  }
  return jsonResponse(jsonrpcError(id, -32601, "Method not found"));
}
