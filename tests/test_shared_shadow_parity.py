"""In-memory shadow parity tests: only fixed endpoints, never production calls."""
from __future__ import annotations

import asyncio
import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from shared_alfred_gateway import shadow_parity as audit

EXPECTED = {
    "dataset_info": {},
    "search_rulers": {"text": "Сталин", "year": 1953, "limit": 3},
    "get_timeline": {"year": 1953, "limit": 3, "offset": 0},
    "compare_rulers": {"person_ids": ["person-e88112738c1054f5"]},
    "get_evidence": {"record_id": "power-p03"},
}

class FakeTool:
    def __init__(self, name):
        self.name = name
    def model_dump(self, by_alias):
        return {"name": self.name, "inputSchema": {
            "type": "object", "properties": {
                name: {} for name in EXPECTED[self.name]
            }, "required": []
        }}

class FakeClient:
    def __init__(self, target, *, mismatch=None):
        self.target = target
        self.mismatch = mismatch
        self.calls = []
    async def __aenter__(self):
        return self
    async def __aexit__(self, *unused):
        return False
    async def list_tools(self):
        return SimpleNamespace(tools=[FakeTool(name) for name in audit.ATLAS_TOOLS])
    async def call_tool(self, name, args):
        self.calls.append((name, args))
        reply = {"operation": name, "arguments": args}
        if name == "get_evidence":
            reply = {"found": True, "source_ids": ["PC03"], "sources": [{"id": "PC03"}]}
        if self.mismatch == name and self.target == audit.SHADOW_ORIGIN:
            reply["missing_provenance"] = True
        return SimpleNamespace(is_error=False, structured_content=reply)

class ShadowParityTests(unittest.TestCase):
    def test_canonical_exact_vectors(self):
        self.assertEqual(dict(audit.VECTORS), EXPECTED)
        self.assertEqual(tuple(EXPECTED), audit.ATLAS_TOOLS)
        self.assertTrue(audit.SHADOW_ORIGIN.startswith("http://127.0.0.1:"))
        self.assertNotIn("https://", audit.SHADOW_ORIGIN)

    def test_structured_and_legacy_text_response_handling(self):
        d = {"found": True, "source_ids": ["PC03"]}
        parsed = audit.unwrap_tool_response(SimpleNamespace(is_error=False, structured_content=d))
        self.assertEqual(parsed, d)
        text = SimpleNamespace(type="text", text=json.dumps(d))
        parsed = audit.unwrap_tool_response(
            SimpleNamespace(is_error=False, structured_content=None, content=[text])
        )
        self.assertEqual(parsed, d)
        with self.assertRaises(ValueError):
            audit.unwrap_tool_response(SimpleNamespace(is_error=True, content=[text]))
        with self.assertRaises(ValueError):
            audit.unwrap_tool_response(SimpleNamespace(
                is_error=False, structured_content={"large": "x" * (audit.MAX_ATLAS_RESPONSE + 1)}
            ))

    def test_parity_success_and_fails_on_missing_evidence(self):
        atlas, shadow = FakeClient("http://app:8765/mcp"), FakeClient(audit.SHADOW_ORIGIN)
        clients = iter([atlas, shadow])
        with patch("mcp.Client", side_effect=lambda *args, **kw: next(clients)):
            result = asyncio.run(audit.verify("http://app:8765/mcp"))
        self.assertEqual(result, (True, "five_real_atlas_calls_equal"))
        self.assertEqual(atlas.calls, list(audit.VECTORS))
        self.assertEqual(shadow.calls, list(audit.VECTORS))

        clients = iter([
            FakeClient("http://app:8765/mcp"),
            FakeClient(audit.SHADOW_ORIGIN, mismatch="get_evidence"),
        ])
        with patch("mcp.Client", side_effect=lambda *args, **kw: next(clients)):
            result = asyncio.run(audit.verify("http://app:8765/mcp"))
        self.assertEqual(result, (False, "response_parity_mismatch_get_evidence"))

    def test_never_connects_to_unapproved_origins(self):
        with patch("mcp.Client", side_effect=AssertionError("unexpected client")):
            result = asyncio.run(audit.verify("https://evil.example/mcp"))
        self.assertEqual(result, (False, "blocked_atlas_origin"))

    def test_runtime_errors_never_leak_token_or_url(self):
        def denied(*args, **kwargs):
            raise ValueError("token=extremely-private https://service.internal/secrets")
        with patch("mcp.Client", side_effect=denied):
            result = asyncio.run(audit.verify("http://app:8765/mcp"))
        self.assertEqual(result, (False, "private_mcp_transport_unavailable"))
        self.assertNotIn("extremely-private", repr(result))
        self.assertNotIn("service.internal", repr(result))

    def test_cli_is_disabled_by_default(self):
        with patch.dict(audit.os.environ, {}, clear=True):
            with redirect_stdout(io.StringIO()) as capture:
                self.assertEqual(audit.main(), 2)
        self.assertEqual(capture.getvalue().strip(), "shared_mcp_shadow_parity: blocked_disabled")

if __name__ == "__main__":
    unittest.main()
