"""Tests for the production HTTP retry/backoff layer (connectors/base.py)."""
import unittest
from unittest import mock

import requests

from connectors import base as b


class _FakeResp:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class HttpRetryTest(unittest.TestCase):
    def setUp(self):
        self.client = b._HttpClient()
        # Neutralize sleeps for deterministic tests.
        patcher = mock.patch("connectors.base.time.sleep", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_session(self, side_effect):
        return mock.patch("connectors.base.requests.Session.get", side_effect=side_effect)

    def test_429_then_success_records_history(self):
        with self._patch_session(side_effect=[_FakeResp(429, {"Retry-After": "0"}),
                                               _FakeResp(200)]) as get:
            history = []
            resp = self.client.get("https://example.test", history=history)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([h["http_status"] for h in history], [429, 200])
        self.assertEqual(len(get.call_args_list), 2)

    def test_500s_exhaust_to_source_temporary_failure(self):
        with self._patch_session(side_effect=[_FakeResp(500), _FakeResp(502), _FakeResp(503)]):
            with self.assertRaises(b.ConnectorError) as ctx:
                self.client.get("https://example.test", retries=3)
        self.assertEqual(ctx.exception.status, b.SOURCE_TEMPORARY_FAILURE)
        self.assertEqual(len(ctx.exception.attempts), 3)

    def test_401_never_retried(self):
        with self._patch_session(side_effect=[_FakeResp(401)]) as get:
            resp = self.client.get("https://example.test", retries=5)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(get.call_count, 1)

    def test_404_never_retried(self):
        with self._patch_session(side_effect=[_FakeResp(404)]) as get:
            resp = self.client.get("https://example.test", retries=5)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(get.call_count, 1)

    def test_timeout_exhaustion_raises_timeout_with_attempts(self):
        def raise_timeout(*a, **k):
            raise requests.exceptions.Timeout("read timed out")
        with self._patch_session(side_effect=raise_timeout):
            with self.assertRaises(b.ConnectorError) as ctx:
                self.client.get("https://example.test", retries=3)
        self.assertEqual(ctx.exception.status, b.TIMEOUT)
        self.assertEqual([h["error"] for h in ctx.exception.attempts],
                         ["timeout", "timeout", "timeout"])

    def test_connection_reset_exhaustion_raises_network_error(self):
        def raise_reset(*a, **k):
            raise requests.exceptions.ConnectionError("Connection reset by peer")
        with self._patch_session(side_effect=raise_reset):
            with self.assertRaises(b.ConnectorError) as ctx:
                self.client.get("https://example.test", retries=2)
        self.assertEqual(ctx.exception.status, b.NETWORK_ERROR)

    def test_connector_error_message_defaults_to_status(self):
        err = b.ConnectorError(b.RATE_LIMITED)
        self.assertEqual(str(err), b.RATE_LIMITED)
        self.assertEqual(err.status, b.RATE_LIMITED)


if __name__ == "__main__":
    unittest.main()
