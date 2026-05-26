"""Device pairing lifecycle management.

Manages the one-time pairing secret flow for device-mode authentication.
On first boot, a pairing secret is generated and logged to the console.
The device owner enters this secret via the nomotactic UI to claim the
device and receive JWT tokens.

The pairing secret is also written to a shared file so that nomopractic
reads it as the WPA2 passphrase for the Wi-Fi Soft AP (see nomopractic ADR-005).

See ADR-014 for design rationale.
"""

import grp
import hmac
import logging
import os
import secrets
import stat
import tempfile

from nomothetic.device_jwt import DeviceJwtSecretStore

logger = logging.getLogger(__name__)

_DEFAULT_PAIRING_SECRET_PATH = "/var/lib/nomon/pairing_secret"
_PAIRING_SECRET_DIGITS = 8


def get_pairing_secret_path() -> str:
    """Return the configured pairing secret file path.

    Reads from the ``NOMON_PAIRING_SECRET_PATH`` environment variable,
    falling back to ``/var/lib/nomon/pairing_secret``.

    Returns
    -------
    str
        Absolute path to the shared pairing secret file.
    """
    return os.environ.get("NOMON_PAIRING_SECRET_PATH", _DEFAULT_PAIRING_SECRET_PATH)


def _read_shared_secret() -> str | None:
    """Read the pairing secret from the shared file, if present and valid.

    Returns the stripped secret string if the file exists and contains a
    non-empty value, otherwise returns ``None``.

    Returns
    -------
    str or None
        The pairing secret read from disk, or ``None`` if the file is absent
        or cannot be read.
    """
    path = get_pairing_secret_path()
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _write_shared_secret(secret: str) -> None:
    """Write the pairing secret to the shared file atomically.

    Uses a write-to-temp-then-rename pattern for atomicity.  Sets file
    mode ``0640`` and group ``nomon`` so that nomopractic can read it.

    If the target directory does not exist or permissions cannot be set,
    a warning is logged but no exception is raised — HTTP pairing still
    works; the shared file is used as the Wi-Fi Soft AP passphrase.

    Parameters
    ----------
    secret : str
        The pairing secret to persist.
    """
    path = get_pairing_secret_path()
    target_dir = os.path.dirname(path)

    if not os.path.isdir(target_dir):
        logger.warning(
            "Pairing secret directory %s does not exist; "
            "Wi-Fi Soft AP will not work until it is created",
            target_dir,
        )
        return

    fd = None
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=".pairing_secret_")
        os.write(fd, secret.encode("utf-8"))
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)  # 0o640
        os.close(fd)
        fd = None

        try:
            nomon_gid = grp.getgrnam("nomon").gr_gid
            os.chown(tmp_path, -1, nomon_gid)
        except (KeyError, PermissionError):
            logger.warning(
                "Could not set group 'nomon' on pairing secret file; "
                "nomopractic may not be able to read it as the Wi-Fi Soft AP passphrase"
            )

        os.rename(tmp_path, path)
        logger.info("Pairing secret written to %s", path)
        tmp_path = None  # rename succeeded — don't clean up
    except OSError:
        logger.warning(
            "Failed to write pairing secret to %s; Wi-Fi Soft AP passphrase will not be available",
            path,
            exc_info=True,
        )
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class PairingState:
    """Manages device pairing lifecycle.

    On construction, a fresh JWT signing secret is generated.  The pairing
    secret is created on demand via :meth:`generate_secret` and consumed
    (single-use) via :meth:`verify_and_consume`.

    Attributes
    ----------
    secret : str or None
        Current pairing secret (None when consumed or not yet generated).
    paired : bool
        Whether the device has been successfully paired.
    owner_email : str or None
        Email of the paired owner (set externally after pairing).
    jwt_secret : str
        Random JWT signing key (regenerated on :meth:`reset`).
    """

    def __init__(self) -> None:
        self.secret: str | None = None
        self._last_secret: str | None = None
        self.paired: bool = False
        self.owner_email: str | None = None
        # JWT signing secret is loaded from (or generated into) the persistent
        # store at /var/lib/nomon/device_jwt_secret so it survives AP → WiFi
        # mode switches and service restarts.  See DeviceJwtSecretStore (ADR-016).
        self.jwt_secret: str = DeviceJwtSecretStore().load_or_generate()

    def load_or_generate_secret(self) -> str:
        """Load an existing pairing secret or generate a new one.

        On **first boot** (no file on disk) or when the stored value is
        invalid, delegates to :meth:`generate_secret` to create and persist a
        new secret.

        On **subsequent restarts** (valid 8-digit secret already on disk),
        loads that value without overwriting the file, so the WPA2 Soft AP
        passphrase stays stable across service restarts.

        Returns
        -------
        str
            The active pairing secret.
        """
        existing = _read_shared_secret()
        if existing is not None and existing.isdigit() and len(existing) == _PAIRING_SECRET_DIGITS:
            self.secret = existing
            self._last_secret = existing
            self.paired = False
            logger.info(
                "Loaded existing pairing secret from %s",
                get_pairing_secret_path(),
            )
            return self.secret
        logger.info("Generated new pairing secret")
        return self.generate_secret()

    def generate_secret(self) -> str:
        """Generate a pairing secret for device authentication.

        The secret is written to the shared pairing secret file so that
        nomopractic's Wi-Fi Soft AP (`scripts/ap-mode.sh`) can read it
        as the WPA2 hotspot passphrase (see nomopractic ADR-005).

        Returns
        -------
        str
            The pairing secret (8-digit zero-padded numeric string).
        """
        passkey = secrets.randbelow(10**_PAIRING_SECRET_DIGITS)
        self.secret = f"{passkey:0{_PAIRING_SECRET_DIGITS}d}"
        self._last_secret = self.secret
        self.paired = False
        _write_shared_secret(self.secret)
        return self.secret

    def get_active_secret(self) -> str | None:
        """Return the current pairing secret from memory or the shared file."""
        if self.secret is not None:
            return self.secret
        existing = _read_shared_secret()
        if existing is not None and existing.isdigit() and len(existing) == _PAIRING_SECRET_DIGITS:
            return existing
        return self._last_secret

    def has_pairing_secret(self) -> bool:
        """Return True when a valid pairing secret is available."""
        return self.get_active_secret() is not None

    def verify_secret(self, candidate: str) -> bool:
        """Verify candidate against the active pairing secret (constant-time)."""
        active = self.get_active_secret()
        return active is not None and hmac.compare_digest(active, candidate)

    def complete_pairing(self, owner_email: str) -> None:
        """Mark the device as paired for *owner_email*."""
        self.paired = True
        self.owner_email = owner_email
        self.secret = None

    def verify_and_consume(self, candidate: str) -> bool:
        """Verify candidate against stored secret (constant-time).

        On success the secret is consumed from in-memory state — subsequent
        calls in the same runtime will return False until a new session is
        started. The shared on-disk secret remains available for secure
        re-pairing.

        Parameters
        ----------
        candidate : str
            The secret value to verify.

        Returns
        -------
        bool
            True if the candidate matches and the device was not already paired.
        """
        if self.paired or not self.verify_secret(candidate):
            return False
        self.paired = True
        self.secret = None
        return True

    def is_paired(self) -> bool:
        """Return whether the device has been paired.

        Returns
        -------
        bool
        """
        return self.paired

    def reset_session(self) -> None:
        """Invalidate the current auth session while preserving the pairing secret.

        Rotates the JWT signing secret so previously issued access tokens stop
        working, clears owner state, and leaves the shared pairing secret file
        untouched so the legitimate owner can pair again without restarting the
        service.
        """
        self.paired = False
        self.owner_email = None
        self.secret = None
        self.jwt_secret = DeviceJwtSecretStore().rotate()

    def reset(self) -> None:
        """Clear pairing state, rotate JWT state, and delete the pairing secret.

        Deletes the on-disk pairing secret file so the next service startup
        generates a fresh passphrase.  After reset the device is unpaired and
        a new pairing secret must be generated via :meth:`generate_secret` or
        :meth:`load_or_generate_secret`.
        """
        self.reset_session()
        self._last_secret = None
        try:
            os.unlink(get_pairing_secret_path())
        except OSError:
            pass
