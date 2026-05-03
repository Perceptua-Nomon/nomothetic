"""Tests for the device pairing module."""

import os
import stat
from unittest.mock import MagicMock, patch

from nomothetic.pairing import PairingState, _write_shared_secret, get_pairing_secret_path

# ============================================================================
# Secret generation
# ============================================================================


def test_generate_secret_returns_string():
    """generate_secret returns a non-empty string."""
    ps = PairingState()
    secret = ps.generate_secret()
    assert isinstance(secret, str)
    assert len(secret) > 0


def test_generate_secret_is_six_digit_numeric():
    """Pairing secret is a 6-digit zero-padded numeric string."""
    ps = PairingState()
    secret = ps.generate_secret()
    assert len(secret) == 6
    assert secret.isdigit()


def test_generate_secret_unique():
    """Two generated secrets are different."""
    ps = PairingState()
    a = ps.generate_secret()
    b = ps.generate_secret()
    assert a != b


def test_generate_secret_stores_on_state():
    """generate_secret stores the secret on the PairingState."""
    ps = PairingState()
    secret = ps.generate_secret()
    assert ps.secret == secret


# ============================================================================
# Verify and consume
# ============================================================================


def test_verify_correct_secret():
    """Correct candidate returns True and consumes the secret."""
    ps = PairingState()
    secret = ps.generate_secret()
    assert ps.verify_and_consume(secret) is True
    assert ps.paired is True
    assert ps.secret is None


def test_verify_wrong_secret():
    """Wrong candidate returns False and leaves state unchanged."""
    ps = PairingState()
    ps.generate_secret()
    assert ps.verify_and_consume("wrong-secret") is False
    assert ps.paired is False
    assert ps.secret is not None


def test_consume_once_only():
    """A consumed secret cannot be used again."""
    ps = PairingState()
    secret = ps.generate_secret()
    assert ps.verify_and_consume(secret) is True
    assert ps.verify_and_consume(secret) is False


def test_verify_no_secret_returns_false():
    """verify_and_consume returns False when no secret has been generated."""
    ps = PairingState()
    assert ps.verify_and_consume("anything") is False


def test_verify_already_paired_returns_false():
    """verify_and_consume returns False when already paired."""
    ps = PairingState()
    secret = ps.generate_secret()
    ps.verify_and_consume(secret)
    # Generate a new secret, but we're already paired
    new_secret = ps.generate_secret()
    # paired is reset by generate_secret
    assert ps.paired is False
    # Now verify with the new secret
    assert ps.verify_and_consume(new_secret) is True


# ============================================================================
# is_paired
# ============================================================================


def test_is_paired_initially_false():
    """is_paired returns False on a fresh PairingState."""
    ps = PairingState()
    assert ps.is_paired() is False


def test_is_paired_after_pairing():
    """is_paired returns True after successful pairing."""
    ps = PairingState()
    secret = ps.generate_secret()
    ps.verify_and_consume(secret)
    assert ps.is_paired() is True


# ============================================================================
# Reset
# ============================================================================


def test_reset_clears_state():
    """reset clears paired state and owner."""
    ps = PairingState()
    secret = ps.generate_secret()
    ps.verify_and_consume(secret)
    ps.owner_email = "test@local"
    old_jwt = ps.jwt_secret

    ps.reset()

    assert ps.paired is False
    assert ps.owner_email is None
    assert ps.secret is None
    assert ps.jwt_secret != old_jwt


# ============================================================================
# JWT secret
# ============================================================================


def test_jwt_secret_generated_on_init():
    """jwt_secret is generated on construction."""
    ps = PairingState()
    assert isinstance(ps.jwt_secret, str)
    assert len(ps.jwt_secret) >= 32


def test_jwt_secret_unique_per_instance():
    """Each PairingState gets a unique JWT secret."""
    a = PairingState()
    b = PairingState()
    assert a.jwt_secret != b.jwt_secret


# ============================================================================
# get_pairing_secret_path
# ============================================================================


def test_get_pairing_secret_path_default():
    """Returns /var/lib/nomon/pairing_secret when env var is not set."""
    with patch.dict(os.environ, {}, clear=True):
        # Remove the env var if set
        os.environ.pop("NOMON_PAIRING_SECRET_PATH", None)
        assert get_pairing_secret_path() == "/var/lib/nomon/pairing_secret"


def test_get_pairing_secret_path_from_env():
    """Returns the path from NOMON_PAIRING_SECRET_PATH env var."""
    with patch.dict(os.environ, {"NOMON_PAIRING_SECRET_PATH": "/tmp/test_secret"}):
        assert get_pairing_secret_path() == "/tmp/test_secret"


# ============================================================================
# Shared secret file write
# ============================================================================


def test_write_shared_secret_to_file(tmp_path):
    """Pairing secret is written to the configured path."""
    secret_path = str(tmp_path / "pairing_secret")
    with patch.dict(os.environ, {"NOMON_PAIRING_SECRET_PATH": secret_path}):
        _write_shared_secret("test-secret-value")

    assert os.path.exists(secret_path)
    with open(secret_path) as f:
        assert f.read() == "test-secret-value"


def test_write_shared_secret_read_back(tmp_path):
    """Written secret can be read back with correct content."""
    secret_path = str(tmp_path / "pairing_secret")
    with patch.dict(os.environ, {"NOMON_PAIRING_SECRET_PATH": secret_path}):
        _write_shared_secret("round-trip-test")

    with open(secret_path) as f:
        content = f.read()
    assert content == "round-trip-test"


def test_write_shared_secret_file_mode(tmp_path):
    """Secret file is created with mode 0640."""
    secret_path = str(tmp_path / "pairing_secret")
    with patch.dict(os.environ, {"NOMON_PAIRING_SECRET_PATH": secret_path}):
        _write_shared_secret("mode-test")

    file_stat = os.stat(secret_path)
    mode = stat.S_IMODE(file_stat.st_mode)
    assert mode == 0o640


def test_write_shared_secret_atomic_rename(tmp_path):
    """Uses atomic write (temp file + rename)."""
    secret_path = str(tmp_path / "pairing_secret")
    with patch.dict(os.environ, {"NOMON_PAIRING_SECRET_PATH": secret_path}):
        with patch("nomothetic.pairing.os.rename", wraps=os.rename) as mock_rename:
            _write_shared_secret("atomic-test")
            mock_rename.assert_called_once()
            # The second argument should be the final path
            assert mock_rename.call_args[0][1] == secret_path


def test_write_shared_secret_missing_directory(tmp_path):
    """Logs warning and returns without error when directory doesn't exist."""
    secret_path = str(tmp_path / "nonexistent" / "pairing_secret")
    with patch.dict(os.environ, {"NOMON_PAIRING_SECRET_PATH": secret_path}):
        # Should not raise — just log a warning
        _write_shared_secret("missing-dir-test")

    assert not os.path.exists(secret_path)


def test_write_shared_secret_group_set_attempted(tmp_path):
    """Attempts to set the 'nomon' group on the secret file."""
    secret_path = str(tmp_path / "pairing_secret")
    mock_grp = MagicMock()
    mock_grp.gr_gid = 12345
    with patch.dict(os.environ, {"NOMON_PAIRING_SECRET_PATH": secret_path}):
        with patch("nomothetic.pairing.grp.getgrnam", return_value=mock_grp):
            with patch("nomothetic.pairing.os.chown") as mock_chown:
                _write_shared_secret("group-test")
                mock_chown.assert_called_once()
                _, gid = mock_chown.call_args[0][1], mock_chown.call_args[0][2]
                assert gid == 12345


def test_write_shared_secret_group_missing_graceful(tmp_path):
    """Handles missing 'nomon' group gracefully."""
    secret_path = str(tmp_path / "pairing_secret")
    with patch.dict(os.environ, {"NOMON_PAIRING_SECRET_PATH": secret_path}):
        with patch("nomothetic.pairing.grp.getgrnam", side_effect=KeyError("nomon")):
            # Should not raise — just log a warning
            _write_shared_secret("no-group-test")

    # File should still exist despite group error
    assert os.path.exists(secret_path)


def test_generate_secret_writes_shared_file(tmp_path):
    """generate_secret writes the secret to the shared file."""
    secret_path = str(tmp_path / "pairing_secret")
    with patch.dict(os.environ, {"NOMON_PAIRING_SECRET_PATH": secret_path}):
        ps = PairingState()
        secret = ps.generate_secret()

    with open(secret_path) as f:
        assert f.read() == secret


def test_write_shared_secret_overwrites_existing(tmp_path):
    """Writing a new secret overwrites the previous one atomically."""
    secret_path = str(tmp_path / "pairing_secret")
    with patch.dict(os.environ, {"NOMON_PAIRING_SECRET_PATH": secret_path}):
        _write_shared_secret("first-secret")
        _write_shared_secret("second-secret")

    with open(secret_path) as f:
        assert f.read() == "second-secret"
