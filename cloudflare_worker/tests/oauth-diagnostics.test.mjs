import test from "node:test";
import assert from "node:assert/strict";
import { OAuthStepError, oauthStep, oauthFailure } from "../src/oauth-diagnostics.js";

class StubAuthorizationError extends Error {}
class StubMetadataError extends Error {}

const classify = {
  isAuthorizationError: error => error instanceof StubAuthorizationError,
  isMetadataError: error => error instanceof StubMetadataError
};

test("expected authorization failures expose only fixed labels, not state/cookies", async () => {
  const internal = new StubAuthorizationError("code=SECRET&state=VERY_PRIVATE&cookie=IMPORTANT");
  const wrapped = await oauthStep("callback_state", async () => { throw internal; }).catch(x => x);
  assert.ok(wrapped instanceof OAuthStepError);
  const safe = oauthFailure(wrapped, classify);
  assert.deepEqual(safe, { stage: "callback_state", kind: "invalid_or_expired", status: 400 });
  const output = JSON.stringify(safe);
  for (const secret of ["SECRET", "VERY_PRIVATE", "IMPORTANT", "code=", "cookie="]) {
    assert.equal(output.includes(secret), false);
  }
});

test("client metadata lookup failures have a distinct safe category", () => {
  const safe = oauthFailure(
    new OAuthStepError("client_validation", new StubMetadataError("private metadata document URL")),
    classify
  );
  assert.deepEqual(safe, { stage: "client_validation", kind: "client_metadata_unavailable", status: 400 });
  assert.equal(JSON.stringify(safe).includes("private metadata document"), false);
});

test("runtime errors reveal no stack, URL, code or provider message", () => {
  const error = new OAuthStepError("github_token_exchange",
    new Error("access_token=SUPER_SECRET; https://github.com/private-path"));
  assert.deepEqual(oauthFailure(error, classify), {
    stage: "github_token_exchange", kind: "runtime_unavailable", status: 503
  });
});

test("untrusted stage names are not reflected", () => {
  const safe = oauthFailure(new OAuthStepError("<script>exfiltrate</script>", new Error("private")), classify);
  assert.equal(safe.stage, "unspecified");
  assert.equal(JSON.stringify(safe).includes("script"), false);
});

test("successful work is passed through without modification", async () => {
  assert.equal(await oauthStep("consent_creation", async () => 42), 42);
});
