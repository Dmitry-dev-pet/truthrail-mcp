"""Exact Host-list and production image source-invariant tests."""
import os
import unittest
from unittest.mock import patch

from alfred_gateway_core.transport_security import trusted_host_headers, transport_security
from alfred_gateway_core.peer_backend import mailbox, server


class ProductionSecurityTests(unittest.TestCase):
    def test_fails_without_reviewed_allowed_hosts(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                transport_security()

    def test_exact_rfc1918_nics_and_dns_rebinding_still_enabled(self):
        allowed = "172.30.143.10:8765,172.30.144.10:8766"
        with patch.dict(os.environ, {"ALFRED_PEER_TRUSTED_HOSTS": allowed}):
            settings = transport_security()
        self.assertTrue(settings.enable_dns_rebinding_protection)
        self.assertEqual(settings.allowed_hosts, [
            "127.0.0.1:18765",
            "172.30.143.10:8765",
            "172.30.144.10:8766",
        ])
        self.assertEqual(settings.allowed_origins, [
            "http://127.0.0.1:18765",
            "http://172.30.143.10:8765",
            "http://172.30.144.10:8766",
        ])

    def test_forbids_unreviewed_domains_wildcards_public_ips(self):
        unsafe = [
            "", "0.0.0.0:8765", "*", "*:8765", "127.0.0.1:8765",
            "localhost:8765", "8.8.8.8:8765", "172.30.143.10:*",
            "172.30.143.10:9876", "172.30.143.10:8765,172.30.143.10:8765",
            "1.1.1.1:8766", "172.30.143.10:8765,8.8.8.8:8766",
            "172.30.143.10:8765,172.30.144.10:8766,10.0.0.3:8765",
            "172.30.143.10:8765/../../",
        ]
        for raw in unsafe:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                trusted_host_headers(raw)

    def test_backend_does_not_register_test_issuer_or_admin_tools(self):
        from inspect import iscoroutinefunction
        names = {tool.name for tool in server._tool_manager.list_tools()}
        self.assertEqual(names, {
            "chat_create", "chat_join", "chat_send",
            "chat_wait", "chat_status", "chat_close",
        })
        self.assertTrue(iscoroutinefunction(server._tool_manager.get_tool("chat_wait").fn))
        self.assertFalse(hasattr(mailbox, "exec"))
        self.assertFalse(hasattr(mailbox, "issue_token"))


if __name__ == "__main__":
    unittest.main()
