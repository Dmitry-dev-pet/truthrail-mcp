#!/usr/bin/env bash
# Disposable, UNAUTHENTICATED local-network-only fixture.
# Production must enforce both JWT and network isolation independently.
set -euo pipefail
test "$#" -eq 1
image="$1"
name="alfred-dual-route-ci-$$"
owner_network="alfred-owner-ci-$$"
external_network="alfred-external-ci-$$"
owner_addr="172.30.143.10"
external_addr="172.30.144.10"
tmp="$(mktemp -d)"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker network rm "$owner_network" "$external_network" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

docker network create --internal --driver bridge --subnet 172.30.143.0/24 "$owner_network" >/dev/null
docker network create --internal --driver bridge --subnet 172.30.144.0/24 "$external_network" >/dev/null
cat > "$tmp/config.yaml" <<'YAML'
# CI ONLY, NO AUTH, NEVER DEPLOY TO VPS.
config:
  adminAddr: 127.0.0.1:15078
gateways:
  native-owner:
    port: 8765
    bindAddress: 172.30.143.10
  cloudflare-external:
    port: 8766
    bindAddress: 172.30.144.10
routes:
- name: native-owner-test
  gateways: [native-owner]
  matches:
  - path:
      pathPrefix: /mcp
  backends:
  - mcp:
      targets:
      - name: peer-chat
        mcp:
          host: http://127.0.0.1:18765/mcp
- name: external-test
  gateways: [cloudflare-external]
  matches:
  - path:
      pathPrefix: /mcp
  backends:
  - mcp:
      targets:
      - name: peer-chat
        mcp:
          host: http://127.0.0.1:18765/mcp
YAML
chmod 0444 "$tmp/config.yaml"

# Connect both networks before starting, so both NIC IPs exist at bind time.
docker create --name "$name" --network "$owner_network" --ip "$owner_addr" \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=8m \
  --memory=176m --memory-swap=224m --pids-limit=64 \
  --mount "type=bind,src=$tmp/config.yaml,dst=/etc/agentgateway/config.yaml,readonly" \
  "$image" >/dev/null
docker network connect --ip "$external_addr" "$external_network" "$name"
docker start "$name" >/dev/null
test -z "$(docker port "$name")"
test "$(docker inspect --format '{{.Config.User}}' "$name")" = "10001:10001"
docker inspect --format '{{.State.Running}} {{.HostConfig.Memory}} {{.HostConfig.ReadonlyRootfs}}' "$name" \
  | grep -q '^true 184549376 true$'

probe() {
  local net="$1" host="$2" port="$3" expect="$4"
  docker run --rm --network "$net" \
    --read-only --cap-drop=ALL --security-opt=no-new-privileges \
    python:3.12-slim-bookworm python -c '
import socket, sys
addr, port, expected = sys.argv[1], int(sys.argv[2]), sys.argv[3]
with socket.socket() as s:
    s.settimeout(1.0)
    try:
        success = s.connect_ex((addr, port)) == 0
    except OSError:
        success = False
sys.exit(0 if success == (expected == "accept") else 1)
' "$host" "$port" "$expect"
}

# Both valid routes MUST work before any denial can count as evidence.
ready=false
for _ in $(seq 1 25); do
  if probe "$owner_network" "$owner_addr" 8765 accept >/dev/null 2>&1 &&
     probe "$external_network" "$external_addr" 8766 accept >/dev/null 2>&1; then
    ready=true; break
  fi
  sleep .2
done
test "$ready" = true
test "$(docker inspect --format '{{.State.Running}}' "$name")" = true
probe "$owner_network" "$owner_addr" 8765 accept
probe "$external_network" "$external_addr" 8766 accept

# External knows owner IP/port but must not be able to connect.
probe "$external_network" "$external_addr" 8765 deny
probe "$external_network" "$owner_addr" 8765 deny
probe "$owner_network" "$owner_addr" 8766 deny
probe "$owner_network" "$external_addr" 8766 deny

# Direct backend TCP must also be unreachable on either bridge NIC.
probe "$external_network" "$external_addr" 18765 deny
probe "$owner_network" "$owner_addr" 18765 deny

printf 'two_docker_networks=verified; both_valid_routes_work=true; cross_network_tcp_denied=6; backend_not_exposed=true; host_published_ports=0; vps_mutated=false\n'
