"""Persisted device JWT signing secret.

Manages the JWT signing secret used by the device pairing lifecycle
(``nomothetic.pairing``).  The secret is stored on disk at
``/var/lib/nomon/device_jwt_secret`` so it survives service restarts and
AP → WiFi mode switches.

Without persistence, every service restart generates a new secret, which
invalidates all previously issued JWTs and forces the user to re-pair.

See ADR-016, §22.4 for design rationale.

Classes
-------
DeviceJwtSecretStore
    Load-or-generate and rotate the on-disk JWT signing secret.
"""

from __future__ import annotations

import logging
import os
import secrets
import stat
import tempfile

logger = logging.getLogger(__name__)

_DEFAULT_SECRET_PATH = "/var/lib/nomon/device_jwt_secret"
_MIN_SECRET_LENGTH = 32


def _get_secret_path() -> str:
    """Return the configured JWT secret file path.

    Reads from the ``NOMON_DEVICE_JWT_SECRET_PATH`` environment variable,
    falling back to ``/var/lib/nomon/device_jwt_secret``.
    """
    return os.environ.get("NOMON_DEVICE_JWT_SECRET_PATH", _DEFAULT_SECRET_PATH)


class DeviceJwtSecretStore:
    """Persistent device JWT signing secret store.

    The secret is stored in a ``0600`` file at *path* (default:
    ``/var/lib/nomon/device_jwt_secret``).  On write failure (e.g. the
    directory does not yet exist), a ``WARNING`` is logged and the
    in-memory secret is returned so the service can start regardless.

    Parameters
    ----------
    path : str, optional
        Absolute path to the secret file.  Defaults to
        ``$NOMON_DEVICE_JWT_SECRET_PATH`` or
        ``/var/lib/nomon/device_jwt_secret``.
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _get_secret_path()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_or_generate(self) -> str:
        """Return the existing secret, or generate and persist a new one.

        1. Reads the secret from *path*.
        2. If the file exists and the value is ``≥ 32`` characters, returns it.
        3. If the file **exists but cannot be read** (permissions, transient
           I/O), it is never overwritten — previously issued tokens must not
           be silently invalidated. A ``ERROR`` is logged and an in-memory
           secret is used for this run; the file is left for the next start.
        4. Otherwise (no file, or a too-short/corrupt value) generates a new
           ``secrets.token_urlsafe(48)`` value, writes it atomically with
           ``0600`` permissions, and returns it.
        5. If the write fails, logs a ``WARNING`` and returns the in-memory
           value (the service continues without persistence).

        Returns
        -------
        str
            JWT signing secret (``≥ 48`` chars for new secrets).
        """
        existing = self._read()
        if existing is not None:
            logger.info("Device JWT secret loaded from %s", self._path)
            return existing

        new_secret = secrets.token_urlsafe(48)
        if self._unreadable_file_present():
            logger.error(
                "Device JWT secret at %s exists but could not be read; using an "
                "in-memory secret for this run without overwriting the file. "
                "Tokens issued now will not survive a restart; previously issued "
                "tokens work again once the file is readable.",
                self._path,
            )
            return new_secret
        self._write(new_secret)
        return new_secret

    def _unreadable_file_present(self) -> bool:
        """True when a plausibly valid secret file exists but could not be read.

        ``os.path.getsize`` only needs directory permissions, so it succeeds
        even when reading the file content fails. A file shorter than the
        minimum length is treated as corrupt (not merely unreadable) and stays
        eligible for regeneration.
        """
        try:
            return os.path.getsize(self._path) >= _MIN_SECRET_LENGTH
        except OSError:
            return False

    def rotate(self) -> str:
        """Generate a new secret, overwrite the file, and return it.

        All previously issued JWTs become invalid after rotation.

        Returns
        -------
        str
            The newly generated JWT signing secret.
        """
        new_secret = secrets.token_urlsafe(48)
        self._write(new_secret)
        logger.info("Device JWT secret rotated at %s", self._path)
        return new_secret

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read(self) -> str | None:
        """Read and validate the secret from disk.

        Returns ``None`` if the file is absent, unreadable, or contains a
        value shorter than ``_MIN_SECRET_LENGTH`` (corrupt file guard).
        """
        try:
            with open(self._path, encoding="utf-8") as fh:
                value = fh.read().strip()
        except OSError:
            return None

        if len(value) < _MIN_SECRET_LENGTH:
            logger.warning(
                "Device JWT secret at %s is too short (%d chars, minimum %d) — "
                "treating as invalid and regenerating.",
                self._path,
                len(value),
                _MIN_SECRET_LENGTH,
            )
            return None

        return value

    def _write(self, secret: str) -> None:
        """Atomically write *secret* to the secret file with ``0600`` permissions.

        Uses a ``tempfile.mkstemp`` + ``os.rename`` pattern for atomicity.
        Logs a ``WARNING`` and returns without raising if the target directory
        is absent or the write otherwise fails — the caller falls back to the
        in-memory secret.
        """
        target_dir = os.path.dirname(self._path)

        if not os.path.isdir(target_dir):
            logger.warning(
                "Device JWT secret directory %s does not exist; "
                "secret will not be persisted.  Service will start with an "
                "in-memory secret that is lost on restart.",
                target_dir,
            )
            return

        fd: int | None = None
        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=".device_jwt_secret_")
            os.write(fd, secret.encode("utf-8"))
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
            os.close(fd)
            fd = None
            os.rename(tmp_path, self._path)
            tmp_path = None
            logger.info("Device JWT secret written to %s", self._path)
        except OSError:
            logger.warning(
                "Failed to write device JWT secret to %s; " "service will use an in-memory secret.",
                self._path,
                exc_info=True,
            )
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
