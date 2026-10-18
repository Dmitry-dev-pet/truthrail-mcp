#!/bin/bash
# Launch ONE Agentgateway router and ONE loopback-only Peer Chat worker.
# Operator must independently supply all auth/scopes/route config and JWKS.
set -Eeuo pipefail
umask 077
config=/etc/agentgateway/config.yaml
owner_pub=/run/alfred-auth/owner-jwks.json
if [ ! -r "$config" ] || [ ! -r "$owner_pub" ]; then
  echo "alfred_missing_operator_config_or_owner_jwks" >&2
  exit 42
fi
if [ -z "$(printenv ALFRED_PEER_TRUSTED_HOSTS 2>/dev/null || true)" ]; then
  echo "alfred_missing_reviewed_private_Host_list" >&2
  exit 43
fi
# Fail closed. The public JWKS must not contain any RSA private key fields.
python - <<'PY'
import json
from pathlib import Path
data = json.loads(Path("/run/alfred-auth/owner-jwks.json").read_text())
assert isinstance(data, dict) and isinstance(data.get("keys"), list)
assert 1 <= len(data["keys"]) <= 8
for key in data["keys"]:
    assert isinstance(key, dict) and key.get("kty") == "RSA"
    assert key.get("kid") and key.get("n") and key.get("e")
    assert "d" not in key and "p" not in key and "q" not in key
from alfred_gateway_core.transport_security import transport_security
settings = transport_security()
assert settings.enable_dns_rebinding_protection
PY
# Gateways must refuse unauthenticated traffic. The signed user/service JWTs
# are NOT stored here; a tunnel-local header file belongs to tunnel-client.
if ! /usr/local/bin/agentgateway --validate-only -f "$config" >/dev/null; then
  echo "alfred_gateway_policy_validation_failed" >&2
  exit 44
fi
python -m alfred_gateway_core.serve &
peer_pid=$!
/usr/local/bin/agentgateway -f "$config" &
gateway_pid=$!
cleanup() {
  trap - EXIT TERM INT
  kill "$peer_pid" "$gateway_pid" 2>/dev/null || true
  wait "$peer_pid" 2>/dev/null || true
  wait "$gateway_pid" 2>/dev/null || true
}
trap cleanup EXIT TERM INT
set +e
wait -n "$peer_pid" "$gateway_pid"
status=$?
set -e
echo "alfred_core_child_exited" >&2
# A child unexpectedly exited; container must stop for reviewed operator recovery.
if [ "$status" -eq 0 ]; then exit 1; fi
exit "$status"
