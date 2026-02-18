"""GCC Desktop Agent — Auto-updater.

Checks the GCC API for a newer agent binary and triggers a background
download when one is available.  This module is intentionally free of
GUI dependencies so it can be tested headlessly.
"""

import json
import logging
import threading
import urllib.error
import urllib.request

logger = logging.getLogger("gcc-agent.updater")

# ---------------------------------------------------------------------------
# Version utilities
# ---------------------------------------------------------------------------

CURRENT_VERSION = "1.0.0"


def _parse_version(v: str) -> tuple:
    """Parse 'X.Y.Z' into a comparable tuple of ints."""
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except (ValueError, AttributeError):
        return (0,)


def _version_newer(candidate: str, current: str) -> bool:
    """Return True if *candidate* is strictly newer than *current*."""
    return _parse_version(candidate) > _parse_version(current)


# ---------------------------------------------------------------------------
# AutoUpdater
# ---------------------------------------------------------------------------

class AutoUpdater:
    """Checks the GCC API for a newer agent version and triggers download.

    The :meth:`check` method is safe to call from any thread.  It never
    raises — errors are logged and returned as an ``"error"`` status so
    the tray app is unaffected by network issues.
    """

    def __init__(self, config: dict):
        self.config = config
        self._current = CURRENT_VERSION

    def _api_url(self) -> str:
        return self.config.get("api_url", "https://gcc-api.devloopment.com").rstrip("/")

    def check(self) -> tuple[str, str]:
        """Check for updates against the GCC API.

        Returns
        -------
        (status, message)
            status  : ``"up_to_date"`` | ``"update_available"`` | ``"error"``
            message : Human-readable string suitable for a desktop notification.
                      ``"Up to date"`` or ``"Downloading v1.x.x…"``
        """
        try:
            url = f"{self._api_url()}/api/agent/latest-version"
            req = urllib.request.Request(url)
            api_key = self.config.get("api_key", "")
            if api_key:
                req.add_header("X-API-Key", api_key)

            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            latest = data.get("version", "").strip()
            if not latest:
                logger.warning("Update check: empty version in response")
                return "error", "Update check failed (bad response)"

            if _version_newer(latest, self._current):
                download_url = data.get("download_url", "")
                logger.info("Update available: v%s (current: v%s)", latest, self._current)
                threading.Thread(
                    target=self._download_update,
                    args=(latest, download_url),
                    daemon=True,
                ).start()
                return "update_available", f"Downloading v{latest}…"
            else:
                logger.info("Agent is up to date (v%s)", self._current)
                return "up_to_date", "Up to date"

        except urllib.error.HTTPError as exc:
            logger.warning("Update check HTTP error: %s", exc.code)
            return "error", f"Update check failed (HTTP {exc.code})"
        except Exception as exc:
            logger.warning("Update check failed: %s", exc)
            return "error", "Update check failed"

    def _download_update(self, version: str, url: str) -> None:
        """Background: download the update package and prepare installation.

        Currently a stub — the full implementation should:
        1. Stream the binary to a temp file
        2. Verify checksum
        3. Replace the running executable
        4. Restart the agent
        """
        if not url:
            logger.info("No download_url provided for v%s — skipping download", version)
            return
        logger.info("Downloading update v%s from %s", version, url)
        # TODO: implement download → checksum verify → replace → restart
        logger.info("Download stub — v%s ready to install (url=%s)", version, url)
