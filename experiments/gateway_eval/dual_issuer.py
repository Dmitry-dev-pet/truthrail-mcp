"""Ephemeral TWO trust roots: owner JWT cannot be forged by external issuer.

GitHub-hosted isolated research only. Private signing keys live solely inside
this process and are never written to disk or printed in CI output.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

BASE = "http://127.0.0.1:18767"
OWNER_AUD = "http://127.0.0.1:18766/mcp"
EXTERNAL_AUD = "http://127.0.0.1:18769/mcp"


def main(output_dir):
    folder = Path(output_dir)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    owner_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    external_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwks(key, kid):
        obj = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        obj["kid"] = kid
        return json.dumps({"keys": [obj]}).encode("utf-8")

    public = {
        "/owner/jwks.json": jwks(owner_key, "owner-ci-key"),
        "/external/jwks.json": jwks(external_key, "external-ci-key"),
    }

    def issue(name, key, issuer, subject, aud, scopes, expire=180):
        now = int(time.time())
        token = jwt.encode({
            "iss": BASE + "/" + issuer, "aud": aud, "sub": subject,
            "scopes": scopes, "iat": now, "nbf": now - 1,
            "exp": now + expire,
        }, key, algorithm="RS256",
            headers={"kid": issuer + "-ci-key"})
        path = folder / name
        path.write_text(token, encoding="ascii")
        path.chmod(0o600)

    issue("owner.jwt", owner_key, "owner", "approved-owner",
          OWNER_AUD, ["alfred:owner"])
    issue("owner_wrong_audience.jwt", owner_key, "owner", "approved-owner",
          EXTERNAL_AUD, ["alfred:owner"])
    issue("owner_expired.jwt", owner_key, "owner", "approved-owner",
          OWNER_AUD, ["alfred:owner"], -10)

    issue("external_writer.jwt", external_key, "external", "external-writer",
          EXTERNAL_AUD,
          ["chat:join", "chat:send", "chat:wait", "chat:status"])
    issue("external_reader.jwt", external_key, "external", "external-reader",
          EXTERNAL_AUD, ["chat:status"])
    # An external token claims the OWNER scope, but is signed by the
    # EXTERNAL identity authority. It must never grant an owner tool.
    issue("external_claims_owner.jwt", external_key, "external",
          "external-forged-owner", EXTERNAL_AUD,
          ["alfred:owner", "chat:status"])
    issue("external_wrong_audience.jwt", external_key, "external",
          "external-reader", OWNER_AUD, ["chat:status"])
    issue("external_expired.jwt", external_key, "external",
          "external-reader", EXTERNAL_AUD, ["chat:status"], -10)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = public.get(self.path)
            if payload is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    print("two ephemeral JWKS issuers ready", flush=True)
    HTTPServer(("127.0.0.1", 18767), Handler).serve_forever()


if __name__ == "__main__":
    main(sys.argv[1])
