#!/usr/bin/env bash
# Off-VPS only, real Agentgateway v1.6.0 + private Peer Chat HTTP.
# This is a *capability experiment*, never a production entrypoint/operator.
# It deliberately overrides the image's JWT-only startup validator only in
# temporary GitHub-hosted Docker, pending a separately reviewed replacement.
set -Eeuo pipefail
test "${GITHUB_ACTIONS:-}" = "true"
test "$#" -eq 1
image="$1"
name="alfred-apikey-probe-$$"
network="alfred-apikey-owner-$$"
tmp="$(mktemp -d)"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT
umask 077
mkdir -p "$tmp/config" "$tmp/public" "$tmp/private"
# Only a test fixture is created. Its plaintext never goes into YAML or logs.
python3 - "$tmp/config/config.yaml" "$tmp/private/token" <<'PY'
import hashlib
from pathlib import Path
import secrets
import sys
from textwrap import dedent
key = secrets.token_urlsafe(48)
Path(sys.argv[2]).write_text(key, encoding="ascii")
keyhash = hashlib.sha256(key.encode("ascii")).hexdigest()
# Single isolated owner listener; the future external JWT/scopes ingress is
# intentionally NOT enabled or implicitly opened in this initial proof.
config = dedent("""\
config:
  adminAddr: 127.0.0.1:15078
gateways:
  native-owner:
    bindAddress: 172.30.143.10
    port: 8765
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
      - keyHash: sha256:__HASH__
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
""")
Path(sys.argv[1]).write_text(config.replace("__HASH__", keyhash), encoding="ascii")
print("owner_api_key_config=one_sha256_hash_no_plaintext; issuer_created=false")
PY
chmod 0755 "$tmp/config"
chmod 0644 "$tmp/config/config.yaml"
# Synthetic, public-only target list; not an app secret.
printf '%s\n' '{"schema":1,"targets":[{"name":"peer-chat","host":"http://127.0.0.1:18765/mcp"}]}' > "$tmp/config/approved-targets.json"
chmod 0644 "$tmp/config/approved-targets.json"

docker network create --driver bridge --internal \
  --subnet 172.30.143.0/24 "$network" >/dev/null
# No image rebuild or GHCR publishing; same pinned official Agentgateway binary.
# The product entrypoint is deliberately bypassed only in this CI sandbox,
# because its existing operator validator still requires owner RSA/JWKS.
docker create --name "$name" --network "$network" --ip 172.30.143.10 \
  --read-only --security-opt=no-new-privileges --cap-drop=ALL \
  --tmpfs /tmp:rw,nosuid,nodev,size=8m \
  --memory=176m --memory-swap=224m --pids-limit=64 \
  --cpus=0.50 --user 10001:10001 \
  -e ALFRED_PEER_TRUSTED_HOSTS=172.30.143.10:8765 \
  --mount "type=bind,src=$tmp/config,dst=/etc/agentgateway,readonly" \
  --entrypoint /bin/bash "$image" -c \
  'set -e; python -m alfred_gateway_core.serve & peer_pid=$!; \
   /usr/local/bin/agentgateway -f /etc/agentgateway/config.yaml & gateway_pid=$!; \
   wait -n "$peer_pid" "$gateway_pid"; exit 1' >/dev/null
docker start "$name" >/dev/null
test -z "$(docker port "$name")"
test "$(docker inspect --format '{{.Config.User}}' "$name")" = "10001:10001"
# Only nonsecret evidence from the temporary probe reaches logs.
for attempt in $(seq 1 65); do
  if docker exec "$name" python -c "import socket; s=socket.create_connection(('172.30.143.10',8765),timeout=.2);s.close()" >/dev/null 2>&1; then
    break
  fi
  if [ "$(docker inspect --format '{{.State.Running}}' "$name")" != "true" ]; then
    echo "owner-apikey:gateway_exited" >&2
    exit 1
  fi
  sleep .2
done
id_u="$(id -u)"
id_g="$(id -g)"
docker run --rm --network "$network" \
  --read-only --security-opt=no-new-privileges --cap-drop=ALL \
  --tmpfs /tmp:rw,nosuid,nodev,size=8m --user "$id_u:$id_g" \
  --mount "type=bind,src=$tmp/private,dst=/run/test-owner-auth,readonly" \
  --mount "type=bind,src=$PWD/experiments/native_owner_apikey/probe.py,dst=/probe.py,readonly" \
  --entrypoint python "$image" /probe.py
test -z "$(docker port "$name")"
peak="$(docker exec "$name" cat /sys/fs/cgroup/memory.peak)"
case "$peak" in *[!0-9]*|'') echo "owner-apikey:peak_unavailable" >&2; exit 1;; esac
test "$peak" -lt 184549376
echo "native_owner_api_key=verified; synthetic_only=true; ports_published=0; VPS_changed=false; owner_issuer=false"
