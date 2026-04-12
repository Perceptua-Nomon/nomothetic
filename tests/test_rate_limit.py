"""Tests for the rate_limit module.

Covers basic limiting, window expiry, multiple IPs, and reset behaviour.
"""

import time
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from nomothetic.rate_limit import RateLimiter, _client_ip

# ============================================================================
# RateLimiter unit tests
# ============================================================================


class TestRateLimiterBasic:
    """Core rate-limiting behaviour."""

    def test_allows_requests_under_limit(self):
        """Requests under the limit should pass without error."""
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            limiter.check("10.0.0.1")

    def test_blocks_requests_over_limit(self):
        """Exceeding the limit raises 429."""
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        limiter.check("10.0.0.1")
        limiter.check("10.0.0.1")
        with pytest.raises(HTTPException) as exc_info:
            limiter.check("10.0.0.1")
        assert exc_info.value.status_code == 429

    def test_different_keys_independent(self):
        """Different IPs have independent counters."""
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        limiter.check("10.0.0.1")
        # Second IP should still be allowed
        limiter.check("10.0.0.2")
        # First IP should be blocked
        with pytest.raises(HTTPException):
            limiter.check("10.0.0.1")


class TestRateLimiterWindowExpiry:
    """Sliding-window expiration."""

    def test_requests_expire_after_window(self):
        """Old requests outside the window are discarded."""
        limiter = RateLimiter(max_requests=1, window_seconds=1)
        limiter.check("10.0.0.1")

        # Simulate time passing beyond the window
        with patch("nomothetic.rate_limit.time.monotonic", return_value=time.monotonic() + 2):
            # Should be allowed again after window expires
            limiter.check("10.0.0.1")


class TestRateLimiterReset:
    """Reset behaviour."""

    def test_reset_clears_all_state(self):
        """After reset, all counters are cleared."""
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        limiter.check("10.0.0.1")
        with pytest.raises(HTTPException):
            limiter.check("10.0.0.1")

        limiter.reset()
        # Should work again after reset
        limiter.check("10.0.0.1")

    def test_reset_clears_all_keys(self):
        """Reset clears state for all IPs, not just one."""
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        limiter.check("10.0.0.1")
        limiter.check("10.0.0.2")

        limiter.reset()
        limiter.check("10.0.0.1")
        limiter.check("10.0.0.2")


# ============================================================================
# _client_ip tests
# ============================================================================


class TestClientIp:
    """Client IP extraction logic."""

    def _make_request(self, host="127.0.0.1", forwarded_for=None):
        """Create a minimal mock request."""
        from unittest.mock import MagicMock

        request = MagicMock()
        request.client.host = host
        if forwarded_for is not None:
            request.headers.get.return_value = forwarded_for
        else:
            request.headers.get.return_value = None
        return request

    def test_uses_client_host_by_default(self):
        """Without NOMON_TRUST_PROXY, uses request.client.host."""
        request = self._make_request(host="192.168.1.1", forwarded_for="10.0.0.1")
        with patch.dict("os.environ", {}, clear=False):
            # Ensure NOMON_TRUST_PROXY is not set
            import os

            os.environ.pop("NOMON_TRUST_PROXY", None)
            assert _client_ip(request) == "192.168.1.1"

    def test_trusts_forwarded_for_when_enabled(self):
        """With NOMON_TRUST_PROXY=true, reads X-Forwarded-For."""
        request = self._make_request(host="192.168.1.1", forwarded_for="10.0.0.1")
        with patch.dict("os.environ", {"NOMON_TRUST_PROXY": "true"}):
            assert _client_ip(request) == "10.0.0.1"

    def test_falls_back_to_host_when_no_header(self):
        """With NOMON_TRUST_PROXY=true but no header, falls back to host."""
        request = self._make_request(host="192.168.1.1", forwarded_for=None)
        with patch.dict("os.environ", {"NOMON_TRUST_PROXY": "true"}):
            assert _client_ip(request) == "192.168.1.1"

    def test_ignores_header_when_trust_disabled(self):
        """With NOMON_TRUST_PROXY=false, ignores X-Forwarded-For."""
        request = self._make_request(host="192.168.1.1", forwarded_for="10.0.0.1")
        with patch.dict("os.environ", {"NOMON_TRUST_PROXY": "false"}):
            assert _client_ip(request) == "192.168.1.1"

    def test_handles_multiple_forwarded_ips(self):
        """Takes first IP from comma-separated X-Forwarded-For."""
        request = self._make_request(
            host="192.168.1.1", forwarded_for="10.0.0.1, 10.0.0.2, 10.0.0.3"
        )
        with patch.dict("os.environ", {"NOMON_TRUST_PROXY": "1"}):
            assert _client_ip(request) == "10.0.0.1"

    def test_handles_no_client(self):
        """Returns 'unknown' when request.client is None."""
        from unittest.mock import MagicMock

        request = MagicMock()
        request.client = None
        request.headers.get.return_value = None
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("NOMON_TRUST_PROXY", None)
            assert _client_ip(request) == "unknown"
