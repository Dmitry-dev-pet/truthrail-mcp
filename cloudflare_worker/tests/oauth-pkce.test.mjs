import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { githubPkce, validGithubPkceVerifier } from "../src/oauth-pkce.js";

test("S256 challenge binds to randomly generated verifier", async () => {
  const first = await githubPkce();
  const second = await githubPkce();
  assert.equal(validGithubPkceVerifier(first.verifier), true);
  assert.equal(first.verifier.length, 64);
  assert.equal(first.challenge.length, 43);
  assert.match(first.challenge, /^[A-Za-z0-9_-]{43}$/);
  const expected = createHash("sha256").update(first.verifier)
    .digest("base64url");
  assert.equal(first.challenge, expected);
  assert.notEqual(first.verifier, second.verifier);
  assert.notEqual(first.challenge, second.challenge);
});

test("bad PKCE verifiers are rejected before code exchange", () => {
  for (const invalid of ([
    undefined, null, "", 42, {}, "abc", "!".repeat(64),
    "a".repeat(42), "a".repeat(129), "a".repeat(32) + "."
  ])) {
    assert.equal(validGithubPkceVerifier(invalid), false);
  }
});
