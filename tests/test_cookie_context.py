import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from hidemyemail_generator.main import (
    browser_cookie_header,
    browser_request_cookie_header,
    capturecookiecommand,
    is_hidemyemail_app_request,
    is_hidemyemail_list_request,
    load_cookie_context,
    resolve_maildomain_host,
    write_captured_cookie,
)


class CookieContextTests(unittest.TestCase):
    def test_empty_cookie_file_returns_empty_cookie(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cookie_file = Path(tmpdir) / "cookies.txt"
            cookie_file.write_text("", encoding="utf-8")

            cookie, maildomain_host = load_cookie_context(str(cookie_file), "global")

            self.assertEqual(cookie, "")
            self.assertEqual(maildomain_host, "")

    def test_matches_global_and_china_versioned_hme_documents(self):
        self.assertTrue(
            is_hidemyemail_app_request(
                "https://www.icloud.com/applications/hidemyemail/"
                "2626Build17/en-us/index.html?rootDomain=www",
                "global",
            )
        )
        self.assertTrue(
            is_hidemyemail_app_request(
                "https://www.icloud.com.cn/applications/hidemyemail/"
                "2626Build17/zh-cn/index.html?rootDomain=www",
                "china",
            )
        )
        self.assertTrue(
            is_hidemyemail_app_request(
                "https://www.icloud.com/applications/hidemyemail/"
                "current/en-us/index.html?rootDomain=www",
                "global",
            )
        )

    def test_rejects_assets_wrong_hosts_and_missing_root_domain(self):
        rejected = (
            "https://www.icloud.com/applications/hidemyemail/"
            "2626Build17/en-us/main.js?rootDomain=www",
            "https://example.com/applications/hidemyemail/"
            "2626Build17/en-us/index.html?rootDomain=www",
            "http://www.icloud.com/applications/hidemyemail/"
            "2626Build17/en-us/index.html?rootDomain=www",
            "https://www.icloud.com/applications/hidemyemail/"
            "2626Build17/en-us/index.html",
            "https://www.icloud.com/applications/hidemyemail/"
            "2626Build17/en-us/index.html?rootDomain=other",
        )
        for request_url in rejected:
            with self.subTest(request_url=request_url):
                self.assertFalse(
                    is_hidemyemail_app_request(request_url, "global")
                )

    def test_matches_only_region_scoped_hme_list_requests(self):
        self.assertTrue(
            is_hidemyemail_list_request(
                "https://p111-maildomainws.icloud.com/v2/hme/list?dsid=123",
                "global",
            )
        )
        self.assertTrue(
            is_hidemyemail_list_request(
                "https://p217-maildomainws.icloud.com.cn/v2/hme/list?dsid=123",
                "china",
            )
        )
        self.assertTrue(
            is_hidemyemail_list_request(
                "https://p224-maildomainws-china.icloud.com/v2/hme/list"
                "?clientBuildNumber=synthetic",
                "global",
            )
        )
        self.assertTrue(
            is_hidemyemail_list_request(
                "https://p224-maildomainws-china.icloud.com/v2/hme/list",
                "china",
            )
        )
        rejected = (
            "http://p111-maildomainws.icloud.com/v2/hme/list",
            "https://p111-maildomainws.icloud.com.cn/v2/hme/list",
            "https://p111-maildomainws.icloud.com.evil.test/v2/hme/list",
            "https://user@p111-maildomainws.icloud.com/v2/hme/list",
            "https://p111-maildomainws.icloud.com:444/v2/hme/list",
            "https://p111-maildomainws.icloud.com/v1/hme/list",
            "https://p111-maildomainws.icloud.com/v2/hme/list/",
            "https://p111-maildomainws.icloud.com/v2/hme/list#fragment",
            "https://p224-maildomainws-china.icloud.com.evil.test/v2/hme/list",
            "https://p224-maildomainws-china.evil.test/v2/hme/list",
        )
        for request_url in rejected:
            with self.subTest(request_url=request_url):
                self.assertFalse(
                    is_hidemyemail_list_request(request_url, "global")
                )

    def test_maildomain_resolution_prefers_capture_then_premium_service(self):
        account = {
            "userPartition": "68",
            "webservices": {
                "premiummailsettings": {
                    "url": "https://p123-maildomainws.icloud.com/v2/hme"
                }
            },
        }
        self.assertEqual(
            "p168-maildomainws.icloud.com",
            resolve_maildomain_host(
                account, "global", "p168-maildomainws.icloud.com"
            ),
        )
        self.assertEqual(
            "p123-maildomainws.icloud.com",
            resolve_maildomain_host(account, "global"),
        )
        account["webservices"]["premiummailsettings"]["url"] = (
            "https://p224-maildomainws-china.icloud.com/v2/hme"
        )
        self.assertEqual(
            "p224-maildomainws-china.icloud.com",
            resolve_maildomain_host(account, "global"),
        )
        account["webservices"]["premiummailsettings"]["url"] = (
            "https://p217-maildomainws.icloud.com.cn/v2/hme"
        )
        self.assertEqual(
            "p68-maildomainws.icloud.com",
            resolve_maildomain_host(account, "global"),
        )
        self.assertEqual(
            "p68-maildomainws.icloud.com",
            resolve_maildomain_host({}, "global"),
        )

    def test_captured_cookie_file_strips_request_query(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cookie_file = Path(tmpdir) / "cookies.txt"
            write_captured_cookie(
                str(cookie_file),
                "X-APPLE-WEBAUTH-USER=secret",
                "global",
                "https://p111-maildomainws.icloud.com/v2/hme/list"
                "?dsid=sensitive&clientBuildNumber=1",
                "p111-maildomainws.icloud.com",
            )
            content = cookie_file.read_text(encoding="utf-8")
            self.assertIn(
                "HIDEMYEMAIL_REQUEST_URL="
                "https://p111-maildomainws.icloud.com/v2/hme/list",
                content,
            )
            self.assertNotIn("dsid", content.lower())
            self.assertNotIn("sensitive", content)
            cookie, host = load_cookie_context(str(cookie_file), "global")
            self.assertEqual("X-APPLE-WEBAUTH-USER=secret", cookie)
            self.assertEqual("p111-maildomainws.icloud.com", host)

    def test_china_shard_cookie_file_strips_request_query(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cookie_file = Path(tmpdir) / "cookies.txt"
            write_captured_cookie(
                str(cookie_file),
                "X-APPLE-WEBAUTH-USER=synthetic",
                "global",
                "https://p224-maildomainws-china.icloud.com/v2/hme/list"
                "?clientBuildNumber=synthetic&dsid=synthetic",
                "p224-maildomainws-china.icloud.com",
            )
            content = cookie_file.read_text(encoding="utf-8")
            self.assertIn(
                "HIDEMYEMAIL_REQUEST_URL="
                "https://p224-maildomainws-china.icloud.com/v2/hme/list",
                content,
            )
            self.assertNotIn("?", content)
            cookie, host = load_cookie_context(str(cookie_file), "global")
            self.assertEqual("X-APPLE-WEBAUTH-USER=synthetic", cookie)
            self.assertEqual("p224-maildomainws-china.icloud.com", host)

    def test_builds_cookie_header_from_browser_cookie_records(self):
        self.assertEqual(
            browser_cookie_header(
                [
                    {"name": "X-APPLE-WEBAUTH-USER", "value": "valid"},
                    {"name": "session", "value": "valid"},
                    {"name": "session", "value": "duplicate"},
                    {"name": "", "value": "ignored"},
                ]
            ),
            "X-APPLE-WEBAUTH-USER=valid; session=valid",
        )

    def test_request_cookie_falls_back_to_exact_captured_url(self):
        request = SimpleNamespace(
            url="https://p168-maildomainws.icloud.com/v2/hme/list?dsid=123",
            all_headers=AsyncMock(return_value={}),
        )
        context = SimpleNamespace(
            cookies=AsyncMock(
                return_value=[
                    {"name": "X-APPLE-WEBAUTH-USER", "value": "valid"}
                ]
            )
        )
        cookie = asyncio.run(browser_request_cookie_header(request, context))
        self.assertEqual("X-APPLE-WEBAUTH-USER=valid", cookie)
        context.cookies.assert_awaited_once_with([request.url])

    def test_capture_cli_exposes_browser_and_timeout_controls(self):
        result = CliRunner().invoke(capturecookiecommand, ["--help"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--browser", result.output)
        self.assertIn("chrome", result.output)
        self.assertIn("edge", result.output)
        self.assertIn("--timeout-seconds", result.output)

    def test_capture_cli_forwards_selected_browser_and_timeout(self):
        capture = AsyncMock(return_value=True)
        with patch("hidemyemail_generator.main._capture_cookie", capture):
            result = CliRunner().invoke(
                capturecookiecommand,
                [
                    "--cookie-file",
                    "capture.txt",
                    "--region",
                    "global",
                    "--browser",
                    "chrome",
                    "--timeout-seconds",
                    "120",
                ],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        capture.assert_awaited_once_with(
            "capture.txt",
            "global",
            browser="chrome",
            timeout_seconds=120,
        )


if __name__ == "__main__":
    unittest.main()
