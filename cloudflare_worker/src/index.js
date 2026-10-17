import { OAuthProvider, AuthorizationError, CimdFetchError, authorizationErrorRedirect } from "@cloudflare/workers-oauth-provider";
import { handleAuthenticatedMcp } from "./mcp.js";
import { githubPkce, validGithubPkceVerifier } from "./oauth-pkce.js";
import { OAuthStepError, oauthStep, oauthFailure } from "./oauth-diagnostics.js";

const ORIGIN = "https://mcp.example.invalid";
const RESOURCE = ORIGIN + "/mcp";

function htmlEscape(value) {
  return String(value).replace(/[&<>"']/g, (char) => "&#" + char.charCodeAt(0) + ";");
}

function consentPage(details, handle) {
  const name = htmlEscape(details.clientName);
  const domain = details.clientDomain
    ? "Client publisher: <strong>" + htmlEscape(details.clientDomain) + "</strong>."
    : "This application's publisher name has not been verified.";
  const destination = htmlEscape(details.redirectHost);
  const localWarning = details.redirectIsLoopback
    ? "<p><strong>This grants access to an app on your computer. Only proceed if you started that sign-in.</strong></p>"
    : "";
  return `<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Truthrail Tools — approve MCP access</title>
<style>body{font:16px system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1.25rem;line-height:1.5}
button{padding:.6rem 1rem;margin-right:.8rem}main{border:1px solid #aaa;padding:1.5rem;border-radius:.5rem}</style>
<main><h1>Authorize ${name}?</h1>
<p>${domain}</p>
<p>Tokens will be sent to <strong>${destination}</strong>.</p>
${localWarning}
<p>Scope: <strong>mcp:read</strong> — only independently approved read-only tools.</p>
<p>Sign-in uses GitHub. Your personal GitHub token is used only to verify the owner and is discarded.</p>
<form method="post" action="/authorize">
<input type="hidden" name="handle" value="${htmlEscape(handle)}">
<button type="submit" name="decision" value="approve">Allow read access</button>
<button type="submit" name="decision" value="deny">Deny</button>
</form></main></html>`;
}

function configured(env) {
  return typeof env?.GITHUB_CLIENT_ID === "string" &&
    /^[A-Za-z0-9_.-]{8,128}$/.test(env.GITHUB_CLIENT_ID) &&
    typeof env?.GITHUB_CLIENT_SECRET === "string" && env.GITHUB_CLIENT_SECRET.length > 16 &&
    typeof env?.OWNER_GITHUB_ID === "string" &&
    /^[1-9][0-9]{0,19}$/.test(env.OWNER_GITHUB_ID);
}

function authError(error) {
  // Only SDK-validated redirects may be followed. Never construct one from
  // raw client input or an untrusted callback URL.
  const reason = error instanceof OAuthStepError ? error.cause : error;
  if (reason instanceof AuthorizationError && reason.redirectTo) {
    return Response.redirect(reason.redirectTo, 302);
  }
  const result = oauthFailure(error, {
    isAuthorizationError: cause => cause instanceof AuthorizationError,
    isMetadataError: cause => cause instanceof CimdFetchError,
  });
  // Static labels only; no state, code, client ID, cookies, metadata URL,
  // exception text or stack. Cloudflare logs can correlate the phase.
  console.warn("truthrail_oauth_failure", result.stage, result.kind);
  return new Response(
    `Authorization unavailable or invalid (diagnostic: ${result.stage}/${result.kind}). Start a new MCP connection.`,
    {
      status: result.status,
      headers: { "Cache-Control": "no-store", "Content-Type": "text/plain; charset=utf-8" },
    },
  );
}

async function startGitHubLogin(approved, oauth, env) {
  const { verifier, challenge } = await oauthStep("upstream_start", () => githubPkce());
  const { state, headers } = await oauthStep("upstream_start", () =>
    oauth.beginUpstream(approved.request, {
      data: { verifier },
      headers: approved.headers,
    })
  );
  const target = new URL("https://github.com/login/oauth/authorize");
  target.searchParams.set("client_id", env.GITHUB_CLIENT_ID);
  target.searchParams.set("redirect_uri", ORIGIN + "/callback");
  target.searchParams.set("scope", "read:user");
  target.searchParams.set("state", state);
  target.searchParams.set("code_challenge", challenge);
  target.searchParams.set("code_challenge_method", "S256");
  headers.set("Location", target.toString());
  return new Response(null, { status: 302, headers });
}

async function finishGitHubLogin(request, env) {
  // Consume browser-bound, one-time state BEFORE trusting query parameters.
  const oauth = env.OAUTH_PROVIDER;
  const { request: original, headers, data } = await oauthStep("callback_state", () => oauth.finishUpstream(request));
  const url = new URL(request.url);
  if (url.searchParams.get("error")) {
    headers.set("Location", authorizationErrorRedirect(original, "access_denied"));
    return new Response(null, { status: 302, headers });
  }
  const code = url.searchParams.get("code");
  if (!code || code.length > 512 || !validGithubPkceVerifier(data?.verifier)) {
    return new Response("Invalid or expired upstream authorization", { status: 400 });
  }

  // Exchange the GitHub OAuth App code. This token is *not* stored in KV
  // props, logs, frontend pages, GitHub Actions or the MCP connection.
  const tokenResponse = await oauthStep("github_token_exchange", () => fetch("https://github.com/login/oauth/access_token", {
    method: "POST",
    headers: { "Accept": "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({
      client_id: env.GITHUB_CLIENT_ID,
      client_secret: env.GITHUB_CLIENT_SECRET,
      code,
      code_verifier: data.verifier,
      redirect_uri: ORIGIN + "/callback",
    }),
    signal: AbortSignal.timeout(8000),
    redirect: "error",
  }));
  if (!tokenResponse.ok) throw new OAuthStepError("github_token_exchange", new Error("github_exchange_unavailable"));
  const tokenPayload = await oauthStep("github_token_exchange", () => tokenResponse.json());
  const accessToken = tokenPayload?.access_token;
  if (typeof accessToken !== "string" || accessToken.length < 20) {
    return new Response("GitHub authorization refused", { status: 403 });
  }

  const identityResponse = await oauthStep("github_identity", () => fetch("https://api.github.com/user", {
    headers: {
      "Accept": "application/vnd.github+json",
      "Authorization": "Bearer " + accessToken,
      "User-Agent": "truthrail-tools-owner-validation",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    signal: AbortSignal.timeout(8000),
    redirect: "error",
  }));
  if (!identityResponse.ok) throw new OAuthStepError("github_identity", new Error("github_identity_unavailable"));
  const identity = await oauthStep("github_identity", () => identityResponse.json());
  const ownerId = String(identity?.id ?? "");
  if (!/^[1-9][0-9]{0,19}$/.test(ownerId) ||
      ownerId !== env.OWNER_GITHUB_ID) {
    // Do not create a grant for any other GitHub user.
    return new Response("This MCP server is owner-only", {
      status: 403, headers: { "Cache-Control": "no-store" },
    });
  }
  const grant = await oauthStep("grant_creation", () => oauth.completeAuthorization({
    request: original,
    userId: ownerId,
    metadata: {},
    scope: ["mcp:read"],
    props: { ownerGithubId: ownerId },
  }));
  headers.set("Location", grant.redirectTo);
  return new Response(null, { status: 302, headers });
}

const defaultHandler = {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health" && request.method === "GET") {
      // No internal ports, account IDs, or authorization state in health.
      const ready = configured(env) && typeof env.OAUTH_KV?.get === "function";
      return Response.json({
        service: "truthrail-tools",
        status: ready ? "ready_for_auth_validation" : "not_provisioned",
      }, {
        status: ready ? 200 : 503,
        headers: { "Cache-Control": "no-store" },
      });
    }
    if (!["/authorize", "/callback"].includes(url.pathname)) {
      return new Response("Not found", { status: 404 });
    }
    if (!configured(env)) {
      return new Response("MCP identity provider not provisioned", { status: 503 });
    }
    try {
      if (url.pathname === "/callback" && request.method === "GET") {
        return await finishGitHubLogin(request, env);
      }
      if (url.pathname !== "/authorize" || !["GET", "POST"].includes(request.method)) {
        return new Response("Method not allowed", { status: 405 });
      }
      const oauth = env.OAUTH_PROVIDER;
      if (request.method === "GET") {
        const authorization = await oauthStep("client_validation", () => oauth.parseAuthRequest(request));
        const details = await oauthStep("client_description", () => oauth.describeConsent(authorization));
        const consent = await oauthStep("consent_creation", () => oauth.beginConsent(authorization));
        consent.headers.set("Content-Type", "text/html; charset=utf-8");
        return new Response(consentPage(details, consent.handle), {
          headers: consent.headers,
        });
      }
      const form = await oauthStep("consent_submission", () => request.formData());
      const handle = form.get("handle");
      if (typeof handle !== "string" || handle.length > 512 || !handle) {
        return new Response("Invalid consent request", { status: 400 });
      }
      if (form.get("decision") !== "approve") {
        const denied = await oauthStep("consent_decision", () => oauth.denyConsent(request, handle));
        return new Response(null, { status: 302, headers: denied.headers });
      }
      const approved = await oauthStep("consent_decision", () => oauth.approveConsent(request, handle, {
        scope: ["mcp:read"],
      }));
      return startGitHubLogin(approved, oauth, env);
    } catch (error) {
      return authError(error);
    }
  },
};

const apiHandler = {
  async fetch(request, env, ctx) {
    // OAuthProvider has validated the bearer token, but the permission and
    // owner binding are checked on EACH MCP request as defense in depth.
    if (!configured(env) ||
        typeof ctx.props?.ownerGithubId !== "string" ||
        ctx.props.ownerGithubId !== env.OWNER_GITHUB_ID) {
      return new Response("Forbidden", { status: 403 });
    }
    if (!ctx.auth?.scope?.includes("mcp:read")) {
      return new Response("Insufficient scope", { status: 403 });
    }
    return handleAuthenticatedMcp(request, env);
  },
};

/**
 * One Worker = OAuth authorization server + one protected MCP resource.
 * Do not export the unprotected MCP handler as the Worker entrypoint.
 */
export default new OAuthProvider({
  apiRoute: "/mcp",
  apiHandler,
  defaultHandler,
  authorizeEndpoint: "/authorize",
  tokenEndpoint: "/oauth/token",
  scopesSupported: ["mcp:read"],
  requiredScopes: ["mcp:read"],
  clientIdMetadataDocumentEnabled: true,
  resourceMetadata: {
    resource: RESOURCE,
    authorization_servers: [ORIGIN],
  },
});
