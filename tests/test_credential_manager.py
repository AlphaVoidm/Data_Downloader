"""Tests for the central credential manager (naming drift, masking, format)."""
import unittest

import credential_manager as cm


class CredentialManagerTest(unittest.TestCase):
    def test_get_credential_from_explicit_by_source_id(self):
        self.assertEqual(
            cm.get_credential("ember", {"ember": "secret-1234567890"}),
            "secret-1234567890",
        )

    def test_get_credential_from_explicit_by_env_name(self):
        self.assertEqual(
            cm.get_credential("ember", {"EMBER_API_KEY": "k-abcdefghijklmnop"}),
            "k-abcdefghijklmnop",
        )

    def test_entsoe_alias_env_name(self):
        # ENTSOE_API_KEY is a common misnomer; must still resolve the token.
        self.assertEqual(
            cm.get_credential("entsoe", {"ENTSOE_API_KEY": "tok-abcdefghijklmnop"}),
            "tok-abcdefghijklmnop",
        )

    def test_cds_alias_env_name(self):
        self.assertEqual(
            cm.get_credential("era5", {"CDSAPI_KEY": "uid:abcdefghijklmnopqrstuvwxyz1234"}),
            "uid:abcdefghijklmnopqrstuvwxyz1234",
        )

    def test_masked_never_reveals_full_value(self):
        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        masked = cm.masked("ember", {"ember": secret})
        self.assertNotIn(secret, masked)
        self.assertIn("…", masked)
        self.assertEqual(masked, "sk-a…7890")

    def test_format_ok_short_value_fails(self):
        ok, note = cm.format_ok("ember", {"ember": "short"})
        self.assertFalse(ok)
        self.assertIn("shorter", note)

    def test_format_ok_whitespace_fails(self):
        ok, _ = cm.format_ok("ember", {"ember": "abcd efgh ijkl mnop"})
        self.assertFalse(ok)

    def test_format_ok_plausible_value(self):
        ok, _ = cm.format_ok("ember", {"ember": "abcdefghijklmnopqrstuvwxyz"})
        self.assertTrue(ok)

    def test_load_credentials_merges_explicit_over_env(self):
        import os
        with unittest.mock.patch.dict(os.environ, {"EMBER_API_KEY": "from-env"}):
            creds = cm.load_credentials({"ember": "from-explicit"})
        self.assertEqual(creds.get("EMBER_API_KEY"), "from-explicit")


if __name__ == "__main__":
    unittest.main()
