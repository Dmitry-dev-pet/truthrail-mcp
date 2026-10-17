"""The comparison benchmark must be genuinely local, finite and version pinned."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SDKComparisonSourceTests(unittest.TestCase):
    def test_no_live_backend_tools_or_secrets(self):
        code = (ROOT / "experiments/sdk_comparison/benchmark.py").read_text()
        for forbidden in (
            "subprocess", "requests.", "httpx.", "urllib.request",
            "docker", "ssh", "Cloudflare", "OpenAI", "AGY_OAUTH",
            "GITHUB_TOKEN", "SECRET_KEY", "socket.", "os.environ",
        ):
            self.assertNotIn(forbidden, code)
        self.assertIn("ITERATIONS = 10", code)
        self.assertIn("in_process", code)
        self.assertIn("unknown_tool_denied", code)

    def test_same_pinned_python_runner(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        block = workflow.split("  sdk-matched-synthetic:", 1)[1]
        self.assertIn("python-version: '3.14'", block)
        self.assertIn("'mcp==2.3.0'", block)
        self.assertIn("'fastmcp==4.1.0'", block)
        self.assertIn("mcp2/bin/python", block)
        self.assertIn("mcp-fastmcp4/bin/python", block)
        self.assertNotIn("self-hosted", block)
        self.assertNotIn("upload-artifact", block)


if __name__ == "__main__":
    unittest.main()
