import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from hidemyemail_generator.main import RichHideMyEmail


LIST_RESPONSE = {
    "success": True,
    "result": {
        "hmeEmails": [
            {
                "anonymousId": "abc-123",
                "hme": "example@icloud.com",
                "label": "Example",
                "note": "Original note",
                "isActive": True,
                "createTimestamp": 1753531200000,
            }
        ]
    },
}


class FakeTransport:
    """Records every request the client makes and replays canned responses."""

    def __init__(self, responses=None):
        self.calls = []
        self.attempts = []
        self.responses = responses or {}

    async def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("json")))
        self.attempts.append(kwargs.get("attempts"))
        endpoint = url.rsplit("/", 1)[-1]
        response = self.responses.get(endpoint, {"success": True, "result": {}})
        if isinstance(response, list):
            response = response.pop(0)
        return response


def run(coro):
    return asyncio.run(coro)


class AddressManagementTests(unittest.TestCase):
    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        cookie_file = Path(tmpdir.name) / "cookies.txt"
        cookie_file.write_text('X-APPLE-WEBAUTH-USER="v=1:s=0:d=1"', encoding="utf-8")

        self.transport = FakeTransport({"list": LIST_RESPONSE})
        patcher = patch.object(RichHideMyEmail, "_request_json", self.transport)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.hme = RichHideMyEmail(cookie_file=str(cookie_file))

    def test_list_exposes_anonymous_id(self):
        result = run(self.hme.list(None, True))
        self.assertEqual(result["addresses"][0]["anonymous_id"], "abc-123")

    def test_deactivate_posts_anonymous_id(self):
        result = run(self.hme.set_active("example@icloud.com", False))
        self.assertTrue(result["ok"])
        self.assertFalse(result["is_active"])
        method, url, payload = self.transport.calls[-1]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/v1/hme/deactivate"))
        self.assertEqual(payload, {"anonymousId": "abc-123"})

    def test_reactivate_posts_anonymous_id(self):
        result = run(self.hme.set_active("example@icloud.com", True))
        self.assertTrue(result["ok"])
        _, url, payload = self.transport.calls[-1]
        self.assertTrue(url.endswith("/v1/hme/reactivate"))
        self.assertEqual(payload, {"anonymousId": "abc-123"})

    def test_delete_transport_is_never_retried(self):
        result = run(self.hme.delete_email("abc-123"))
        self.assertTrue(result["success"])
        self.assertEqual(self.transport.attempts[-1], 1)

    def test_permanent_delete_deactivates_active_address_and_verifies_absence(self):
        self.transport.responses["list"] = [
            LIST_RESPONSE,
            {"success": True, "result": {"hmeEmails": []}},
        ]
        result = run(self.hme.permanently_delete("example@icloud.com"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["was_active"])
        self.assertTrue(result["deactivated"])
        self.assertTrue(result["verified_absent"])
        self.assertEqual(
            [call[1].rsplit("/", 1)[-1] for call in self.transport.calls],
            ["list", "deactivate", "delete", "list"],
        )

    def test_permanent_delete_inactive_address_skips_deactivate(self):
        inactive = {
            "success": True,
            "result": {"hmeEmails": [{**LIST_RESPONSE["result"]["hmeEmails"][0], "isActive": False}]},
        }
        self.transport.responses["list"] = [
            inactive,
            {"success": True, "result": {"hmeEmails": []}},
        ]
        result = run(self.hme.permanently_delete("example@icloud.com"))
        self.assertTrue(result["ok"])
        endpoints = [call[1].rsplit("/", 1)[-1] for call in self.transport.calls]
        self.assertEqual(endpoints, ["list", "delete", "list"])

    def test_deactivate_failure_does_not_submit_delete(self):
        self.transport.responses["deactivate"] = {
            "success": False,
            "error": {"errorCode": "deactivate_failed", "errorMessage": "rejected"},
        }
        self.transport.responses["list"] = [LIST_RESPONSE, LIST_RESPONSE]
        result = run(self.hme.permanently_delete("example@icloud.com"))
        self.assertFalse(result["ok"])
        self.assertFalse(result["delete_requested"])
        self.assertEqual(
            [call[1].rsplit("/", 1)[-1] for call in self.transport.calls],
            ["list", "deactivate", "list"],
        )

    def test_ambiguous_delete_succeeds_only_when_list_confirms_absence(self):
        self.transport.responses["delete"] = {
            "error": 1,
            "reason": "Request timed out after 30s",
        }
        self.transport.responses["list"] = [
            LIST_RESPONSE,
            {"success": True, "result": {"hmeEmails": []}},
        ]
        result = run(self.hme.permanently_delete("example@icloud.com"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["verified_absent"])
        self.assertEqual(
            sum(call[1].endswith("/delete") for call in self.transport.calls), 1
        )

    def test_lingering_address_fails_without_repeating_delete(self):
        with patch(
            "hidemyemail_generator.main.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            result = run(self.hme.permanently_delete("example@icloud.com"))
        self.assertFalse(result["ok"])
        self.assertFalse(result["verified_absent"])
        self.assertEqual(result["error"]["code"], "delete_verification_failed")
        self.assertEqual(
            sum(call[1].endswith("/delete") for call in self.transport.calls), 1
        )
        self.assertEqual(sleep.await_count, 3)

    def test_verify_only_absent_uses_list_without_destructive_requests(self):
        self.transport.responses["list"] = {
            "success": True,
            "result": {"hmeEmails": []},
        }
        result = run(self.hme.verify_permanent_delete("example@icloud.com"))
        self.assertTrue(result["verified_absent"])
        self.assertFalse(result["delete_requested"])
        self.assertEqual(
            [call[1].rsplit("/", 1)[-1] for call in self.transport.calls],
            ["list"],
        )

    def test_verify_only_present_retries_lists_but_never_delete(self):
        with patch(
            "hidemyemail_generator.main.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            result = run(self.hme.verify_permanent_delete("example@icloud.com"))
        self.assertFalse(result["verified_absent"])
        self.assertFalse(result["delete_requested"])
        self.assertEqual(
            [call[1].rsplit("/", 1)[-1] for call in self.transport.calls],
            ["list", "list", "list", "list"],
        )
        self.assertEqual(sleep.await_count, 3)

    def test_update_metadata_keeps_unspecified_fields(self):
        result = run(self.hme.update_metadata("example@icloud.com", "Renamed", None))
        self.assertTrue(result["ok"])
        _, url, payload = self.transport.calls[-1]
        self.assertTrue(url.endswith("/v1/hme/updateMetaData"))
        self.assertEqual(
            payload,
            {"anonymousId": "abc-123", "label": "Renamed", "note": "Original note"},
        )

    def test_unknown_address_fails_without_mutating(self):
        result = run(self.hme.set_active("nope@icloud.com", False))
        self.assertFalse(result["ok"])
        self.assertIn("nope@icloud.com", result["error"]["message"])
        self.assertEqual(len(self.transport.calls), 1)

    def test_missing_anonymous_id_fails_without_mutating(self):
        self.transport.responses["list"] = {
            "success": True,
            "result": {
                "hmeEmails": [
                    {"hme": "example@icloud.com", "label": "", "isActive": True}
                ]
            },
        }
        result = run(self.hme.set_active("example@icloud.com", False))
        self.assertFalse(result["ok"])
        self.assertIn("no identifier", result["error"]["message"])
        self.assertEqual(len(self.transport.calls), 1)

    def test_failed_list_surfaces_error(self):
        self.transport.responses["list"] = {
            "success": False,
            "error": {"errorCode": "-41015", "errorMessage": "Too many requests"},
        }
        result = run(self.hme.update_metadata("example@icloud.com", "x", None))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "-41015")


if __name__ == "__main__":
    unittest.main()
