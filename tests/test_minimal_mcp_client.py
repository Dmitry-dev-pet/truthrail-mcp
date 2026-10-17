import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("minimal_client", Path(__file__).parents[1] / "tools/mcp_client_smoke.py")
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)


class ClientTests(unittest.TestCase):
    def test_endpoint_restrictions(self):
        self.assertEqual(client.endpoint_url("https://mcp.example.invalid/mcp"), "https://mcp.example.invalid/mcp")
        for value in ("http://mcp.example.invalid/mcp", "https://mcp.example.invalid/mcp?key=x",
                      "https://user@mcp.example.invalid/mcp", "https://mcp.example.invalid:8080/mcp"):
            with self.assertRaises(client.ProbeError):
                client.endpoint_url(value)

    def test_minimal_client_walks_protocol_and_checks_source(self):
        calls = []
        token = "alf_" + "a" * 43  # synthetic fixture

        def fake_post(endpoint, supplied, message):
            calls.append(message["method"])
            if supplied != token:
                return 401, None
            if message["method"] == "notifications/initialized":
                return 202, None
            method = message["method"]
            if method == "initialize":
                result = {"protocolVersion": client.PROTOCOL}
            elif method == "tools/list":
                result = {"tools": [{"name": name} for name in client.TOOLS]}
            else:
                data = ({"dataset": "canonical-rulers-atlas-v1", "canonical_persons": 1}
                        if message["params"]["name"] == "dataset_info" else
                        {"found": True, "record_id": "power-p03", "source_ids": ["PC03"]})
                result = {"content": [{"type": "text", "text": json.dumps(data)}]}
            return 200, {"jsonrpc": "2.0", "id": message["id"], "result": result}

        with patch.object(client, "post", fake_post):
            result = client.smoke("https://mcp.example.invalid/mcp", token)
        self.assertTrue(result["sourced_evidence"])
        self.assertFalse(result["revocation_verified"])
        self.assertEqual(calls, ["tools/list", "tools/list", "initialize", "notifications/initialized", "tools/list", "tools/call", "tools/call"])

    def test_unauthenticated_success_is_failure(self):
        with patch.object(client, "post", return_value=(200, {})):
            with self.assertRaisesRegex(client.ProbeError, "not-denied"):
                client.smoke("https://mcp.example.invalid/mcp", "alf_" + "a" * 43)

    def test_redirects_are_not_followed(self):
        self.assertIsNone(client.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid"))

    def test_sources_and_tool_errors_are_not_silently_accepted(self):
        for result in ({"isError": True}, {"content": []}, {"content": [{"type": "text", "text": "invalid"}]}):
            with self.assertRaises(client.ProbeError):
                client.tool_data(result)


if __name__ == "__main__":
    unittest.main()
