/** RFC 7636 S256 challenge for the upstream GitHub OAuth App login.
 *  The secret verifier is kept only in the Workers OAuth Provider's
 *  encrypted, browser-bound beginUpstream() state.
 */
export async function githubPkce() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  const verifier = Array.from(bytes, n => n.toString(16).padStart(2, "0")).join("");
  const digest = new Uint8Array(await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(verifier)
  ));
  const challenge = btoa(String.fromCharCode(...digest))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return { verifier, challenge };
}

export function validGithubPkceVerifier(value) {
  return typeof value === "string" && /^[a-z0-9_-]{43,128}$/.test(value);
}
