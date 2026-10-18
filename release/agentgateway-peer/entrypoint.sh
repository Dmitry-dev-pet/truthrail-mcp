#!/bin/bash
# Launch ONE Agentgateway router and ONE loopback-only Peer Chat worker.
# Operator must independently supply strict hashed owner APIKey policy,
# external public JWKS only when external JWT route is enabled, and targets.
set -Eeuo pipefail
umask 077
config=/etc/agentgateway/config.yaml
approved_targets=/etc/agentgateway/approved-targets.json
if [ ! -r "$config" ] || [ ! -r "$approved_targets" ]; then
  echo "alfred_missing_operator_config_or_targets" >&2
  exit 42
fi
if [ -z "$(printenv ALFRED_PEER_TRUSTED_HOSTS 2>/dev/null || true)" ]; then
  echo "alfred_missing_reviewed_private_Host_list" >&2
  exit 43
fi
# Fail closed. The separate native owner key MUST NOT be mounted into
# the Agentgateway app; its SHA-256 + role is in reviewed config only.
# Optional external route retains strict JWT issuer/aud/JWKS and scopes.
# Run this exact validator BEFORE accepting every later atomic policy update.
if ! python -m alfred_gateway_core.validate_policy \
    "$config" "$approved_targets" \
    "$(if [ -r /run/alfred-auth/external-jwks.json ]; then echo /run/alfred-auth/external-jwks.json; else echo '-'; fi)" \
    "$ALFRED_PEER_TRUSTED_HOSTS" >/dev/null; then
  echo "alfred_strict_policy_gate_denied" >&2
  exit 45
fi
# Gateways MUST refuse missing/invalid native owner keys and external JWTs.
# Neither the raw owner APIKey nor any external JWT is stored in the app.
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
