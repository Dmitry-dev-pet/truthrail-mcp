"""Pin real public Atlas data for five-tool MCP parity, never reach live VPS.

The test uses source from the exact public Git commit behind the deployed
Atlas image. It builds SQLite locally, opens it read-only and compares
the native Atlas MCPServer with the shared one-process dispatcher.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from shared_alfred_gateway.atlas_local import (
    make_embedded_server, local_query_backend,
)
from shared_alfred_gateway.server import ATLAS_TOOLS

CASES = (
    ("dataset_info", {}),
    ("search_rulers", {"text": "Сталин", "year": 1953, "limit": 3}),
    ("get_timeline", {"year": 1953, "limit": 40, "offset": 0}),
    ("compare_rulers", {"person_ids": ["person-e88112738c1054f5"]}),
    ("get_evidence", {"record_id": "power-p03"}),
)


def read_result(reply) -> dict:
    if getattr(reply, "is_error", False):
        raise AssertionError("unexpected_tool_error")
    value = getattr(reply, "structured_content", None)
    if value is None:
        texts = [
            part.text for part in reply.content
            if getattr(part, "type", "") == "text"
        ]
        if len(texts) != 1:
            raise AssertionError("tool_response_not_json")
        value = json.loads(texts[0])
    if isinstance(value, dict) and isinstance(value.get("result"), dict):
        value = value["result"]
    if type(value) is not dict:
        raise AssertionError("unexpected_response_type")
    return value


def signatures(listing) -> dict:
    result = {}
    for tool in listing.tools:
        dumped = tool.model_dump(by_alias=True)
        schema = dumped.get("inputSchema") or dumped.get("input_schema")
        if not isinstance(schema, dict):
            raise AssertionError("missing_input_schema")
        result[tool.name] = (
            set(schema.get("properties", {})),
            set(schema.get("required", [])),
        )
    return result


class PinnedAtlasRealSourceTests(unittest.TestCase):
    def test_real_public_source_five_tool_parity(self):
        root_text = os.environ.get("ALFRED_PINNED_ATLAS_ROOT", "")
        db_text = os.environ.get("ALFRED_PINNED_ATLAS_DB", "")
        self.assertTrue(root_text and db_text, "pinned public fixture required")
        root, db = Path(root_text), Path(db_text)
        self.assertTrue(root.is_dir() and db.is_file(), "missing pinned source")
        self.assertTrue((root / ".git").is_dir() and db.stat().st_size > 0)
        sys.path.insert(0, str(root))

        from mcp import Client
        from scripts.atlas_mcp import create_server
        from scripts.atlas_mcp_query import AtlasQuery

        original = create_server(
            sqlite_path=db, archigos_path="", http_read_only=True,
        )
        shared = make_embedded_server(AtlasQuery(db))

        async def check():
            async with Client(original, raise_exceptions=True) as direct:
                async with Client(shared, raise_exceptions=True) as routed:
                    native = signatures(await direct.list_tools())
                    exposed = signatures(await routed.list_tools())
                    self.assertEqual(set(native), set(ATLAS_TOOLS))
                    self.assertEqual(native, exposed, "real Atlas tool schema drift")
                    self.assertNotIn("archigos_returnees", exposed)
                    self.assertNotIn("youtube_transcript", exposed)
                    for name, args in CASES:
                        with self.subTest(tool=name):
                            before = read_result(await direct.call_tool(name, args))
                            after = read_result(await routed.call_tool(name, args))
                            self.assertTrue(after == before, "Atlas result parity drift")
                            if name == "dataset_info":
                                self.assertEqual(after["dataset"], "canonical-rulers-atlas-v1")
                                self.assertFalse(after["historical_truth_verified_by_ci"])
                                self.assertFalse(after["archigos_file_configured"])
                                self.assertFalse(after["archigos_data_loaded"])
                            elif name == "search_rulers":
                                self.assertGreater(after.get("count", 0), 0)
                            elif name == "get_timeline":
                                self.assertTrue(after["entries"])
                            elif name == "compare_rulers":
                                self.assertEqual(after["persons"][0]["person_id"],
                                                 "person-e88112738c1054f5")
                            elif name == "get_evidence":
                                self.assertTrue(after["found"])
                                self.assertIn("PC03", after["source_ids"])
                                self.assertFalse(after["historical_truth_verified_by_ci"])
                    try:
                        result = await routed.call_tool("arbitrary_shell", {})
                    except Exception:
                        denied = True
                    else:
                        denied = bool(getattr(result, "is_error", False))
                    self.assertTrue(denied, "unknown tool accepted")

        asyncio.run(check())

    def test_unknown_dispatch_and_unguarded_entrypoint_are_denied(self):
        class NoBackend:
            def __getattr__(self, _):
                raise AssertionError("unsafe operation reached backend")

        backend = local_query_backend(NoBackend())
        self.assertEqual(
            asyncio.run(backend("arbitrary_shell", {})),
            {"available": False, "reason": "unknown-atlas-tool"},
        )
        from shared_alfred_gateway.atlas_local import main
        with patch.dict(os.environ, {
            "ALFRED_SHARED_MCP_ENABLED": "0",
            "ALFRED_EMBEDDED_ATLAS_REVIEWED": "0",
        }), self.assertRaisesRegex(SystemExit, "embedded-atlas-not-authorized"):
            main()


if __name__ == "__main__":
    unittest.main()
