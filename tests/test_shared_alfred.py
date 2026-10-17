"""Source-only and real MCP v2 client tests for optional Alfred shared ingress.

No tunnel-client, VPS SSH, Mac backhaul, YouTube network, GitHub PAT or Atlas
database is used. The existing R0Wi GitHub gateway is never started/changed.
"""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from shared_alfred_gateway import server as g


class GatewayPureTests(unittest.TestCase):
    def test_accepted_youtube_forms(self):
        id_ = "NwNZF5Ljzd8"
        for v in [
            id_, "https://youtu.be/" + id_ + "?share=test",
            "https://www.youtube.com/watch?v=" + id_ + "&t=5",
            "https://youtube.com/shorts/" + id_,
            "https://www.youtube-nocookie.com/embed/" + id_,
        ]:
            with self.subTest(v=v):
                self.assertEqual(g.video_id_for(v), id_)

    def test_unsafe_input_never_reaches_backend(self):
        id_ = "NwNZF5Ljzd8"
        for v in [
            "", None, 2, "http://youtube.com/watch?v=" + id_,
            "https://youtube.com.evil.invalid/watch?v=" + id_,
            "https://www.youtube.com@evil.invalid/watch?v=" + id_,
            "https://www.youtube.com:8765/watch?v=" + id_,
            "https://www.youtube.com/watch?v=" + id_ + "&v=abcdefghijk",
            "https://youtube.com/redirect?u=https://evil.invalid",
            "https://example.com/watch?v=" + id_,
            "x" * 513,
        ]:
            with self.subTest(v=v):
                with self.assertRaises(ValueError):
                    g.video_id_for(v)

    def test_already_active_github_policy_is_not_imported(self):
        source = __import__("pathlib").Path(g.__file__).read_text(encoding="utf-8")
        self.assertNotIn("GITHUB_INFRA_TOKEN", source)
        self.assertNotIn("actions_run_trigger", source)
        self.assertNotIn("subprocess.", source)
        self.assertNotIn("eval(", source)

    def test_private_upstream_endpoint_must_be_exactly_allowlisted(self):
        for bad in [
            "http://evil.invalid/mcp", "http://localhost:443/mcp",
            "https://app:8765/mcp", "http://app:8765/redirect",
            "http://app:8765@evil.invalid/mcp",
        ]:
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "atlas-origin-not-allowlisted"):
                    g.configure_backends(atlas_url=bad, caption_url=None,
                                         caption_enabled=False)
        with self.assertRaisesRegex(ValueError, "caption-origin-without-authorization"):
            g.configure_backends(atlas_url="http://app:8765/mcp",
                                 caption_url="http://caption-backhaul:8619/mcp",
                                 caption_enabled=False)
        with self.assertRaisesRegex(ValueError, "caption-private-backhaul-not-allowlisted"):
            with patch.dict(os.environ, {"ALFRED_MAC_BACKHAUL_REVIEWED": "1"}):
                g.configure_backends(atlas_url="http://app:8765/mcp",
                                     caption_url="https://attacker.invalid/mcp",
                                     caption_enabled=True)
        with self.assertRaisesRegex(ValueError, "caption-private-backhaul-not-reviewed"):
            with patch.dict(os.environ, {"ALFRED_MAC_BACKHAUL_REVIEWED": "0"}):
                g.configure_backends(atlas_url="http://app:8765/mcp",
                                     caption_url="http://caption-backhaul:8619/mcp",
                                     caption_enabled=True)


class GatewayMCPClientTests(unittest.TestCase):
    def test_actual_mcp_tools_list_and_opaque_upstream_calls(self):
        from mcp import Client

        seen: list[tuple[str, dict]] = []

        async def atlas_backend(name, args):
            seen.append((name, args))
            return {"source": "atlas-opaque", "tool": name, "args": args}

        async def captions_backend(name, args):
            seen.append((name, args))
            return {
                "available": True,
                "source": "youtube-public-captions",
                "video_id": "NwNZF5Ljzd8",
                "video_url": "https://www.youtube.com/watch?v=NwNZF5Ljzd8",
                "language": "ru",
                "auto_generated": True,
                "total_segments": 3567,
                "cursor": args["cursor"],
                "next_cursor": args["cursor"] + 2,
                "partial_capture": True,
                "untrusted_source_text": True,
                "text": "[00:00:00] sample caption\n",
            }

        async def exercise():
            shared = g.make_server(_atlas_backend=atlas_backend)
            async with Client(shared, raise_exceptions=True) as client:
                tools = await client.list_tools()
                self.assertEqual(
                    {x.name for x in tools.tools}, set(g.ATLAS_TOOLS))
                self.assertNotIn("youtube_transcript", {x.name for x in tools.tools})
                self.assertNotIn("archigos_returnees", {x.name for x in tools.tools})
                self.assertNotIn("news_digest", {x.name for x in tools.tools})
                self.assertNotIn("issue_write", {x.name for x in tools.tools})
                for tool, args in [
                    ("dataset_info", {}),
                    ("search_rulers", {"text": "Ленин", "limit": 3}),
                    ("get_timeline", {"year": 1953}),
                    ("compare_rulers", {"person_ids": ["person-p03"]}),
                    ("get_evidence", {"record_id": "tenure-p03"}),
                ]:
                    reply = await client.call_tool(tool, args)
                    self.assertFalse(reply.is_error)
                    self.assertIn('"source"', str(reply))
            self.assertEqual([x[0] for x in seen], list(g.ATLAS_TOOLS))
            seen.clear()

            with patch.dict(os.environ, {"ALFRED_MAC_BACKHAUL_REVIEWED": "1"}):
                enabled = g.make_server(
                    _atlas_backend=atlas_backend,
                    _caption_backend=captions_backend,
                    caption_url="http://caption-backhaul:8619/mcp",
                    captions_enabled=True,
                )
            async with Client(enabled, raise_exceptions=True) as client:
                tools = await client.list_tools()
                self.assertEqual({x.name for x in tools.tools},
                                 set(g.ATLAS_TOOLS) | {"youtube_transcript"})
                denied = await client.call_tool(
                    "youtube_transcript",
                    {"video_url": "https://evil.invalid/watch?v=NwNZF5Ljzd8"})
                self.assertFalse(denied.is_error)
                self.assertIn("invalid-video-url", str(denied))
                size_denied = await client.call_tool(
                    "youtube_transcript",
                    {"video_url": "NwNZF5Ljzd8", "max_chars": 16001})
                self.assertFalse(size_denied.is_error)
                self.assertIn("invalid-caption-page-limit", str(size_denied))
                self.assertEqual(seen, [])

                ok = await client.call_tool("youtube_transcript", {
                    "video_url": "https://youtu.be/NwNZF5Ljzd8",
                    "language": "ru", "cursor": 17, "max_chars": 256,
                })
                self.assertFalse(ok.is_error)
                self.assertIn("sample caption", str(ok))
                self.assertIn("next_cursor", str(ok))
                # MCP 2.3 may return dict tools as JSON text blocks rather
                # than populating structured_content. Both are legitimate.
                self.assertIn("untrusted_source_text", str(ok))
                self.assertEqual(seen[0][0], "youtube_transcript")
                self.assertEqual(
                    seen[0][1]["video_url"],
                    "https://www.youtube.com/watch?v=NwNZF5Ljzd8",
                )
                self.assertEqual(seen[0][1]["cursor"], 17)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
