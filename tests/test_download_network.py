"""Offline mocks only. These checks never contact a server."""
import importlib.util
import io
import json
from pathlib import Path
import socket
import ssl
import threading
import types
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("download_network_tested", ROOT / "scripts/download_network.py")
network = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(network)
URL = "https://example.invalid/data.csv?sst=example"


class Response(io.BytesIO):
    def __init__(self, body=b"sample", headers=None, url=URL):
        super().__init__(body)
        self.headers = headers or {}
        self.url = url

    def geturl(self):
        return self.url


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.stop = threading.Event()

    def fetch_with(self, response, **kwargs):
        opener = Mock()
        opener.open.return_value = response
        with patch.object(network, "tls_context", return_value=Mock()), \
             patch.object(network.urllib.request, "build_opener", return_value=opener):
            result = network.fetch_bytes(URL, 10, self.stop, **kwargs)
        return result, opener

    def test_default_tls_is_preserved_and_public_and_custom_ca_are_added(self):
        context = Mock()
        fake_certifi = types.SimpleNamespace(where=lambda: "/public-ca.pem")
        with patch.dict("os.environ", {"SCI_DSH_DATA_CA_BUNDLE": "/institution-ca.pem"}, clear=True), \
             patch.dict("sys.modules", {"certifi": fake_certifi}), \
             patch.object(network.ssl, "create_default_context", return_value=context) as create:
            self.assertIs(network.tls_context(), context)
        create.assert_called_once_with()
        self.assertEqual(context.load_verify_locations.call_args_list[0].kwargs, {"cafile": "/public-ca.pem"})
        self.assertEqual(context.load_verify_locations.call_args_list[1].kwargs, {"cafile": "/institution-ca.pem"})

    def test_bad_ca_configuration_has_safe_diagnostic(self):
        with patch.object(network.ssl, "create_default_context", side_effect=OSError("private path and credentials")):
            with self.assertRaises(network.NetworkConfigurationError) as error:
                network.tls_context()
        result = network.classify_error(error.exception)
        self.assertEqual(result["category"], "tls_config")
        self.assertFalse(result["retryable"])
        self.assertNotIn("private", json.dumps(result))

    def test_standard_proxy_discovery_is_used_when_no_override(self):
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://example.invalid:8080"}, clear=True), \
             patch.object(network.urllib.request, "ProxyHandler") as handler:
            network._proxy_handler()
        handler.assert_called_once_with()

    def test_data_proxy_is_scoped_to_this_opener(self):
        proxy = "http://user:private-password@127.0.0.1:7890"
        with patch.dict("os.environ", {"SCI_DSH_DATA_PROXY": proxy}, clear=True), \
             patch.object(network.urllib.request, "ProxyHandler") as handler:
            network._proxy_handler()
        handler.assert_called_once_with({"http": proxy, "https": proxy})

    def test_bad_proxy_does_not_leak_credentials(self):
        with patch.dict("os.environ", {"SCI_DSH_DATA_PROXY": "socks5://user:private-password@localhost:1080"}, clear=True):
            with self.assertRaises(network.NetworkConfigurationError) as error:
                network._proxy_handler()
        result = network.classify_error(error.exception)
        self.assertEqual(result["category"], "proxy_config")
        self.assertNotIn("private-password", str(error.exception) + json.dumps(result))

    def test_a_success_is_one_request(self):
        result, opener = self.fetch_with(Response(b"real-shaped-response"))
        self.assertEqual(result, b"real-shaped-response")
        opener.open.assert_called_once()
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 10)

    def test_https_downgrade_and_credential_redirect_are_rejected(self):
        handler = network.HTTPSOnlyRedirect()
        for target in ("http://example.invalid/data.csv", "https://user:secret@example.invalid/data.csv"):
            with self.subTest(target=target), self.assertRaises(network.UnsafeRedirectError):
                handler.redirect_request(urllib.request.Request(URL), None, 302, "Found", {}, target)

    def test_unexpected_final_http_url_is_rejected(self):
        with self.assertRaises(network.UnsafeRedirectError):
            self.fetch_with(Response(url="http://example.invalid/changed.csv"))

    def test_declared_and_streamed_size_limits(self):
        with self.assertRaises(network.DataResponseError):
            self.fetch_with(Response(headers={"Content-Length": str(network.MAX_RESPONSE_BYTES + 1)}))
        with patch.object(network, "MAX_RESPONSE_BYTES", 10):
            with self.assertRaises(network.DataResponseError):
                self.fetch_with(Response(b"12345678901"))
            result, _ = self.fetch_with(Response(b"1234567890"))
            self.assertEqual(result, b"1234567890")

    def test_cancellation_before_request_and_between_reads(self):
        self.stop.set()
        with patch.object(network.urllib.request, "build_opener") as build:
            with self.assertRaises(network.DownloadCancelled):
                network.fetch_bytes(URL, 10, self.stop)
            build.assert_not_called()
        self.stop.clear()
        stop = self.stop

        class CancellingResponse(Response):
            def read1(self, size=-1):
                stop.set()
                return b"partial"

        with self.assertRaises(network.DownloadCancelled):
            self.fetch_with(CancellingResponse())

    def test_expired_deadline_prevents_request(self):
        with patch.object(network.time, "monotonic", return_value=100), \
             patch.object(network.urllib.request, "build_opener") as build:
            with self.assertRaises(network.NetworkDeadlineExceeded):
                network.fetch_bytes(URL, 10, self.stop, deadline=99)
            build.assert_not_called()

    def test_request_timeout_is_capped_by_remaining_budget(self):
        with patch.object(network.time, "monotonic", return_value=100):
            result, opener = self.fetch_with(Response(), deadline=102)
        self.assertEqual(result, b"sample")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 2)

    def test_deadline_is_rechecked_after_a_read(self):
        clock = {"now": 100}

        class SlowResponse(Response):
            def read1(self, size=-1):
                clock["now"] = 103
                return b"partial"

        with patch.object(network.time, "monotonic", side_effect=lambda: clock["now"]):
            with self.assertRaises(network.NetworkDeadlineExceeded):
                self.fetch_with(SlowResponse(), deadline=102)

    def test_timeout_is_not_retried_in_transport(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError(socket.timeout("timed out"))
        with patch.object(network, "tls_context", return_value=Mock()), \
             patch.object(network.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(urllib.error.URLError):
                network.fetch_bytes(URL, 10, self.stop)
        opener.open.assert_called_once()

    def test_http_retry_categories_and_no_raw_url_leak(self):
        for status, retryable in ((400, False), (401, False), (403, False), (404, False),
                                  (407, False), (408, True), (429, True), (500, True), (503, True)):
            with self.subTest(status=status):
                error = urllib.error.HTTPError("https://user:private-password@example.invalid/", status,
                                               "private-password", {}, None)
                result = network.classify_error(error)
                self.assertEqual(result["retryable"], retryable)
                self.assertEqual(result["http_status"], status)
                self.assertNotIn("private-password", json.dumps(result))

    def test_tls_dns_proxy_and_schema_errors_are_distinct(self):
        errors = [
            (urllib.error.URLError(ssl.SSLCertVerificationError("private TLS details")), "tls", False),
            (urllib.error.URLError(socket.gaierror("private host")), "dns", True),
            (urllib.error.URLError(OSError("Tunnel connection failed: 407 private-password")), "proxy_auth", False),
            (urllib.error.URLError(ConnectionResetError("private host")), "connection", True),
            (network.DataResponseError("<html>private-password</html>"), "data_response", False),
        ]
        for error, category, retryable in errors:
            with self.subTest(category=category):
                result = network.classify_error(error)
                self.assertEqual(result["category"], category)
                self.assertEqual(result["retryable"], retryable)
                self.assertNotIn("private", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
