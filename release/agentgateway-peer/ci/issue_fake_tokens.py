"""CI ONLY: ephemeral native API-key + external RS256 JWT fixtures.

Never generates or installs a production identity. The plaintext owner
API key stays in runner-private 0600 client file; the gateway receives only
a public SHA-256 digest and *separate* external PUBLIC JWKS.
"""
from pathlib import Path
import hashlib
import json
import secrets
import sys
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

OWNER = "http://172.30.143.10:8765/mcp"
EXTERNAL = "http://172.30.144.10:8766/mcp"
ISSUER_EXTERNAL = "https://external-ci.alfred.test"


def main(root: Path) -> None:
    if root.exists() or len(sys.argv) != 2:
        raise SystemExit("CI test root must not exist")
    root.mkdir(mode=0o700, parents=True)
    owner_key = secrets.token_urlsafe(48)
    owner_key_file = root / "owner.key"
    owner_key_file.write_text(owner_key, encoding="ascii")
    owner_key_file.chmod(0o600)
    digest = hashlib.sha256(owner_key.encode("ascii")).hexdigest()
    hash_file = root / "owner-key.sha256"
    hash_file.write_text(digest, encoding="ascii")
    hash_file.chmod(0o600)

    ext_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(ext_key.public_key()))
    jwk["kid"] = "external-fixture"
    public_file = root / "external-jwks.json"
    public_file.write_text(json.dumps({"keys": [jwk]}), encoding="utf-8")
    public_file.chmod(0o644)

    def issue(name, aud, sub, scopes, delta=300):
        now = int(time.time())
        issued_at = now if delta > 0 else now + delta - 30
        raw = jwt.encode({
            "iss": ISSUER_EXTERNAL, "aud": aud, "sub": sub,
            "scopes": scopes, "iat": issued_at, "nbf": issued_at - 1,
            "exp": now + delta,
        }, ext_key, algorithm="RS256", headers={"kid": "external-fixture"})
        dest = root / name
        dest.write_text(raw, encoding="ascii")
        dest.chmod(0o600)

    issue("external_writer.jwt", EXTERNAL, "external-writer",
          ["chat:join", "chat:send", "chat:wait", "chat:status"])
    issue("external_reader.jwt", EXTERNAL, "external-reader", ["chat:status"])
    issue("external_claims_owner.jwt", EXTERNAL, "forged-owner",
          ["alfred:owner", "chat:status"])
    issue("external_expired.jwt", EXTERNAL, "external-reader",
          ["chat:status"], delta=-300)
    issue("external_bad_aud.jwt", OWNER, "external-reader", ["chat:status"])
    print("synthetic_owner_key_sha256=prepared; external_JWTs=private; owner_JWT_issuer=false")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python issue_fake_tokens.py /tmp/new_dir")
    main(Path(sys.argv[1]))
