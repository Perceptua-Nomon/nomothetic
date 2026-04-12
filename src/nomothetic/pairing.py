"""Device pairing lifecycle management.

Manages the one-time pairing secret flow for device-mode authentication.
On first boot, a pairing secret is generated and logged to the console.
The device owner enters this secret via the nomotactic UI to claim the
device and receive JWT tokens.

See ADR-014 for design rationale.
"""

import hmac
import secrets


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
        """Generate a 128-bit pairing secret.

        Returns
        -------
        str
            The pairing secret (22-character URL-safe string).
        """
        self.secret = secrets.token_urlsafe(16)
        self.paired = False
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
