/**
 * Truthrail Tools is a registry of independently owned MCP backends.
 * Every backend is disabled until a reviewed private Workers VPC Service
 * binding and real protocol parity test have been completed.
 *
 * Enabling an entry changes delegated authority. Require a reviewed PR.
 */
export const BACKENDS = Object.freeze([
  {
    id: "historical-atlas",
    owner: "Dmitry-dev-pet/rulers-chronology-audit",
    binding: "ATLAS_VPC",
    origin: "http://atlas.internal/mcp",
    enabled: false,
    tools: [
      {
        name: "dataset_info",
        description: "Read canonical Atlas dataset scope, provenance and limitations.",
        inputSchema: { type: "object", properties: {}, additionalProperties: false }
      },
      {
        name: "search_rulers",
        description: "Search historical rulers by text and optionally a year.",
        inputSchema: {
          type: "object",
          properties: {
            text: { type: "string", maxLength: 200 },
            year: { type: ["integer", "null"], minimum: 1000, maximum: 2100 },
            limit: { type: "integer", minimum: 1, maximum: 30 }
          }, additionalProperties: false
        }
      },
      {
        name: "get_timeline",
        description: "Read bounded timeline and source-qualified power assessments.",
        inputSchema: {
          type: "object",
          properties: {
            year: { type: "integer", minimum: 1000, maximum: 2100 },
            state_id: { type: "string", maxLength: 90 },
            limit: { type: "integer", minimum: 1, maximum: 40 },
            offset: { type: "integer", minimum: 0, maximum: 10000 }
          }, required: ["year"], additionalProperties: false
        }
      },
      {
        name: "compare_rulers",
        description: "Compare up to five canonical Atlas identities.",
        inputSchema: {
          type: "object",
          properties: {
            person_ids: {
              type: "array", minItems: 1, maxItems: 5,
              items: { type: "string", minLength: 1, maxLength: 90 }
            }
          }, required: ["person_ids"], additionalProperties: false
        }
      },
      {
        name: "get_evidence",
        description: "Read exact sourced historical evidence, not arbitrary SQL.",
        inputSchema: {
          type: "object",
          properties: { record_id: { type: "string", minLength: 1, maxLength: 100 } },
          required: ["record_id"], additionalProperties: false
        }
      }
    ]
  },
  {
    id: "ops-diagnostics",
    owner: "Dmitry-dev-pet/ops-hub",
    binding: "OPS_VPC",
    origin: "http://ops.internal/mcp",
    enabled: false,
    // Ops Hub currently exposes REST, not MCP. Do not invent tools.
    tools: []
  },
  {
    id: "media-reader",
    owner: "Dmitry-dev-pet/news-aggregator",
    binding: "MEDIA_VPC",
    origin: "http://media.internal/mcp",
    enabled: false,
    tools: []
  }
]);

const SAFE_NAME = /^[a-z][a-z0-9_-]{0,63}$/;
const SAFE_ORIGIN = /^http:\/\/[a-z][a-z0-9.-]*\/mcp$/;

export function validateCatalog(backends = BACKENDS) {
  if (!Array.isArray(backends) || backends.length > 32) throw Error("invalid-catalog");
  const names = new Set();
  const backendIds = new Set();
  const bindings = new Set();
  for (const backend of backends) {
    if (!backend || !SAFE_NAME.test(backend.id) || backendIds.has(backend.id)) throw Error("invalid-backend-id");
    backendIds.add(backend.id);
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(backend.owner)) throw Error("invalid-owner");
    if (!/^[A-Z][A-Z0-9_]{0,40}$/.test(backend.binding) || bindings.has(backend.binding)) {
      throw Error("invalid-binding");
    }
    bindings.add(backend.binding);
    if (!SAFE_ORIGIN.test(backend.origin)) throw Error("invalid-private-origin");
    if (typeof backend.enabled !== "boolean") throw Error("invalid-enabled");
    if (!Array.isArray(backend.tools) || backend.tools.length > 30) throw Error("too-many-tools");
    if (backend.enabled && backend.tools.length === 0) throw Error("enabled-without-tools");
    for (const tool of backend.tools) {
      if (!SAFE_NAME.test(tool.name) || names.has(tool.name)) throw Error("duplicate-or-invalid-tool");
      names.add(tool.name);
      if (typeof tool.description !== "string" || tool.description.length > 512) throw Error("invalid-description");
      if (!tool.inputSchema || tool.inputSchema.type !== "object" || tool.inputSchema.additionalProperties !== false) {
        throw Error("unsafe-tool-schema");
      }
    }
  }
  return true;
}

export function availableTools(env, backends = BACKENDS) {
  validateCatalog(backends);
  const rows = [];
  for (const backend of backends) {
    if (!backend.enabled) continue;
    // Only a private Cloudflare VPC Service binding is acceptable. No
    // public fetch(), user-controlled destination, URL, or Docker socket.
    if (typeof env?.[backend.binding]?.fetch !== "function") continue;
    for (const tool of backend.tools) rows.push({ backend, tool });
  }
  return rows;
}

export function lookupTool(name, env, backends = BACKENDS) {
  if (typeof name !== "string") return null;
  return availableTools(env, backends).find((x) => x.tool.name === name) || null;
}
