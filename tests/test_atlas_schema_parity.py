"""Source-only Atlas five-tool schema and evidence-parity regression.

This test exercises the actual MCP SDK's tools/list + tools/call over an
in-process candidate server. It never reaches the live Atlas, Mac, VPS,
OpenAI tunnel, private GitHub credentials, or an external model.
The expected signatures were compared with the currently native-connected
Historical Atlas tools on 2026-10-16. This is NOT live-backend parity.
"""
from __future__ import annotations

import asyncio
import unittest

from shared_alfred_gateway import server as shared


# Exactly the native Historical Atlas tool names and input parameter shapes.
# Optional inputs have Python defaults in the existing tool signatures.
EXPECTED = {
    "dataset_info": (set(), set()),
    "search_rulers": ({"text", "year", "limit"}, set()),
    "get_timeline": ({"year", "state_id", "limit", "offset"}, {"year"}),
    "compare_rulers": ({"person_ids"}, {"person_ids"}),
    "get_evidence": ({"record_id"}, {"record_id"}),
}


class AtlasSchemaParityTests(unittest.TestCase):
    def test_real_mcp_sdk_advertises_only_existing_five_atlas_schemas(self):
        from mcp import Client

        seen = []

        async def atlas_backend(operation: str, arguments: dict) -> dict:
            seen.append((operation, arguments))
            if operation == "get_evidence":
                # Distinct provenance fields must survive the proxy unchanged.
                return {
                    "found": True,
                    "record_id": arguments["record_id"],
                    "source_ids": ["PC03"],
                    "verification": "imported_not_rechecked",
                    "historical_truth_verified_by_ci": False,
                }
            return {"operation": operation, "arguments": arguments}

        async def exercise():
            server = shared.make_server(_atlas_backend=atlas_backend)
            async with Client(server, raise_exceptions=True) as client:
                listing = await client.list_tools()
                actual = {tool.name: tool for tool in listing.tools}
                self.assertEqual(set(actual), set(EXPECTED))
                for name, (properties, required) in EXPECTED.items():
                    with self.subTest(tool=name):
                        tool = actual[name]
                        # MCP SDK versions may use inputSchema or input_schema
                        # in Python; the wire schema is authoritative.
                        raw = tool.model_dump(by_alias=True)
                        schema = raw.get("inputSchema", raw.get("input_schema"))
                        self.assertIsInstance(schema, dict)
                        self.assertEqual(set(schema.get("properties", {})), properties)
                        self.assertEqual(set(schema.get("required", [])), required)

                source_reply = await client.call_tool(
                    "get_evidence", {"record_id": "tenure-p03"}
                )
                self.assertFalse(source_reply.is_error)
                output = str(source_reply)
                self.assertIn("PC03", output)
                self.assertIn("imported_not_rechecked", output)
                self.assertIn("historical_truth_verified_by_ci", output)
                self.assertEqual(
                    seen, [("get_evidence", {"record_id": "tenure-p03"})]
                )

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
