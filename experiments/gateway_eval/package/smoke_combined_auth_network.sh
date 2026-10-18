#!/usr/bin/env bash
# HOSTED GITHUB ACTIONS ONLY. This experiment creates ephemeral test JWTs,
# never invokes any VPS/Cloudflare/OpenAI API or reads a production secret.
set -Eeuo pipefail
echo "combined_phase=entry"
test "$#" -eq 2 || { echo "combined_wrong_arity" >&2; exit 1; }
test "$(printenv GITHUB_ACTIONS 2>/dev/null || true)" = true || { echo "combined_not_actions" >&2; exit 1; }
base_image="$1"
test_image="$2"
name="alfred-combined-auth-ci-$$"
owner_network="alfred-owner-jwt-ci-$$"
external_network="alfred-external-jwt-ci-$$"
owner_addr=172.30.143.10
external_addr=172.30.144.10
tmp="$(mktemp -d)"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker network rm "$owner_network" "$external_network" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

docker network create --internal --driver bridge \
  --subnet 172.30.143.0/24 "$owner_network" >/dev/null
docker network create --internal --driver bridge \
  --subnet 172.30.144.0/24 "$external_network" >/dev/null
echo "combined_phase=networks_ready"

cat > "$tmp/config.yaml" <<'YAML'
# HOSTED CI ONLY. Ephemeral local issuer. NEVER deploy this config.
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
- name: native-owner-route
  gateways: [native-owner]
  matches:
  - path:
      pathPrefix: /mcp
  policies:
    mcpAuthentication:
      mode: strict
      issuer: http://127.0.0.1:18767/owner
      audiences: ["http://172.30.143.10:8765/mcp"]
      jwks:
        url: http://127.0.0.1:18767/owner/jwks.json
      resourceMetadata:
        resource: http://172.30.143.10:8765/mcp
        scopesSupported: [alfred:owner]
        bearerMethodsSupported: [header]
    mcpAuthorization:
      rules:
      - 'jwt.sub == "approved-owner" && "alfred:owner" in jwt.scopes'
  backends:
  - mcp:
      targets:
      - name: peer-chat
        mcp:
          host: http://127.0.0.1:18765/mcp
- name: external-scoped-route
  gateways: [cloudflare-external]
  matches:
  - path:
      pathPrefix: /mcp
  policies:
    mcpAuthentication:
      mode: strict
      issuer: http://127.0.0.1:18767/external
      audiences: ["http://172.30.144.10:8766/mcp"]
      jwks:
        url: http://127.0.0.1:18767/external/jwks.json
      resourceMetadata:
        resource: http://172.30.144.10:8766/mcp
        scopesSupported: [chat:join, chat:send, chat:wait, chat:status]
        bearerMethodsSupported: [header]
    mcpAuthorization:
      rules:
      - 'mcp.tool.name == "chat_join" && "chat:join" in jwt.scopes'
      - 'mcp.tool.name == "chat_send" && "chat:send" in jwt.scopes'
      - 'mcp.tool.name == "chat_wait" && "chat:wait" in jwt.scopes'
      - 'mcp.tool.name == "chat_status" && "chat:status" in jwt.scopes'
  backends:
  - mcp:
      targets:
      - name: peer-chat
        mcp:
          host: http://127.0.0.1:18765/mcp
YAML
chmod 0444 "$tmp/config.yaml"
test -s "$tmp/config.yaml"

# Docker container must refuse a missing explicitly supplied config.
set +e
docker run --rm --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=8m \
  --memory=176m --memory-swap=224m \
  --env ALFRED_CI_TEST_ONLY=1 \
  --env ALFRED_CI_OWNER_AUD=http://172.30.143.10:8765/mcp \
  --env ALFRED_CI_EXTERNAL_AUD=http://172.30.144.10:8766/mcp \
  "$test_image" >/dev/null 2>&1
without_config=$?
set -e
echo "combined_phase=no_config_exit_code:$without_config"
test "$without_config" -eq 42

# No host-published port, no Docker socket and no privileged execution.
# Attach BOTH private interfaces before starting to ensure exact bind IPs.
docker create --name "$name" \
  --network "$owner_network" --ip "$owner_addr" \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=8m \
  --memory=176m --memory-swap=224m --pids-limit=64 --cpus=0.5 \
  --env ALFRED_CI_TEST_ONLY=1 \
  --env ALFRED_CI_OWNER_AUD=http://172.30.143.10:8765/mcp \
  --env ALFRED_CI_EXTERNAL_AUD=http://172.30.144.10:8766/mcp \
  --mount "type=bind,src=$tmp/config.yaml,dst=/etc/agentgateway/config.yaml,readonly" \
  "$test_image" >/dev/null
docker network connect --ip "$external_addr" "$external_network" "$name"
docker start "$name" >/dev/null
echo "combined_phase=container_started"
# Nonsecret metadata only: fail early if an expected child crashed or OOM'd.
state="$(docker inspect --format '{{.State.Running}} {{.State.ExitCode}} {{.State.OOMKilled}} {{.HostConfig.Memory}} {{.HostConfig.ReadonlyRootfs}} {{.Config.User}}' "$name")"
echo "combined_container_state=$state"
if [ "$(docker inspect --format '{{.State.Running}}' "$name")" != true ]; then
  docker logs --tail 25 "$name" 2>&1 |
    grep -E 'Traceback|ImportError|ModuleNotFoundError|SyntaxError|error|Error|invalid_test_|combined_ci_child_exited|blocked_' |
    tail -10 >&2 || true
  exit 1
fi

test -z "$(docker port "$name")"
test "$(docker inspect --format '{{.Config.User}}' "$name")" = "10001:10001"
docker inspect --format '{{.State.Running}} {{.HostConfig.Memory}} {{.HostConfig.ReadonlyRootfs}}' "$name" \
  | grep -q '^true 184549376 true$'

# Wait for synthetic issuer to create its tokens INSIDE tmpfs, no logs.
ready=false
for _ in $(seq 1 90); do
  if ! [ "$(docker inspect --format '{{.State.Running}}' "$name")" = true ]; then
    echo "test_gateway_not_running" >&2
    # Prints ONLY sanitised, fixed error-class lines, never test JWTs,
    # headers, request bodies, file contents, or raw application output.
    docker inspect --format 'fixture_exit_code={{.State.ExitCode}} fixture_oom={{.State.OOMKilled}}' "$name" >&2
    docker logs --tail 36 "$name" 2>&1 |
      grep -E 'Traceback|ModuleNotFoundError|ImportError|SyntaxError|invalid_test_|ERROR|Error|error|blocked_|combined_ci_child_exited|oom|panic|permission denied' |
      sed -E 's/(Bearer )[A-Za-z0-9._~+\/-]+/\1[REDACTED]/g' |
      tail -12 >&2 || true
    exit 1
  fi
  if docker exec "$name" test -s /tmp/ephemeral_tokens/owner.jwt >/dev/null 2>&1 &&
     docker exec "$name" test -s /tmp/ephemeral_tokens/external_writer.jwt >/dev/null 2>&1; then
    ready=true; break
  fi
  sleep .15
done
test "$ready" = true

# One live deployment: verify BOTH strict auth policies, BOTH actual MCP
# listeners, three B<->A round trips and every wrong-token denial.
docker exec "$name" python /app/combined_auth_client.py all

# Copy ONLY ephemeral synthetic test JWTs from container tmpfs into a
# local temp folder for independent network-namespace client probes.
# Never echo them to logs or publish them as a CI artifact.
mkdir -m 0700 "$tmp/tokens"
docker cp "$name:/tmp/ephemeral_tokens/." "$tmp/tokens/" >/dev/null
chmod 0700 "$tmp/tokens"
chmod 0600 "$tmp/tokens/"*.jwt

# For the network probes, reuse ONLY the read-only Python MCP base image.
# Each probe has a DIFFERENT Docker network; it receives only the test
# JWTs and test Python source, not the running application's root mount.
runner_uid="$(id -u)"
runner_gid="$(id -g)"
for role in owner external; do
  if [ "$role" = owner ]; then network="$owner_network"; else network="$external_network"; fi
  docker run --rm --network "$network" \
    --user "$runner_uid:$runner_gid" \
    --read-only --cap-drop=ALL --security-opt=no-new-privileges \
    --tmpfs /tmp:rw,nosuid,nodev,size=8m --pids-limit=64 \
    --mount "type=bind,src=$tmp/tokens,dst=/run/test-jwts,readonly" \
    --mount "type=bind,src=$PWD/experiments/gateway_eval/combined_auth_client.py,dst=/probe.py,readonly" \
    --entrypoint python "$base_image" /probe.py "$role" /run/test-jwts
done

test -z "$(docker port "$name")"
test "$(docker inspect --format '{{.State.Running}}' "$name")" = true
stats="$(docker stats --no-stream --format '{{.MemUsage}}' "$name")"
test -n "$stats"

# cgroup v2 high-water is stronger than a single sampled docker stats value.
if docker exec "$name" test -r /sys/fs/cgroup/memory.peak >/dev/null 2>&1; then
  peak="$(docker exec "$name" cat /sys/fs/cgroup/memory.peak)"
  case "$peak" in *[!0-9]*|'') echo "invalid_mem_peak" >&2; exit 1;; esac
  test "$peak" -lt 184549376
  printf 'combined_auth_cgroup_peak_bytes=%s\n' "$peak"
fi
printf 'combined_auth_cgroup_current=%s; docker_cap=176MiB; host_ports=0; no_vps_change=true; no_cloudflare_change=true; no_tunnel_change=true\n' "$stats"
