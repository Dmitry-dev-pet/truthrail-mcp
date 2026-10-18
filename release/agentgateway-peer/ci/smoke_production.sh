#!/usr/bin/env bash
# OFF-VPS GitHub Actions only. Ephemeral fake issuer is NEVER in runtime image.
set -Eeuo pipefail
test "$(printenv GITHUB_ACTIONS 2>/dev/null || true)" = true
test "$#" -eq 1
image="$1"
name="alfred-core-ci-$$"
owner_net="alfred-core-owner-$$"
external_net="alfred-core-external-$$"
tmp="$(mktemp -d)"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker network rm "$owner_net" "$external_net" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT
python release/agentgateway-peer/ci/issue_fake_tokens.py "$tmp/private"
mkdir -m 0755 "$tmp/public" "$tmp/config"
mkdir -m 0700 "$tmp/session"
cp "$tmp/private/external-jwks.json" "$tmp/public/external-jwks.json"
chmod 0644 "$tmp/public/"*.json
cat > "$tmp/config/approved-targets.json" <<'JSON'
{"schema":1,"targets":[{"name":"peer-chat","host":"http://127.0.0.1:18765/mcp"}]}
JSON
cat > "$tmp/config/config.yaml" <<'YAML'
# Synthetic strict-auth CI config, never deploy to a real VPS.
config:
  adminAddr: 127.0.0.1:15078
gateways:
  native-owner:
    bindAddress: 172.30.143.10
    port: 8765
  cloudflare-external:
    bindAddress: 172.30.144.10
    port: 8766
routes:
- name: native-owner-route
  gateways: [native-owner]
  matches:
  - path:
      pathPrefix: /mcp
  policies:
    apiKey:
      mode: strict
      location:
        header:
          name: x-alfred-native-owner-key
      keys:
      - keyHash: sha256:__OWNER_KEY_SHA256__
        metadata:
          role: owner
    mcpAuthorization:
      rules:
      - 'apiKey.role == "owner"'
  backends:
  - mcp:
      targets:
      - name: peer-chat
        mcp:
          host: http://127.0.0.1:18765/mcp
- name: cloudflare-external-route
  gateways: [cloudflare-external]
  matches:
  - path:
      pathPrefix: /mcp
  policies:
    mcpAuthentication:
      mode: strict
      issuer: https://external-ci.alfred.test
      audiences: ["http://172.30.144.10:8766/mcp"]
      jwks:
        file: /run/alfred-auth/external-jwks.json
      jwtValidationOptions:
        requiredClaims: [exp, nbf, aud, sub]
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
# Only the public digest is placed in gateway config; raw secret is client-only.
sed -i "s/__OWNER_KEY_SHA256__/$(cat "$tmp/private/owner-key.sha256")/" "$tmp/config/config.yaml"

export ALFRED_PEER_TRUSTED_HOSTS=172.30.143.10:8765,172.30.144.10:8766
python -m alfred_gateway_core.validate_policy "$tmp/config/config.yaml" \
  "$tmp/config/approved-targets.json" \
  "$tmp/public/external-jwks.json" "$ALFRED_PEER_TRUSTED_HOSTS"
cp "$tmp/config/config.yaml" "$tmp/permissive.yaml"
sed -i 's/mode: strict/mode: permissive/g' "$tmp/permissive.yaml"
if python -m alfred_gateway_core.validate_policy "$tmp/permissive.yaml" \
  "$tmp/config/approved-targets.json" \
  "$tmp/public/external-jwks.json" "$ALFRED_PEER_TRUSTED_HOSTS" >/dev/null 2>&1; then
  echo "CRITICAL:unprotected_route_accepted" >&2; exit 1
fi
cp "$tmp/config/config.yaml" "$tmp/public-ip.yaml"
sed -i 's/bindAddress: 172.30.143.10/bindAddress: 0.0.0.0/' "$tmp/public-ip.yaml"
if python -m alfred_gateway_core.validate_policy "$tmp/public-ip.yaml" \
  "$tmp/config/approved-targets.json" \
  "$tmp/public/external-jwks.json" "$ALFRED_PEER_TRUSTED_HOSTS" >/dev/null 2>&1; then
  echo "CRITICAL:public_owner_listener_accepted" >&2; exit 1
fi
echo 'invalid_auth_config_denied=true; public_owner_bind_denied=true'

set +e
docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=8m \
  --memory=176m --memory-swap=224m \
  --env ALFRED_PEER_TRUSTED_HOSTS "$image" >/dev/null 2>&1
no_config=$?
set -e
test "$no_config" -eq 42

docker network create --driver bridge --internal \
  --subnet 172.30.143.0/24 "$owner_net" >/dev/null
docker network create --driver bridge --internal \
  --subnet 172.30.144.0/24 "$external_net" >/dev/null
docker create --name "$name" --network "$owner_net" --ip 172.30.143.10 \
  --read-only --security-opt=no-new-privileges --cap-drop=ALL \
  --tmpfs /tmp:rw,nosuid,nodev,size=8m \
  --memory=176m --memory-swap=224m --pids-limit=64 --cpus=0.50 \
  --env ALFRED_PEER_TRUSTED_HOSTS \
  --mount "type=bind,src=$tmp/config,dst=/etc/agentgateway,readonly" \
  --mount "type=bind,src=$tmp/public,dst=/run/alfred-auth,readonly" \
  "$image" >/dev/null
docker network connect --ip 172.30.144.10 "$external_net" "$name"
docker start "$name" >/dev/null
test -z "$(docker port "$name")"
test "$(docker inspect --format '{{.Config.User}}' "$name")" = '10001:10001'
test "$(docker inspect --format '{{.HostConfig.ReadonlyRootfs}}' "$name")" = true

for _ in $(seq 1 65); do
  if docker exec "$name" python -c "import socket;socket.create_connection(('127.0.0.1',18765),timeout=.2).close()" >/dev/null 2>&1; then
    break
  fi
  if [ "$(docker inspect --format '{{.State.Running}}' "$name")" != true ]; then
    docker inspect --format 'app_running={{.State.Running}} oom={{.State.OOMKilled}} exit={{.State.ExitCode}}' "$name"
    docker logs "$name" 2>&1 | grep -E 'Error|error|invalid|failed|blocked|Traceback' | tail -10 || true
    exit 1
  fi
  sleep .2
done

# App has only an external PUBLIC JWKS and native owner keyHash in config.
# Raw owner key and signed external JWTs stay outside the server image.
docker exec "$name" test ! -e /run/alfred-auth/owner.jwt
docker exec "$name" test ! -e /run/alfred-auth/owner.key
docker exec "$name" test ! -e /tmp/ephemeral_tokens
docker exec "$name" test ! -e /app/dual_issuer.py
id_u="$(id -u)"
id_g="$(id -g)"
run_probe() {
  local role="$1" net
  if [ "$role" = owner ]; then net="$owner_net"; else net="$external_net"; fi
  docker run --rm --network "$net" --user "$id_u:$id_g" \
    --read-only --security-opt=no-new-privileges --cap-drop=ALL \
    --tmpfs /tmp:rw,nosuid,nodev,size=8m --pids-limit=32 \
    --mount "type=bind,src=$tmp/private,dst=/run/test-auth,readonly" \
    --mount "type=bind,src=$tmp/session,dst=/run/test-session" \
    --mount "type=bind,src=$PWD/release/agentgateway-peer/ci/probe.py,dst=/probe.py,readonly" \
    --entrypoint python "$image" /probe.py "$role"
}
run_probe owner > "$tmp/owner.log" 2>&1 &
powner=$!
run_probe external > "$tmp/external.log" 2>&1 &
pext=$!
if ! wait "$powner"; then
  echo "owner_probe_failed" >&2
  grep -E 'AssertionError|Exception|RuntimeError|Traceback' "$tmp/owner.log" | tail -10 >&2 || true
  exit 1
fi
if ! wait "$pext"; then
  echo "external_probe_failed" >&2
  grep -E 'AssertionError|Exception|RuntimeError|Traceback' "$tmp/external.log" | tail -10 >&2 || true
  exit 1
fi
grep '"round_trips": 3' "$tmp/owner.log"
grep '"round_trips": 3' "$tmp/external.log"

test -z "$(docker port "$name")"
test "$(docker inspect --format '{{.State.Running}}' "$name")" = true
usage="$(docker stats --no-stream --format '{{.MemUsage}}' "$name")"
peak="$(docker exec "$name" cat /sys/fs/cgroup/memory.peak)"
case "$peak" in *[!0-9]*|'') echo "unavailable_cgroup_peak" >&2; exit 1;; esac
test "$peak" -lt 184549376
printf 'prod_candidate_ci_peak_bytes=%s; cgroup=%s; cap=176MiB; owner_auth=strict_api_key; external_auth=strict_jwt_scopes; built_in_signer=false; host_ports=0; VPS_mutated=false\n' "$peak" "$usage"
