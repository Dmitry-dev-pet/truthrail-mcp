// A deployment-owned, hash-only client registry. No credential issuance here.
const TOKEN = /^alf_[A-Za-z0-9_-]{43,86}$/;
const CLIENT = /^[a-z][a-z0-9-]{2,47}$/;
const HASH = /^[a-f0-9]{64}$/;
const MAX_LIFETIME = 30 * 24 * 60 * 60;
const record = (x) => x !== null && typeof x === "object" && !Array.isArray(x);
const exact = (x, names) => record(x) && Object.keys(x).length === names.length &&
  names.every((name) => Object.hasOwn(x, name));

export function audienceUrl(value) {
  if (typeof value !== "string" || value.length > 256) throw Error("invalid-audience");
  const url = new URL(value);
  if (value.includes("?") || value.includes("#") || url.protocol !== "https:" || url.username || url.password || url.port ||
      url.pathname !== "/mcp" || url.search || url.hash || url.href !== value) {
    throw Error("invalid-audience");
  }
  return value;
}

export function parseRegistry(document, audience) {
  audienceUrl(audience);
  if (typeof document !== "string" || document.length > 8192) throw Error("invalid-registry");
  const data = JSON.parse(document);
  if (!exact(data, ["version", "audience", "clients"]) || data.version !== 1 ||
      data.audience !== audience || !Array.isArray(data.clients) ||
      data.clients.length < 1 || data.clients.length > 8) throw Error("invalid-registry");
  const clients = new Set(), hashes = new Set();
  for (const grant of data.clients) {
    if (!exact(grant, ["client_id", "token_sha256", "scopes", "not_before", "expires_at", "revoked"]) ||
        typeof grant.client_id !== "string" || !CLIENT.test(grant.client_id) || clients.has(grant.client_id) ||
        typeof grant.token_sha256 !== "string" || !HASH.test(grant.token_sha256) || hashes.has(grant.token_sha256) ||
        !Array.isArray(grant.scopes) || grant.scopes.length !== 1 || grant.scopes[0] !== "atlas:read" ||
        !Number.isSafeInteger(grant.not_before) || grant.not_before < 0 ||
        !Number.isSafeInteger(grant.expires_at) || grant.expires_at <= grant.not_before ||
        grant.expires_at - grant.not_before > MAX_LIFETIME || typeof grant.revoked !== "boolean") {
      throw Error("invalid-client-grant");
    }
    clients.add(grant.client_id); hashes.add(grant.token_sha256);
  }
  return data.clients;
}

function equalDigest(a, b) {
  let difference = a.length ^ b.length;
  for (let i = 0; i < 64; i++) difference |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return difference === 0;
}

export async function verifyBearer(header, grants, now = Math.floor(Date.now() / 1000)) {
  if (!Number.isSafeInteger(now) || now < 0) return null;
  const match = typeof header === "string" && /^Bearer (alf_[A-Za-z0-9_-]{43,86})$/i.exec(header);
  if (!match || !TOKEN.test(match[1])) return null;
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(match[1]));
  const hash = Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
  let result = null;
  for (const grant of grants) if (equalDigest(hash, grant.token_sha256)) result = grant;
  if (!result || result.revoked || now < result.not_before || now >= result.expires_at) return null;
  return { client_id: result.client_id, scopes: [...result.scopes] };
}
