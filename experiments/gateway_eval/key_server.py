"""Ephemeral local JWKS + test JWTs; never use these credentials outside CI."""
import json
from pathlib import Path
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "http://127.0.0.1:18767"
AUDIENCE = "http://127.0.0.1:18766/mcp"

def main(path):
    folder = Path(path)
    folder.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public["kid"] = "test-only"
    jwks = json.dumps({"keys": [public]}).encode("utf-8")

    def create(sub, scopes, aud=AUDIENCE):
        now = int(time.time())
        return jwt.encode({
            "sub": sub, "iss": ISSUER, "aud": aud,
            "scopes": scopes, "iat": now, "nbf": now - 1, "exp": now + 300,
        }, key, algorithm="RS256", headers={"kid": "test-only"})

    for filename, sub, scopes, aud in [
        ("reader.jwt", "reader", ["atlas:read"], AUDIENCE),
        ("writer.jwt", "writer", ["atlas:read", "chat:send"], AUDIENCE),
        ("wrong-audience.jwt", "outsider", ["atlas:read"], "wrong-audience"),
    ]:
        target = folder / filename
        target.write_text(create(sub, scopes, aud), encoding="ascii")
        target.chmod(0o600)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/jwks.json":
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(jwks)))
            self.end_headers()
            self.wfile.write(jwks)
        def log_message(self, *_): pass

    print("local synthetic JWKS ready", flush=True)
    HTTPServer(("127.0.0.1", 18767), Handler).serve_forever()

if __name__ == "__main__":
    main(sys.argv[1])
