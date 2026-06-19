"""Plugin authentication via Ed25519 challenge-response.

Lets an on-device autonomy plugin (e.g. ``autonomon``) obtain a short-lived
device JWT *without* a pre-shared secret on disk. The plugin holds an Ed25519
private key generated on-device; nomothetic stores only the matching public key.
At runtime the plugin proves possession of the private key by signing a
server-issued nonce, and receives a device JWT in return.

Security model (see ADR-019):

* **Registration is localhost-only.** The public key is registered during deploy
  by a process running *on the device*; a remote caller cannot register a key.
* **Registration is key-stable.** Re-registering the *same* key is a no-op
  (idempotent redeploys); registering a *different* key for an already-registered
  plugin is rejected (blocks key-swap attacks). To rotate, the operator deletes
  the stored ``.pub`` file and redeploys.
* **Challenge nonces are single-use and short-lived**, so a captured token
  request cannot be replayed.
* **The issued JWT is signed with the device JWT secret**, so it is inherently
  scoped to *this* device — a key extracted from one Pi yields a token no other
  device will accept.

Classes
-------
PluginKeyStore
    Load/register Ed25519 public keys, one file per plugin.
ChallengeStore
    In-memory, single-use, TTL-bounded nonce store.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import stat
import tempfile
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

logger = logging.getLogger(__name__)

_DEFAULT_KEY_DIR = "/var/lib/nomon/plugin_keys"
_DEFAULT_NONCE_TTL_S = 30.0
_MAX_OUTSTANDING_NONCES = 256
# Plugin names map to filenames, so restrict them to a safe charset (no path
# separators, no traversal) before they ever touch the filesystem.
_PLUGIN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class PluginAuthError(Exception):
    """Base error for plugin authentication failures."""


class InvalidPluginName(PluginAuthError):
    """Raised when a plugin name is not a safe, recognised identifier."""


class KeyConflict(PluginAuthError):
    """Raised when registering a different key for an already-registered plugin."""


def _validate_plugin_name(name: str) -> str:
    """Return *name* if it is a safe plugin identifier, else raise.

    Parameters
    ----------
    name : str
        Candidate plugin name.

    Returns
    -------
    str
        The validated name.

    Raises
    ------
    InvalidPluginName
        If *name* contains characters outside ``[a-z0-9_-]`` or is too long.
    """
    if not _PLUGIN_NAME_RE.match(name):
        raise InvalidPluginName(
            f"invalid plugin name {name!r}: must match {_PLUGIN_NAME_RE.pattern}"
        )
    return name


def _get_key_dir() -> str:
    """Return the configured plugin public-key directory."""
    return os.environ.get("NOMON_PLUGIN_KEY_DIR", _DEFAULT_KEY_DIR)


class PluginKeyStore:
    """On-disk store of registered plugin Ed25519 public keys.

    One PEM file per plugin at ``<dir>/<plugin>.pub``. The directory holds only
    public keys, so it is not sensitive, but writes are atomic to avoid torn
    files during concurrent deploys.

    Parameters
    ----------
    key_dir : str, optional
        Directory holding the ``.pub`` files. Defaults to
        ``$NOMON_PLUGIN_KEY_DIR`` or ``/var/lib/nomon/plugin_keys``.
    """

    def __init__(self, key_dir: str | None = None) -> None:
        self._dir = key_dir or _get_key_dir()

    def _path(self, plugin: str) -> str:
        return os.path.join(self._dir, f"{_validate_plugin_name(plugin)}.pub")

    def get_public_key(self, plugin: str) -> Ed25519PublicKey | None:
        """Return the registered public key for *plugin*, or ``None``.

        Parameters
        ----------
        plugin : str
            Plugin name.

        Returns
        -------
        Ed25519PublicKey or None
            The parsed public key, or ``None`` if the plugin is unregistered or
            the stored file is unreadable/corrupt.
        """
        pem = self._read_pem(plugin)
        if pem is None:
            return None
        try:
            key = serialization.load_pem_public_key(pem)
        except (ValueError, TypeError):
            logger.warning("Stored public key for plugin %r is unparseable", plugin)
            return None
        if not isinstance(key, Ed25519PublicKey):
            logger.warning("Stored key for plugin %r is not Ed25519", plugin)
            return None
        return key

    def register(self, plugin: str, public_key_pem: str) -> str:
        """Register (or confirm) a plugin's public key.

        Idempotent in the safe direction: re-registering the *same* key returns
        ``"exists"``; registering a *different* key for an already-registered
        plugin raises :class:`KeyConflict`.

        Parameters
        ----------
        plugin : str
            Plugin name (validated; becomes the filename).
        public_key_pem : str
            PEM-encoded Ed25519 public key (SubjectPublicKeyInfo).

        Returns
        -------
        str
            ``"registered"`` if newly stored, ``"exists"`` if the same key was
            already present.

        Raises
        ------
        InvalidPluginName
            If *plugin* is not a safe identifier.
        PluginAuthError
            If *public_key_pem* is not a valid Ed25519 public key.
        KeyConflict
            If a different key is already registered for *plugin*.
        """
        pem_bytes = public_key_pem.encode("utf-8")
        try:
            new_key = serialization.load_pem_public_key(pem_bytes)
        except (ValueError, TypeError) as exc:
            raise PluginAuthError(f"invalid public key PEM: {exc}") from exc
        if not isinstance(new_key, Ed25519PublicKey):
            raise PluginAuthError("public key must be Ed25519")

        existing = self.get_public_key(plugin)
        if existing is not None:
            if _raw_pub(existing) == _raw_pub(new_key):
                return "exists"
            raise KeyConflict(
                f"plugin {plugin!r} already registered with a different key; "
                "delete the stored .pub file to rotate"
            )

        self._write_pem(plugin, pem_bytes)
        logger.info("Registered public key for plugin %r", plugin)
        return "registered"

    # -- internal file helpers ------------------------------------------------

    def _read_pem(self, plugin: str) -> bytes | None:
        try:
            with open(self._path(plugin), "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def _write_pem(self, plugin: str, pem_bytes: bytes) -> None:
        os.makedirs(self._dir, exist_ok=True)
        path = self._path(plugin)
        fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=f".{plugin}_")
        try:
            os.write(fd, pem_bytes)
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 0o644
            os.close(fd)
            fd = -1
            os.rename(tmp, path)
            tmp = ""
        finally:
            if fd >= 0:
                os.close(fd)
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


def _raw_pub(key: Ed25519PublicKey) -> bytes:
    """Return the 32-byte raw form of an Ed25519 public key (for comparison)."""
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def verify_signature(public_key: Ed25519PublicKey, message: bytes, signature: bytes) -> bool:
    """Return ``True`` iff *signature* is a valid Ed25519 signature over *message*.

    Parameters
    ----------
    public_key : Ed25519PublicKey
        The registered plugin public key.
    message : bytes
        The signed bytes (the challenge nonce, UTF-8 encoded).
    signature : bytes
        The candidate signature.

    Returns
    -------
    bool
        Whether the signature verifies. Never raises on a bad signature.
    """
    try:
        public_key.verify(signature, message)
        return True
    except InvalidSignature:
        return False


class ChallengeStore:
    """In-memory, single-use, TTL-bounded challenge nonces, keyed by plugin.

    A plugin requests a nonce, signs it, and submits it back within the TTL.
    :meth:`consume` validates and removes the nonce so it cannot be replayed.

    Parameters
    ----------
    ttl_seconds : float, optional
        Nonce lifetime. Defaults to 30 s.
    max_outstanding : int, optional
        Upper bound on concurrently-valid nonces, to cap memory. Defaults to 256.
    """

    def __init__(
        self,
        ttl_seconds: float = _DEFAULT_NONCE_TTL_S,
        max_outstanding: int = _MAX_OUTSTANDING_NONCES,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_outstanding
        # nonce -> (plugin, expires_at_monotonic). Keyed by the (unguessable)
        # nonce — not by plugin — so concurrent challenges for the same plugin do
        # not evict each other and a flood of remote challenge requests cannot
        # knock out the on-device plugin's in-flight nonce.
        self._pending: dict[str, tuple[str, float]] = {}

    def issue(self, plugin: str) -> tuple[str, float]:
        """Issue a fresh single-use nonce for *plugin*.

        Multiple nonces may be outstanding per plugin at once (concurrent token
        acquisition is safe). Expired entries are swept first, and the total is
        capped at ``max_outstanding`` (oldest evicted) to bound memory.

        Parameters
        ----------
        plugin : str
            Plugin name.

        Returns
        -------
        tuple of (str, float)
            The nonce and its TTL in seconds.
        """
        now = time.monotonic()
        self._sweep(now)
        if len(self._pending) >= self._max:
            # Evict the soonest-to-expire entry to make room.
            oldest = min(self._pending, key=lambda n: self._pending[n][1])
            del self._pending[oldest]
        nonce = secrets.token_urlsafe(32)
        self._pending[nonce] = (plugin, now + self._ttl)
        return nonce, self._ttl

    def consume(self, plugin: str, nonce: str) -> bool:
        """Validate and remove a nonce. Returns ``True`` iff it was valid.

        A nonce is valid if it is outstanding, was issued for *plugin*, and has
        not expired. Consumption is single-use: the entry is removed on the first
        attempt so a nonce cannot be replayed.

        Parameters
        ----------
        plugin : str
            Plugin name.
        nonce : str
            The nonce echoed back by the plugin.

        Returns
        -------
        bool
            Whether the nonce was outstanding, matching, and unexpired.
        """
        entry = self._pending.pop(nonce, None)
        if entry is None:
            return False
        stored_plugin, expires_at = entry
        if time.monotonic() > expires_at:
            return False
        return secrets.compare_digest(stored_plugin, plugin)

    def _sweep(self, now: float) -> None:
        """Drop expired nonces."""
        expired = [n for n, (_, exp) in self._pending.items() if now > exp]
        for n in expired:
            del self._pending[n]
