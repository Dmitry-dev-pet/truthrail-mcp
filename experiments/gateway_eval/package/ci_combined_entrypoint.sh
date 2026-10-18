#!/bin/bash
# OFF-VPS CI ONLY: one Agentgateway, two JWT route policies, two private NICs.
set -Eeuo pipefail
if [ ! -r /etc/agentgateway/config.yaml ]; then
  echo "blocked_missing_test_policy" >&2
  exit 42
fi
if [ "$(printenv ALFRED_CI_TEST_ONLY 2>/dev/null || true)" != "1" ] ||
   [ "$(printenv ALFRED_CI_OWNER_AUD 2>/dev/null || true)" != "http://172.30.143.10:8765/mcp" ] ||
   [ "$(printenv ALFRED_CI_EXTERNAL_AUD 2>/dev/null || true)" != "http://172.30.144.10:8766/mcp" ]; then
  echo "blocked_unapproved_ci_execution_context" >&2
  exit 44
fi
export ALFRED_OWNER_ONLY_FIXTURE=1
python /app/dual_issuer.py /tmp/ephemeral_tokens &
issuer_pid=$!
# Agentgateway may validate/fetch both JWT JWKS targets on startup.
# Wait for readiness instead of starting all subprocesses concurrently.
jwks_ready=false
for _ in $(seq 1 80); do
  if ! kill -0 "$issuer_pid" 2>/dev/null; then
    echo "combined_issuer_exited_during_start" >&2; exit 1
  fi
  if python -c "from urllib.request import urlopen; r=urlopen('http://127.0.0.1:18767/owner/jwks.json', timeout=.2); assert r.status == 200" >/dev/null 2>&1; then
    jwks_ready=true; break
  fi
  sleep .1
done
test "$jwks_ready" = true || { echo "combined_jwks_not_ready" >&2; exit 1; }
echo "combined_issuer_ready=true"
python /app/peer_backend.py &
backend_pid=$!
backend_ready=false
for _ in $(seq 1 80); do
  if ! kill -0 "$backend_pid" 2>/dev/null; then
    echo "combined_peer_backend_exited_during_start" >&2; exit 1
  fi
  if python -c "import socket; socket.create_connection(('127.0.0.1',18765), timeout=.2).close()" >/dev/null 2>&1; then
    backend_ready=true; break
  fi
  sleep .1
done
test "$backend_ready" = true || { echo "combined_backend_not_ready" >&2; exit 1; }
echo "combined_backend_ready=true"
/usr/local/bin/agentgateway -f /etc/agentgateway/config.yaml &
gateway_pid=$!
cleanup() {
  trap - EXIT TERM INT
  kill "$issuer_pid" "$backend_pid" "$gateway_pid" 2>/dev/null || true
  wait "$issuer_pid" 2>/dev/null || true
  wait "$backend_pid" 2>/dev/null || true
  wait "$gateway_pid" 2>/dev/null || true
}
trap cleanup EXIT TERM INT
set +e
wait -n "$issuer_pid" "$backend_pid" "$gateway_pid"
code=$?
set -e
echo "combined_ci_child_exited" >&2
exit "$code"
