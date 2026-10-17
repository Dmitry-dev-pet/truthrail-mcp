#!/bin/bash
# One disposable image, two bounded processes; backend bound to container loopback.
set -Eeuo pipefail
config=/etc/agentgateway/config.yaml
if [ ! -r "$config" ]; then
  echo "peer_gateway_blocked_missing_reviewed_config" >&2
  exit 42
fi
python /app/peer_backend.py &
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
exit_status=$?
set -e
echo "peer_gateway_child_exited" >&2
exit "$exit_status"
