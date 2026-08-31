"""Tests for the source auth-check module (never prints credentials)."""
import unittest
from unittest import mock

from auth_check import (
    AUTH_FAILED,
    CONFIGURATION_ERROR,
    ENDPOINT_OK,
    AuthCheckResult,
    run_auth_checks,
    render_auth_check,
)


class AuthCheckTest(unittest.TestCase):
    def test_no_credentials_reports_configuration_error(self):
        results = run_auth_checks({})
        # every credential-protected source reports a config error, no network.
        self.assertTrue(results)
        for r in results:
            self.assertEqual(r.status, CONFIGURATION_ERROR)
            self.assertFalse(r.credential_supplied)

    def test_ember_auth_failed_when_key_rejected(self):
        with mock.patch.dict("auth_check._PROBES",
                             {"ember": lambda creds: {"status": AUTH_FAILED,
                                                       "message": "HTTP 401 — key rejected",
                                                       "http_status": 401}}):
            results = run_auth_checks({"ember": "not-a-real-key-1234567890"})
        ember = next(r for r in results if r.source_id == "ember")
        self.assertEqual(ember.status, AUTH_FAILED)
        self.assertEqual(ember.http_status, 401)
        self.assertTrue(ember.credential_supplied)
        self.assertFalse(ember.endpoint_available)

    def test_ember_endpoint_ok(self):
        with mock.patch.dict("auth_check._PROBES",
                             {"ember": lambda creds: {"status": ENDPOINT_OK, "message": "HTTP 200",
                                                       "http_status": 200, "endpoint_available": True}}):
            results = run_auth_checks({"ember": "real-looking-key-1234567890"})
        ember = next(r for r in results if r.source_id == "ember")
        self.assertEqual(ember.status, ENDPOINT_OK)
        self.assertTrue(ember.endpoint_available)

    def test_render_never_contains_plaintext_secret(self):
        secret = "supersecretvalue-abcdefghijklmnop"
        r = AuthCheckResult(source_id="ember", source_name="Ember", status=ENDPOINT_OK,
                            message="ok", credential_supplied=True, credential_format_ok=True,
                            masked_credential="supe…mnop", endpoint_available=True)
        text = render_auth_check([r])
        self.assertIn("Ember", text)
        self.assertIn("supe…mnop", text)
        self.assertNotIn(secret, text)

    def test_result_to_dict_has_expected_fields(self):
        r = AuthCheckResult(source_id="ember", source_name="Ember", status=ENDPOINT_OK, message="ok")
        d = r.to_dict()
        for k in ("source_id", "status", "credential_supplied", "credential_format_ok",
                  "masked_credential", "http_status", "endpoint_available"):
            self.assertIn(k, d)


if __name__ == "__main__":
    unittest.main()
