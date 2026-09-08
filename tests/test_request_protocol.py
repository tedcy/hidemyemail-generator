import asyncio
import json
import socket
import ssl
import unittest
from unittest.mock import AsyncMock, patch

from hidemyemail_generator.hidemyemail import HideMyEmail, _request_failure_reason


# Mirrors the iCloud web client request the values were captured from. Update
# this together with HideMyEmail when Apple ships a new web client.
CAPTURED_HEADERS = {
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Content-Type": "text/plain",
    "Accept": "*/*",
    "Sec-GPC": "1",
    "Origin": "https://www.icloud.com",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Referer": "https://www.icloud.com/",
    "Accept-Language": "en-US,en;q=0.7",
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Brave";v="150"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}


class FakeResponse:
    def __init__(self, outcome, status=None, headers=None):
        self.outcome = outcome
        self.status = status
        self.headers = headers or {}

    async def __aenter__(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self):
        return self.outcome


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def request(self, *_args, **_kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, tuple):
            return FakeResponse(*outcome)
        return FakeResponse(outcome)


class RequestProtocolTests(unittest.TestCase):
    def test_global_headers_match_captured_web_client(self):
        headers = HideMyEmail.browser_headers("global", "cookie=value")
        self.assertEqual({k: v for k, v in headers.items() if k != "Cookie"}, CAPTURED_HEADERS)
        self.assertEqual(headers["Cookie"], "cookie=value")

    def test_client_version_matches_captured_request(self):
        self.assertEqual(HideMyEmail.params["clientBuildNumber"], "2626Build17")
        self.assertEqual(HideMyEmail.params["clientMasteringNumber"], "2626Build17")

    def test_china_region_swaps_origin_and_locale(self):
        headers = HideMyEmail.browser_headers("china")
        self.assertEqual(headers["Origin"], "https://www.icloud.com.cn")
        self.assertEqual(headers["Referer"], "https://www.icloud.com.cn/")
        self.assertEqual(headers["Accept-Language"], "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7")

    def test_cookie_whitespace_is_stripped(self):
        self.assertEqual(HideMyEmail.browser_headers("global", "  a=b\n")["Cookie"], "a=b")

    def test_generate_sends_region_lang_code(self):
        seen = []

        async def fake_request(self, method, url, **kwargs):
            seen.append(kwargs.get("json"))
            return {"success": True, "result": {}}

        for region, expected in [("global", "en-us"), ("china", "zh-cn")]:
            with patch.object(HideMyEmail, "_request_json", fake_request):
                asyncio.run(HideMyEmail(region=region).generate_email())
            self.assertEqual(seen[-1], {"langCode": expected})

    def test_transient_request_retries_with_short_backoff(self):
        client = HideMyEmail()
        client.s = FakeSession(
            [asyncio.TimeoutError(), {"success": True, "result": {}}]
        )
        with patch(
            "hidemyemail_generator.hidemyemail.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            result = asyncio.run(client.list_email())
        self.assertTrue(result["success"])
        self.assertEqual(2, client.s.calls)
        sleep.assert_awaited_once_with(0.5)

    def test_exhausted_timeout_returns_only_fixed_reason(self):
        client = HideMyEmail()
        client.s = FakeSession([asyncio.TimeoutError(), asyncio.TimeoutError()])
        with patch(
            "hidemyemail_generator.hidemyemail.asyncio.sleep", new=AsyncMock()
        ):
            result = asyncio.run(client.list_email())
        self.assertEqual({"error": 1, "reason": "request_timeout"}, result)

    def test_non_object_response_is_classified_without_body(self):
        client = HideMyEmail()
        client.s = FakeSession([["unexpected"], ["still-unexpected"]])
        with patch(
            "hidemyemail_generator.hidemyemail.asyncio.sleep", new=AsyncMock()
        ):
            result = asyncio.run(client.list_email())
        self.assertEqual({"error": 1, "reason": "response_invalid_json"}, result)

    def test_invalid_response_keeps_only_fixed_transport_metadata(self):
        client = HideMyEmail()
        response = (["unexpected"], 403, {"Content-Type": "text/html; charset=utf-8"})
        client.s = FakeSession([response, response])
        with patch(
            "hidemyemail_generator.hidemyemail.asyncio.sleep", new=AsyncMock()
        ):
            result = asyncio.run(client.list_email())
        self.assertEqual("response_invalid_json", result["reason"])
        self.assertEqual(403, result["http_status"])
        self.assertEqual("edge_blocked", result["response_kind"])
        self.assertEqual("text/html", result["content_type"])
        self.assertNotIn("unexpected", json.dumps(result))

    def test_request_failure_classification_uses_fixed_enums(self):
        self.assertEqual(
            "request_dns_error", _request_failure_reason(socket.gaierror())
        )
        self.assertEqual("request_tls_error", _request_failure_reason(ssl.SSLError()))
        self.assertEqual(
            "response_invalid_json",
            _request_failure_reason(json.JSONDecodeError("bad", "x", 0)),
        )


if __name__ == "__main__":
    unittest.main()
