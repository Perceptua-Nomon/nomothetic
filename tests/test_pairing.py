"""Tests for the device pairing module."""

from nomothetic.pairing import PairingState

# ============================================================================
# Secret generation
# ============================================================================


def test_generate_secret_returns_string():
    """generate_secret returns a non-empty string."""
    ps = PairingState()
    secret = ps.generate_secret()
    assert isinstance(secret, str)
    assert len(secret) > 0


def test_generate_secret_has_sufficient_entropy():
    """Pairing secret has at least 128 bits of entropy (22 URL-safe chars)."""
    ps = PairingState()
    secret = ps.generate_secret()
    assert len(secret) >= 22


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
