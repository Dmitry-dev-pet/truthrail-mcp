#!/usr/bin/env bash
set -euo pipefail

# Hosted-CI, disposable, read-only one-container MCP + real public Atlas data.
# No VPS, private tunnel, Cloudflare, Mac, SSH, credentials or live clients.
test "$#" -eq 1
image="$1"
name="truthrail-embedded-atlas-ci-$$"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run --detach --rm --name "$name" \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=8m \
  --memory=176m --memory-swap=224m --cpus=0.50 \
  --env ALFRED_SHARED_MCP_ENABLED=1 \
  --env ALFRED_EMBEDDED_ATLAS_REVIEWED=1 \
  --env ATLAS_MCP_BIND=0.0.0.0 \
  --publish "127.0.0.1:18767:8765" \
  "$image" >/dev/null

python3 - <<'PY'
import asyncio
import json
from mcp import Client

URL = "http://127.0.0.1:18767/mcp"
EXPECTED = {
    "dataset_info": (set(), set()),
    "search_rulers": ({"text", "year", "limit"}, set()),
    "get_timeline": ({"year", "state_id", "limit", "offset"}, {"year"}),
    "compare_rulers": ({"person_ids"}, {"person_ids"}),
    "get_evidence": ({"record_id"}, {"record_id"}),
}


async def await_service():
    for _ in range(100):
        try:
            async with Client(URL, raise_exceptions=True) as client:
                await client.list_tools()
            return
        except Exception:
            await asyncio.sleep(0.15)
    raise AssertionError("one_process_mcp_http_unavailable")


def data(reply):
    if reply.is_error:
        raise AssertionError("tool_error")
    value = getattr(reply, "structured_content", None)
    if value is None:
        blocks = [x.text for x in reply.content if getattr(x, "type", "") == "text"]
        assert len(blocks) == 1
        value = json.loads(blocks[0])
    if isinstance(value, dict) and isinstance(value.get("result"), dict):
        value = value["result"]
    assert isinstance(value, dict)
    return value


async def main():
    await await_service()
    async with Client(URL, raise_exceptions=True) as client:
        available = {x.name: x for x in (await client.list_tools()).tools}
        assert set(available) == set(EXPECTED), "wrong_tool_registry"
        for name, (props, req) in EXPECTED.items():
            model = available[name].model_dump(by_alias=True)
            schema = model.get("inputSchema") or model.get("input_schema")
            assert isinstance(schema, dict)
            assert set(schema.get("properties", {})) == props, "schema_drift"
            assert set(schema.get("required", [])) == req, "required_field_drift"
        info = data(await client.call_tool("dataset_info", {}))
        assert info["dataset"] == "canonical-rulers-atlas-v1"
        assert info["historical_truth_verified_by_ci"] is False
        assert info["archigos_file_configured"] is False
        found = data(await client.call_tool(
            "search_rulers", {"text": "Сталин", "year": 1953, "limit": 3}))
        assert found["count"] >= 1
        timeline = data(await client.call_tool(
            "get_timeline", {"year": 1953, "limit": 40, "offset": 0}))
        assert timeline["entries"] and timeline["year"] == 1953
        comparison = data(await client.call_tool(
            "compare_rulers", {"person_ids": ["person-e88112738c1054f5"]}))
        assert comparison["persons"][0]["person_id"] == "person-e88112738c1054f5"
        evidence = data(await client.call_tool(
            "get_evidence", {"record_id": "power-p03"}))
        assert evidence["found"] is True
        assert "PC03" in evidence["source_ids"]
        assert evidence["historical_truth_verified_by_ci"] is False
        try:
            reply = await client.call_tool("arbitrary_shell", {})
        except Exception:
            denied = True
        else:
            denied = reply.is_error
        assert denied, "unknown_tool_accepted"


asyncio.run(main())
print("embedded_atlas_http_five_tools=verified; synthetic=false; external_backend=false")
PY

docker inspect --format '{{.State.Running}} {{.HostConfig.Memory}}' "$name" \
  | grep -q '^true 184549376$'
usage="$(docker stats --no-stream --format '{{.MemUsage}}' "$name")"
test -n "$usage"
printf 'embedded_atlas_ci_cgroup_memory=%s; cap=176MiB; no_VPS_runtime_changes=true\n' "$usage"
