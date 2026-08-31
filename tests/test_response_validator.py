"""Tests for the response validator (never trust HTTP 200 == data)."""
import unittest

from response_validator import (
    INVALID_JSON,
    INVALID_XML,
    NO_RECORDS,
    PORTAL_HTML,
    SCHEMA_MISMATCH,
    AUTH_FAILED,
    RATE_LIMITED,
    EMPTY_RESPONSE,
    OK,
    validate_response,
)


class _FakeResponse:
    def __init__(self, status, content_type, body):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")


class ValidateResponseTest(unittest.TestCase):
    def test_portal_html_is_not_success(self):
        body = "<!DOCTYPE html><html><head><title>Transparency Platform</title></head><body>login</body></html>"
        resp = _FakeResponse(200, "text/html", body)
        result = validate_response(resp, expected_format="xml")
        self.assertEqual(result.status, PORTAL_HTML)
        self.assertFalse(result.ok)

    def test_auth_page_is_portal(self):
        body = "<html><head><title>Sign In</title></head></html>"
        resp = _FakeResponse(200, "text/html", body)
        result = validate_response(resp, expected_format="json")
        self.assertEqual(result.status, PORTAL_HTML)

    def test_valid_xml(self):
        body = '<?xml version="1.0"?><MarketDocument><TimeSeries><Period><Point><quantity>12</quantity></Point></Period></TimeSeries></MarketDocument>'
        resp = _FakeResponse(200, "application/xml", body)
        result = validate_response(resp, expected_format="xml", min_records=1)
        self.assertTrue(result.ok)
        self.assertEqual(result.record_count, 1)

    def test_invalid_xml(self):
        body = "<broken"
        resp = _FakeResponse(200, "application/xml", body)
        result = validate_response(resp, expected_format="xml")
        self.assertEqual(result.status, INVALID_XML)

    def test_valid_json_list(self):
        body = '[{"date":"2024-01-01","value":1.0},{"date":"2024-02-01","value":2.0}]'
        resp = _FakeResponse(200, "application/json", body)
        result = validate_response(resp, expected_format="json")
        self.assertTrue(result.ok)
        self.assertEqual(result.record_count, 2)

    def test_json_wrapped_data_key(self):
        body = '{"meta": {}, "data": [{"date":"2024-01-01","value":1.0}]}'
        resp = _FakeResponse(200, "application/json", body)
        result = validate_response(resp, expected_format="json", required_columns=["date"])
        self.assertTrue(result.ok)
        self.assertEqual(result.record_count, 1)

    def test_invalid_json(self):
        resp = _FakeResponse(200, "application/json", "{not json")
        result = validate_response(resp, expected_format="json")
        self.assertEqual(result.status, INVALID_JSON)

    def test_schema_mismatch(self):
        body = '[{"when":"2024-01-01","load":1.0}]'
        resp = _FakeResponse(200, "application/json", body)
        result = validate_response(resp, expected_format="json", required_columns=["date", "value"])
        self.assertEqual(result.status, SCHEMA_MISMATCH)

    def test_no_records(self):
        body = "[]"
        resp = _FakeResponse(200, "application/json", body)
        result = validate_response(resp, expected_format="json")
        self.assertEqual(result.status, NO_RECORDS)

    def test_empty_response(self):
        resp = _FakeResponse(200, "application/json", "")
        result = validate_response(resp, expected_format="json")
        self.assertEqual(result.status, EMPTY_RESPONSE)

    def test_auth_failed(self):
        resp = _FakeResponse(401, "text/html", "<html>unauthorized</html>")
        result = validate_response(resp, expected_format="json")
        self.assertEqual(result.status, AUTH_FAILED)

    def test_rate_limited(self):
        resp = _FakeResponse(429, "application/json", "{}")
        result = validate_response(resp, expected_format="json")
        self.assertEqual(result.status, RATE_LIMITED)

    def test_csv(self):
        body = "date,value\n2024-01-01,1.0\n"
        resp = _FakeResponse(200, "text/csv", body)
        result = validate_response(resp, expected_format="csv", required_columns=["date", "value"])
        self.assertTrue(result.ok)
        self.assertEqual(result.record_count, 1)

    def test_wrong_format_detected(self):
        body = '{"a": 1}'
        resp = _FakeResponse(200, "application/json", body)
        result = validate_response(resp, expected_format="xml")
        self.assertFalse(result.ok)
        self.assertNotEqual(result.status, OK)


if __name__ == "__main__":
    unittest.main()
