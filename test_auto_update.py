"""Unit tests for the auto-update functionality in tray.py."""

import json
import threading
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

from updater import (
    AutoUpdater,
    CURRENT_VERSION,
    _parse_version,
    _version_newer,
)


class TestParseVersion(unittest.TestCase):
    def test_standard(self):
        self.assertEqual(_parse_version("1.2.3"), (1, 2, 3))

    def test_single(self):
        self.assertEqual(_parse_version("2"), (2,))

    def test_bad_input(self):
        self.assertEqual(_parse_version("not.a.version"), (0,))

    def test_empty(self):
        self.assertEqual(_parse_version(""), (0,))


class TestVersionNewer(unittest.TestCase):
    def test_newer_patch(self):
        self.assertTrue(_version_newer("1.0.1", "1.0.0"))

    def test_newer_minor(self):
        self.assertTrue(_version_newer("1.1.0", "1.0.9"))

    def test_newer_major(self):
        self.assertTrue(_version_newer("2.0.0", "1.9.9"))

    def test_same(self):
        self.assertFalse(_version_newer("1.0.0", "1.0.0"))

    def test_older(self):
        self.assertFalse(_version_newer("0.9.9", "1.0.0"))


class TestAutoUpdater(unittest.TestCase):
    def _make_updater(self, api_url="https://test.example.com", api_key="testkey"):
        config = {"api_url": api_url, "api_key": api_key}
        return AutoUpdater(config)

    def _mock_response(self, data: dict, status: int = 200):
        """Build a mock urlopen context-manager return value."""
        body = json.dumps(data).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = status
        return mock_resp

    # -- Up-to-date --------------------------------------------------------

    def test_up_to_date(self):
        updater = self._make_updater()
        with patch("urllib.request.urlopen", return_value=self._mock_response({"version": CURRENT_VERSION})):
            status, message = updater.check()
        self.assertEqual(status, "up_to_date")
        self.assertIn("up to date", message.lower())

    # -- Update available --------------------------------------------------

    def test_update_available_spawns_download_thread(self):
        updater = self._make_updater()
        new_version = "9.9.9"
        download_started = threading.Event()

        def fake_download(version, url):
            download_started.set()

        updater._download_update = fake_download

        with patch("urllib.request.urlopen", return_value=self._mock_response({
            "version": new_version, "download_url": "https://example.com/agent.zip"
        })):
            status, message = updater.check()

        self.assertEqual(status, "update_available")
        self.assertIn(new_version, message)
        download_started.wait(timeout=2)
        self.assertTrue(download_started.is_set())

    def test_update_message_contains_version(self):
        updater = self._make_updater()
        with patch("urllib.request.urlopen", return_value=self._mock_response({
            "version": "2.5.0"
        })):
            status, message = updater.check()
        self.assertEqual(status, "update_available")
        self.assertIn("2.5.0", message)

    # -- Error cases -------------------------------------------------------

    def test_network_error_returns_error_status(self):
        updater = self._make_updater()
        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            status, message = updater.check()
        self.assertEqual(status, "error")
        self.assertIn("failed", message.lower())

    def test_empty_version_returns_error(self):
        updater = self._make_updater()
        with patch("urllib.request.urlopen", return_value=self._mock_response({"version": ""})):
            status, message = updater.check()
        self.assertEqual(status, "error")

    def test_missing_version_key_returns_error(self):
        updater = self._make_updater()
        with patch("urllib.request.urlopen", return_value=self._mock_response({})):
            status, message = updater.check()
        self.assertEqual(status, "error")

    # -- API key header ----------------------------------------------------

    def test_api_key_sent_in_header(self):
        updater = self._make_updater(api_key="mysecretkey")
        captured_req = {}

        def fake_urlopen(req, timeout=10):
            captured_req["headers"] = dict(req.headers)
            return self._mock_response({"version": CURRENT_VERSION})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            updater.check()

        self.assertIn("X-api-key", captured_req["headers"])
        self.assertEqual(captured_req["headers"]["X-api-key"], "mysecretkey")

    # -- Download stub (no-op when no URL) ---------------------------------

    def test_download_no_url_is_safe(self):
        """_download_update with no URL must not raise."""
        updater = self._make_updater()
        updater._download_update("1.2.3", "")  # should not raise


if __name__ == "__main__":
    unittest.main()
