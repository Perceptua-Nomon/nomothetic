"""Device pairing lifecycle management.

Manages the one-time pairing secret flow for device-mode authentication.
On first boot, a pairing secret is generated and logged to the console.
The device owner enters this secret via the nomotactic UI to claim the
device and receive JWT tokens.

The pairing secret is also written to a shared file so that nomopractic
can read it for BLE pairing verification (see nomopractic ADR-003).

See ADR-014 for design rationale.
"""

import grp
import hmac
import logging
import os
import secrets
import stat
import tempfile

logger = logging.getLogger(__name__)

_DEFAULT_PAIRING_SECRET_PATH = "/var/lib/nomon/pairing_secret"


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


def _write_shared_secret(secret: str) -> None:
    """Write the pairing secret to the shared file atomically.

    Uses a write-to-temp-then-rename pattern for atomicity.  Sets file
    mode ``0640`` and group ``nomon`` so that nomopractic can read it.

    If the target directory does not exist or permissions cannot be set,
    a warning is logged but no exception is raised — HTTP pairing still
    works; only BLE pairing requires the shared file.

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
            "BLE pairing will not work until it is created",
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
                "nomopractic may not be able to read it"
            )

        os.rename(tmp_path, path)
        logger.info("Pairing secret written to %s", path)
        tmp_path = None  # rename succeeded — don't clean up
    except OSError:
        logger.warning(
            "Failed to write pairing secret to %s; " "BLE pairing will not work",
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
        self.paired: bool = False
        self.owner_email: str | None = None
        self.jwt_secret: str = secrets.token_urlsafe(48)

    def generate_secret(self) -> str:
        """Generate a 6-digit numeric BLE passkey (000000–999999).

        The passkey is written to the shared pairing secret file so that
        nomopractic's BlueZ passkey agent can read it for OS-level
        Bluetooth pairing.

        Returns
        -------
        str
            The pairing secret (6-digit zero-padded numeric string).
        """
        passkey = secrets.randbelow(1_000_000)
        self.secret = f"{passkey:06d}"
        self.paired = False
        _write_shared_secret(self.secret)
        return self.secret

    def verify_and_consume(self, candidate: str) -> bool:
        """Verify candidate against stored secret (constant-time).

        On success the secret is consumed — subsequent calls with the
        same value will return False.

        Parameters
        ----------
        candidate : str
            The secret value to verify.

        Returns
        -------
        bool
            True if the candidate matches and the device was not already paired.
        """
        if self.secret is None or self.paired:
            return False
        if hmac.compare_digest(self.secret, candidate):
            self.paired = True
            self.secret = None
            return True
        return False

    def is_paired(self) -> bool:
        """Return whether the device has been paired.

        Returns
        -------
        bool
        """
        return self.paired

    def reset(self) -> None:
        """Clear pairing state and regenerate JWT secret.

        After reset the device is unpaired and a new pairing secret must
        be generated via :meth:`generate_secret`.
        """
        self.paired = False
        self.owner_email = None
        self.secret = None
        self.jwt_secret = secrets.token_urlsafe(48)
