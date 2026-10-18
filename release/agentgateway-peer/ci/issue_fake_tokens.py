"""GitHub Actions only: create ephemeral RS256 JWT fixtures outside runtime image.

DO NOT use this tool for actual Alfred credentials. It deliberately emits only
temporary synthetic test JWTs and PUBLIC JWKS, never a production signing key.
The private test signing keys live exclusively in this short-lived process.
"""
from pathlib import Path
import json
import sys
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

OWNER = "http://172.30.143.10:8765/mcp"
EXTERNAL = "http://172.30.144.10:8766/mcp"
ISSUER_OWNER = "https://owner-ci.alfred.test"
ISSUER_EXTERNAL = "https://external-ci.alfred.test"


def main(root: Path) -> None:
    if root.exists() or len(sys.argv) != 2:
        raise SystemExit("CI test root must not exist")
    root.mkdir(mode=0o700, parents=True)
    owner_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ext_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    for private, kid, filename in (
        (owner_key, "owner-fixture", "owner-jwks.json"),
        (ext_key, "external-fixture", "external-jwks.json"),
    ):
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
        jwk["kid"] = kid
        file = root / filename
        file.write_text(json.dumps({"keys": [jwk]}), encoding="utf-8")
        file.chmod(0o644)

    def issue(name, key, kid, issuer, aud, sub, scopes, delta=300):
        now = int(time.time())
        issued_at = now if delta > 0 else now + delta - 30
        raw = jwt.encode({
            "iss": issuer, "aud": aud, "sub": sub, "scopes": scopes,
            "iat": issued_at, "nbf": issued_at - 1,
            "exp": now + delta,
        }, key, algorithm="RS256", headers={"kid": kid})
        dest = root / name
        dest.write_text(raw, encoding="ascii")
        dest.chmod(0o600)

    issue("owner.jwt", owner_key, "owner-fixture", ISSUER_OWNER,
          OWNER, "alfred-native-owner", ["alfred:owner"])
    issue("owner_bad_aud.jwt", owner_key, "owner-fixture", ISSUER_OWNER,
          EXTERNAL, "alfred-native-owner", ["alfred:owner"])
    issue("owner_expired.jwt", owner_key, "owner-fixture", ISSUER_OWNER,
          OWNER, "alfred-native-owner", ["alfred:owner"], delta=-300)
    issue("external_writer.jwt", ext_key, "external-fixture", ISSUER_EXTERNAL,
          EXTERNAL, "external-writer",
          ["chat:join", "chat:send", "chat:wait", "chat:status"])
    issue("external_reader.jwt", ext_key, "external-fixture", ISSUER_EXTERNAL,
          EXTERNAL, "external-reader", ["chat:status"])
    issue("external_claims_owner.jwt", ext_key, "external-fixture",
          ISSUER_EXTERNAL, EXTERNAL, "forged-owner",
          ["alfred:owner", "chat:status"])
    issue("external_expired.jwt", ext_key, "external-fixture",
          ISSUER_EXTERNAL, EXTERNAL, "external-reader",
          ["chat:status"], delta=-300)
    issue("external_bad_aud.jwt", ext_key, "external-fixture",
          ISSUER_EXTERNAL, OWNER, "external-reader",
          ["chat:status"])
    print("fixture_public_jwks=generated; temporary_signed_JWTs=private; real_issuer_used=false")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python ci_issue_tokens.py /tmp/new_directory")
    main(Path(sys.argv[1]))
