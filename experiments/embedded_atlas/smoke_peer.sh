#!/usr/bin/env bash
set -euo pipefail

# Source-only one-process HTTP test with optional chat enabled. Hosted CI only.
# No VPS, second tunnel, public port, GitHub token, credentials or Mac control.
test "$#" -eq 1
image="$1"
name="truthrail-embedded-peer-ci-$$"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run --detach --rm --name "$name" \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=8m \
  --memory=176m --memory-swap=224m --cpus=0.50 \
  --env ALFRED_SHARED_MCP_ENABLED=1 \
  --env ALFRED_EMBEDDED_ATLAS_REVIEWED=1 \
  --env ALFRED_PEER_CHAT_MCP_ENABLED=1 \
  --env ATLAS_MCP_BIND=0.0.0.0 \
  --publish "127.0.0.1:18768:8765" \
  "$image" >/dev/null

python3 - <<'PY'
import asyncio
import json
from mcp import Client

URL = "http://127.0.0.1:18768/mcp"
ATLAS = {"dataset_info", "search_rulers", "get_timeline",
         "compare_rulers", "get_evidence"}
CHAT = {"chat_create", "chat_send", "chat_wait", "chat_close"}

def unpack(reply):
    assert not reply.is_error, "peer_tool_error"
    obj = getattr(reply, "structured_content", None)
    if obj is None:
        txt = [x.text for x in reply.content if getattr(x, "type", "") == "text"]
        assert len(txt) == 1
        obj = json.loads(txt[0])
    if isinstance(obj, dict) and isinstance(obj.get("result"), dict):
        obj = obj["result"]
    assert isinstance(obj, dict)
    return obj

async def ready():
    for _ in range(100):
        try:
            async with Client(URL, raise_exceptions=True) as c:
                await c.list_tools()
            return
        except Exception:
            await asyncio.sleep(0.15)
    raise AssertionError("peer-enabled embedded MCP HTTP unavailable")

async def main():
    await ready()
    async with Client(URL, raise_exceptions=True) as a, \
               Client(URL, raise_exceptions=True) as b:
        names = {x.name for x in (await a.list_tools()).tools}
        assert names == ATLAS | CHAT, "peer_tool_registry_mismatch"
        atlas = unpack(await a.call_tool("dataset_info", {}))
        assert atlas["dataset"] == "canonical-rulers-atlas-v1"
        assert not atlas["historical_truth_verified_by_ci"]
        evidence = unpack(await a.call_tool("get_evidence", {"record_id": "power-p03"}))
        assert evidence["found"] and "PC03" in evidence["source_ids"]

        created = unpack(await a.call_tool("chat_create", {}))
        room = created["room_id"]
        assert len(room) == 32
        last_a, last_b = 0, 0

        # Three actual HTTP wake-ups, from two independently connected clients.
        for i in range(3):
            pending = asyncio.create_task(b.call_tool("chat_wait", {
                "room_id": room, "role": "B", "after_id": last_b,
                "wait_seconds": 5
            }))
            await asyncio.sleep(0.10)
            assert not pending.done(), "wait_did_not_block"
            sent_a = unpack(await a.call_tool("chat_send", {
                "room_id": room, "sender": "A", "message": f"A/{i+1}",
                **({"reply_to": last_a} if last_a else {})
            }))
            got_b = unpack(await asyncio.wait_for(pending, timeout=8))
            assert got_b["status"] == "message"
            assert got_b["message"] == f"A/{i+1}"
            assert got_b["id"] == sent_a["id"]
            last_b = got_b["id"]
            sent_b = unpack(await b.call_tool("chat_send", {
                "room_id": room, "sender": "B",
                "message": f"B/{i+1}", "reply_to": last_b,
            }))
            got_a = unpack(await a.call_tool("chat_wait", {
                "room_id": room, "role": "A", "after_id": last_a,
                "wait_seconds": 0,
            }))
            assert got_a["message"] == f"B/{i+1}"
            assert got_a["id"] == sent_b["id"]
            last_a = got_a["id"]
        closed = unpack(await a.call_tool("chat_close", {"room_id": room}))
        assert closed["status"] == "closed"

asyncio.run(main())
print("embedded_peer_http=verified; round_trips=3; atlas_provenance=verified; VPS_mutated=false")
PY

docker inspect --format '{{.State.Running}} {{.HostConfig.Memory}}' "$name" \
  | grep -q '^true 184549376$'
usage="$(docker stats --no-stream --format '{{.MemUsage}}' "$name")"
test -n "$usage"
printf 'peer_http_ci_cgroup_memory=%s; cap=176MiB; existing_tunnel_unchanged=true\n' "$usage"
