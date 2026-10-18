#!/usr/bin/env bash
# Unauthenticated test config on HOSTED CI LOOPBACK ONLY; never on VPS.
set -euo pipefail
test "$#" -eq 1
image="$1"
name="alfred-peer-only-ci-$$"
tmp="$(mktemp -d)"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$tmp"; }
trap cleanup EXIT
cat > "$tmp/config.yaml" <<'YAML'
# CI ONLY, NO AUTH. Never deploy this configuration.
config:
  adminAddr: 127.0.0.1:15078
mcp:
  port: 8765
  statefulMode: stateless
  targets:
  - name: peer-chat
    mcp:
      host: http://127.0.0.1:18765/mcp
YAML
chmod 0444 "$tmp/config.yaml"

# Safe by default: image will not start without an operator-provided config.
set +e
docker run --rm --read-only --security-opt=no-new-privileges --cap-drop=ALL \
  --tmpfs /tmp:rw,nosuid,nodev,size=8m \
  --memory=176m --memory-swap=224m "$image" >/dev/null 2>&1
no_config_status=$?
set -e
test "$no_config_status" -eq 42

docker run --detach --rm --name "$name" \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=8m \
  --memory=176m --memory-swap=224m --cpus=0.50 --pids-limit=64 \
  --mount "type=bind,src=$tmp/config.yaml,dst=/etc/agentgateway/config.yaml,readonly" \
  --publish "127.0.0.1:18768:8765" "$image" >/dev/null

python - <<'PY'
import asyncio
import json
from mcp import Client
URL = "http://127.0.0.1:18768/mcp"
TOOLS = {"chat_create", "chat_join", "chat_send",
         "chat_wait", "chat_status", "chat_close"}
def unpack(reply):
    assert not reply.is_error
    item = getattr(reply, "structured_content", None)
    if item is None:
        parts = [x.text for x in reply.content if getattr(x, "type", "") == "text"]
        assert len(parts) == 1
        item = json.loads(parts[0])
    if isinstance(item, dict) and isinstance(item.get("result"), dict):
        item = item["result"]
    assert isinstance(item, dict)
    return item
def match(names, suffix):
    found = [n for n in names if n == suffix or n.endswith("_" + suffix)]
    assert len(found) == 1
    return found[0]
async def run():
    for _ in range(70):
        try:
            async with Client(URL, raise_exceptions=True) as client:
                await client.list_tools()
            break
        except Exception:
            await asyncio.sleep(.15)
    else: raise AssertionError("packed_gateway_unavailable")
    async with Client(URL, raise_exceptions=True) as a, \
               Client(URL, raise_exceptions=True) as b:
        names = [t.name for t in (await a.list_tools()).tools]
        assert len(names) == 6 and all(match(names, t) for t in TOOLS)
        assert not any("atlas" in n for n in names)
        created = unpack(await a.call_tool(match(names, "chat_create"), {}))
        joined = unpack(await b.call_tool(match(names, "chat_join"), {
            "room_id": created["room_id"], "invitation": created["b_invite"]
        }))
        assert joined["role"] == "B"
        pending = asyncio.create_task(b.call_tool(match(names, "chat_wait"), {
            "room_id": created["room_id"], "ticket": joined["b_ticket"],
            "after_id": 0, "wait_seconds": 3
        }))
        await asyncio.sleep(.15)
        assert not pending.done()
        sent = unpack(await a.call_tool(match(names, "chat_send"), {
            "room_id": created["room_id"], "ticket": created["a_ticket"],
            "message": "one-image-A"
        }))
        received = unpack(await asyncio.wait_for(pending, timeout=6))
        assert received["id"] == sent["id"] and received["from"] == "A"
        assert received["message"] == "one-image-A"
        closed = unpack(await a.call_tool(match(names, "chat_close"), {
            "room_id": created["room_id"], "ticket": created["a_ticket"]
        }))
        assert closed["status"] == "closed"
asyncio.run(run())
print("one_image_mcp_http=verified; no_atlas=true; auth_fixture_ci_only=true")
PY

test "$(docker inspect --format '{{.Config.User}}' "$name")" = "10001:10001"
docker inspect --format '{{.State.Running}} {{.HostConfig.Memory}} {{.HostConfig.ReadonlyRootfs}}' "$name" \
  | grep -q '^true 184549376 true$'
stats="$(docker stats --no-stream --format '{{.MemUsage}}' "$name")"
test -n "$stats"
printf 'one_image_ci_cgroup_memory=%s; cap=176MiB; VPS_mutated=false\n' "$stats"
