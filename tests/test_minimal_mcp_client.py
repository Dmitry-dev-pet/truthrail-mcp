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

    def test_httpx2_transport_auth_and_no_redirect_or_proxy(self):
        import httpx2

        requests, options = [], []
        responses = [
            httpx2.Response(401, headers={"Content-Type": "application/json"}, json={"error": "denied"}),
            httpx2.Response(307, headers={"Location": "https://other.example.invalid/mcp"}),
            httpx2.Response(200, headers={"Content-Type": "application/json"},
                            json={"jsonrpc": "2.0", "id": 2, "result": {}}),
        ]
        def respond(request):
            requests.append(request)
            return responses.pop(0)
        original_client = httpx2.Client
        def isolated_client(**kwargs):
            options.append(kwargs)
            return original_client(transport=httpx2.MockTransport(respond), **kwargs)
        endpoint = "https://mcp.example.invalid/mcp"
        with patch.object(httpx2, "Client", side_effect=isolated_client):
            self.assertEqual(client.post(endpoint, None, {"method": "tools/list"}), (401, None))
            self.assertEqual(client.post(endpoint, "synthetic-token", {"method": "initialize"}), (307, None))
            self.assertEqual(client.post(endpoint, "synthetic-token", {"method": "initialize"}),
                             (200, {"jsonrpc": "2.0", "id": 2, "result": {}}))
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(request.method == "POST" and str(request.url) == endpoint for request in requests))
        self.assertTrue(all(cfg == {"timeout": 15.0, "follow_redirects": False, "trust_env": False}
                            for cfg in options))
        self.assertNotIn("authorization", requests[0].headers)
        self.assertEqual(requests[1].headers.get("authorization"), "Bearer synthetic-token")
        self.assertEqual(requests[1].headers.get("mcp-protocol-version"), client.PROTOCOL)

    def test_httpx2_rejects_oversized_or_non_json(self):
        import httpx2

        original_client = httpx2.Client
        responses = [
            httpx2.Response(200, headers={"Content-Type": "application/json"}, content=b"x" * (client.MAX_RESPONSE + 1)),
            httpx2.Response(200, headers={"Content-Type": "text/html"}, content=b"not-json"),
        ]
        def isolated_client(**kwargs):
            return original_client(transport=httpx2.MockTransport(lambda request: responses.pop(0)), **kwargs)
        with patch.object(httpx2, "Client", side_effect=isolated_client):
            with self.assertRaisesRegex(client.ProbeError, "response-too-large"):
                client.post("https://mcp.example.invalid/mcp", None, {"method": "tools/list"})
            with self.assertRaisesRegex(client.ProbeError, "expected-json-response"):
                client.post("https://mcp.example.invalid/mcp", None, {"method": "tools/list"})

    def test_direct_post_rejects_non_https_or_query(self):
        for url in ("http://mcp.example.invalid/mcp", "https://mcp.example.invalid/mcp?debug=1"):
            with self.assertRaisesRegex(client.ProbeError, "invalid-endpoint"):
                client.post(url, None, {"method": "tools/list"})

    def test_sources_and_tool_errors_are_not_silently_accepted(self):
        for result in ({"isError": True}, {"content": []}, {"content": [{"type": "text", "text": "invalid"}]}):
            with self.assertRaises(client.ProbeError):
                client.tool_data(result)


if __name__ == "__main__":
    unittest.main()
