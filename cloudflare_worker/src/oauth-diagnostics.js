/**
 * Safe OAuth failure stages for end-user troubleshooting.
 *
 * Never report error.message, stacks, OAuth codes, client URLs, cookies,
 * request parameters, grants or secrets. Every emitted string comes from
 * this fixed allowlist, not the provider or the request.
 */
const STAGES = Object.freeze(new Set([
  "client_validation",
  "client_description",
  "consent_creation",
  "consent_submission",
  "consent_decision",
  "upstream_start",
  "callback_state",
  "github_token_exchange",
  "github_identity",
  "grant_creation",
  "unspecified"
]));

function safeStage(stage) {
  return STAGES.has(stage) ? stage : "unspecified";
}

export class OAuthStepError extends Error {
  constructor(stage, cause) {
    super("oauth_step_failed");
    this.name = "OAuthStepError";
    this.stage = safeStage(stage);
    this.cause = cause;
  }
}

export async function oauthStep(stage, perform) {
  try {
    return await perform();
  } catch (cause) {
    throw new OAuthStepError(stage, cause);
  }
}

/**
 * Pure, bounded labels. Callers provide type predicates from their own
 * installed SDK instance; no raw or user-supplied details are returned.
 */
export function oauthFailure(error, { isAuthorizationError, isMetadataError } = {}) {
  const stage = error instanceof OAuthStepError ? error.stage : "unspecified";
  const cause = error instanceof OAuthStepError ? error.cause : error;
  const kind = isAuthorizationError?.(cause)
    ? "invalid_or_expired"
    : isMetadataError?.(cause)
      ? "client_metadata_unavailable"
      : "runtime_unavailable";
  return {
    stage,
    kind,
    status: kind === "runtime_unavailable" ? 503 : 400
  };
}
