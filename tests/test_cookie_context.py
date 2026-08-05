import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from hidemyemail_generator.main import (
    browser_cookie_header,
    capturecookiecommand,
    is_hidemyemail_app_request,
    load_cookie_context,
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
