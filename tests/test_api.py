"""Tests for the HTTP REST API module.

Tests cover endpoint functionality, error handling, and CORS behavior.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from nomothetic.api import APIServer, create_app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def mock_camera():
    """Create a mock camera for testing."""
    camera = MagicMock()
    camera.width = 1280
    camera.height = 720
    camera.fps = 30
    camera.encoder = "h264"
    camera._is_recording = False
    return camera


# ============================================================================
# Health & Status Endpoints
# ============================================================================


def test_health_check(client):
    """Test health check endpoint returns success."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "nomon-camera-api"
    assert "version" in data


def test_camera_status_without_camera(client):
    """Test camera status endpoint without initialized camera."""
    response = client.get("/api/camera/status")
    assert response.status_code == 500
    assert "not initialized" in response.json()["error"]


def test_camera_status_with_camera(client, mock_camera):
    """Test camera status endpoint returns current state."""
    import nomothetic.api

    nomothetic.api._camera = mock_camera

    response = client.get("/api/camera/status")
    assert response.status_code == 200
    data = response.json()
    assert data["camera_ready"] is True
    assert data["recording"] is False
    assert data["resolution"] == "1280x720"
    assert data["fps"] == 30
    assert data["encoder"] == "h264"
    assert "timestamp" in data

    # Cleanup
    nomothetic.api._camera = None


def test_camera_status_recording(client, mock_camera):
    """Test camera status reflects recording state."""
    import nomothetic.api

    mock_camera._is_recording = True
    nomothetic.api._camera = mock_camera

    response = client.get("/api/camera/status")
    assert response.status_code == 200
    assert response.json()["recording"] is True

    # Cleanup
    nomothetic.api._camera = None


# ============================================================================
# Image Capture Endpoints
# ============================================================================


def test_capture_image_success(client, mock_camera):
    """Test successful image capture."""
    import nomothetic.api

    nomothetic.api._camera = mock_camera

    response = client.post(
        "/api/camera/capture",
        json={"filename": "test.jpg"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["filename"] == "test.jpg"
    assert "timestamp" in data
    mock_camera.capture_image.assert_called_once_with("test.jpg")

    # Cleanup
    nomothetic.api._camera = None


def test_capture_image_invalid_filename(client, mock_camera):
    """Test capture with invalid filename raises error."""
    import nomothetic.api

    mock_camera.capture_image.side_effect = ValueError("Invalid filename")
    nomothetic.api._camera = mock_camera

    response = client.post(
        "/api/camera/capture",
        json={"filename": "../etc/passwd"},
    )
    assert response.status_code == 400
    assert "Invalid filename" in response.json()["error"]

    # Cleanup
    nomothetic.api._camera = None


def test_capture_image_camera_error(client, mock_camera):
    """Test capture with camera error."""
    import nomothetic.api

    mock_camera.capture_image.side_effect = RuntimeError("Camera failed")
    nomothetic.api._camera = mock_camera

    response = client.post(
        "/api/camera/capture",
        json={"filename": "photo.jpg"},
    )
    assert response.status_code == 500
    assert "Camera failed" in response.json()["error"]

    # Cleanup
    nomothetic.api._camera = None


def test_capture_without_camera(client):
    """Test capture endpoint without initialized camera."""
    response = client.post(
        "/api/camera/capture",
        json={"filename": "test.jpg"},
    )
    assert response.status_code == 500
    assert "not initialized" in response.json()["error"]


# ============================================================================
# Video Recording Endpoints
# ============================================================================


def test_record_start_success(client, mock_camera):
    """Test successful recording start."""
    import nomothetic.api

    nomothetic.api._camera = mock_camera

    response = client.post(
        "/api/camera/record/start",
        json={"filename": "video.mp4"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["filename"] == "video.mp4"
    assert "timestamp" in data
    mock_camera.start_recording.assert_called_once_with("video.mp4")

    # Cleanup
    nomothetic.api._camera = None


def test_record_start_with_encoder(client, mock_camera):
    """Test recording start with encoder specification."""
    import nomothetic.api

    nomothetic.api._camera = mock_camera

    response = client.post(
        "/api/camera/record/start",
        json={"filename": "video.mp4", "encoder": "mjpeg"},
    )
    assert response.status_code == 200
    # Encoder should be updated
    assert mock_camera.encoder == "mjpeg"

    # Cleanup
    nomothetic.api._camera = None


def test_record_start_already_recording(client, mock_camera):
    """Test recording start when already recording."""
    import nomothetic.api

    mock_camera._is_recording = True
    nomothetic.api._camera = mock_camera

    response = client.post(
        "/api/camera/record/start",
        json={"filename": "video.mp4"},
    )
    assert response.status_code == 409
    assert "Recording already in progress" in response.json()["error"]

    # Cleanup
    nomothetic.api._camera = None


def test_record_start_invalid_filename(client, mock_camera):
    """Test recording start with invalid filename."""
    import nomothetic.api

    mock_camera.start_recording.side_effect = ValueError("Invalid filename")
    nomothetic.api._camera = mock_camera

    response = client.post(
        "/api/camera/record/start",
        json={"filename": "../etc/passwd"},
    )
    assert response.status_code == 400

    # Cleanup
    nomothetic.api._camera = None


def test_record_start_without_camera(client):
    """Test record start without initialized camera."""
    response = client.post(
        "/api/camera/record/start",
        json={"filename": "video.mp4"},
    )
    assert response.status_code == 500
    assert "not initialized" in response.json()["error"]


def test_record_stop_success(client, mock_camera):
    """Test successful recording stop."""
    import nomothetic.api

    mock_camera._is_recording = True
    nomothetic.api._camera = mock_camera

    response = client.post("/api/camera/record/stop")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "timestamp" in data
    mock_camera.stop_recording.assert_called_once()

    # Cleanup
    nomothetic.api._camera = None


def test_record_stop_not_recording(client, mock_camera):
    """Test recording stop when not recording."""
    import nomothetic.api

    nomothetic.api._camera = mock_camera

    response = client.post("/api/camera/record/stop")
    assert response.status_code == 409
    assert "No recording in progress" in response.json()["error"]

    # Cleanup
    nomothetic.api._camera = None


def test_record_stop_camera_error(client, mock_camera):
    """Test recording stop with camera error."""
    import nomothetic.api

    mock_camera._is_recording = True
    mock_camera.stop_recording.side_effect = RuntimeError("Stop failed")
    nomothetic.api._camera = mock_camera

    response = client.post("/api/camera/record/stop")
    assert response.status_code == 500
    assert "Stop failed" in response.json()["error"]

    # Cleanup
    nomothetic.api._camera = None


def test_record_stop_without_camera(client):
    """Test record stop without initialized camera."""
    response = client.post("/api/camera/record/stop")
    assert response.status_code == 500
    assert "not initialized" in response.json()["error"]


# ============================================================================
# CORS Headers
# ============================================================================


def test_cors_middleware_configured(client):
    """Test that middleware is configured."""
    app = client.app
    # Check that app has middleware
    assert len(app.user_middleware) > 0


# ============================================================================
# API Server Configuration
# ============================================================================


def test_api_server_initialization():
    """Test APIServer initialization with defaults."""
    server = APIServer()
    assert server.host == "127.0.0.1"
    assert server.port == 8443
    assert server.use_ssl is True


def test_api_server_custom_host_port():
    """Test APIServer with custom host and port."""
    server = APIServer(host="0.0.0.0", port=9000)
    assert server.host == "0.0.0.0"
    assert server.port == 9000


def test_api_server_invalid_port():
    """Test APIServer rejects invalid ports."""
    with pytest.raises(ValueError, match="Invalid port"):
        APIServer(port=0)

    with pytest.raises(ValueError, match="Invalid port"):
        APIServer(port=70000)


def test_api_server_get_config():
    """Test APIServer configuration generation."""
    server = APIServer(host="localhost", port=8000, use_ssl=False)
    config = server.get_config()
    assert config["host"] == "localhost"
    assert config["port"] == 8000
    assert "ssl_certfile" not in config


def test_api_server_get_config_with_ssl():
    """Test APIServer configuration with SSL."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        server = APIServer(port=8443, use_ssl=True, cert_dir=Path(tmpdir))
        config = server.get_config()
        assert "ssl_certfile" in config
        assert "ssl_keyfile" in config


# ============================================================================
# Request/Response Models
# ============================================================================


def test_capture_request_model():
    """Test CaptureRequest validation."""
    from nomothetic.api import CaptureRequest

    req = CaptureRequest(filename="test.jpg")
    assert req.filename == "test.jpg"


def test_record_request_model():
    """Test RecordRequest validation."""
    from nomothetic.api import RecordRequest

    req = RecordRequest(filename="video.mp4")
    assert req.filename == "video.mp4"
    assert req.encoder == "h264"

    req2 = RecordRequest(filename="video.mp4", encoder="mjpeg")
    assert req2.encoder == "mjpeg"


def test_camera_status_model():
    """Test CameraStatus model."""
    from nomothetic.api import CameraStatus

    status = CameraStatus(
        camera_ready=True,
        recording=False,
        resolution="1280x720",
        fps=30,
        encoder="h264",
        timestamp="2024-01-01T00:00:00",
    )
    assert status.camera_ready is True
    assert status.recording is False


# ============================================================================
# HAT Endpoints
# ============================================================================


@pytest.fixture
def mock_hat():
    """Create a mock HatClient for HAT endpoint tests."""
    return MagicMock()


def test_get_battery_no_client(client):
    """GET /api/hat/battery returns 503 when _hat_client is None."""
    import nomothetic.api

    nomothetic.api._hat_client = None
    response = client.get("/api/hat/battery")
    assert response.status_code == 503
    assert "not available" in response.json()["error"]


def test_get_battery_success(client, mock_hat):
    """GET /api/hat/battery returns voltage and timestamp on success."""
    import nomothetic.api

    mock_hat.get_battery_voltage.return_value = 7.42
    nomothetic.api._hat_client = mock_hat

    response = client.get("/api/hat/battery")
    assert response.status_code == 200
    data = response.json()
    assert data["voltage_v"] == pytest.approx(7.42)
    assert "timestamp" in data

    nomothetic.api._hat_client = None


def test_get_battery_connection_error(client, mock_hat):
    """GET /api/hat/battery returns 503 on HatConnectionError."""
    import nomothetic.api
    from nomothetic.hat import HatConnectionError

    mock_hat.get_battery_voltage.side_effect = HatConnectionError("socket gone")
    nomothetic.api._hat_client = mock_hat

    response = client.get("/api/hat/battery")
    assert response.status_code == 503

    nomothetic.api._hat_client = None


def test_get_battery_hardware_error(client, mock_hat):
    """GET /api/hat/battery returns 500 on HatError (hardware failure)."""
    import nomothetic.api
    from nomothetic.hat import HatError

    mock_hat.get_battery_voltage.side_effect = HatError("HARDWARE_ERROR", "I2C failed")
    nomothetic.api._hat_client = mock_hat

    response = client.get("/api/hat/battery")
    assert response.status_code == 500

    nomothetic.api._hat_client = None


def test_set_servo_no_client(client):
    """POST /api/hat/servo returns 503 when _hat_client is None."""
    import nomothetic.api

    nomothetic.api._hat_client = None
    response = client.post("/api/hat/servo", json={"channel": 0, "angle_deg": 90.0})
    assert response.status_code == 503


def test_set_servo_success(client, mock_hat):
    """POST /api/hat/servo returns channel, angle, and timestamp on success."""
    import nomothetic.api

    mock_hat.set_servo_angle.return_value = None
    nomothetic.api._hat_client = mock_hat

    response = client.post("/api/hat/servo", json={"channel": 2, "angle_deg": 45.0})
    assert response.status_code == 200
    data = response.json()
    assert data["channel"] == 2
    assert data["angle_deg"] == pytest.approx(45.0)
    assert "timestamp" in data

    nomothetic.api._hat_client = None


def test_set_servo_invalid_channel(client, mock_hat):
    """POST /api/hat/servo returns 422 when channel is out of range."""
    import nomothetic.api

    nomothetic.api._hat_client = mock_hat
    response = client.post("/api/hat/servo", json={"channel": 12, "angle_deg": 90.0})
    assert response.status_code == 422

    nomothetic.api._hat_client = None


def test_set_servo_invalid_angle(client, mock_hat):
    """POST /api/hat/servo returns 422 when angle_deg is out of range."""
    import nomothetic.api

    nomothetic.api._hat_client = mock_hat
    response = client.post("/api/hat/servo", json={"channel": 0, "angle_deg": 200.0})
    assert response.status_code == 422

    nomothetic.api._hat_client = None


def test_set_servo_connection_error(client, mock_hat):
    """POST /api/hat/servo returns 503 on HatConnectionError."""
    import nomothetic.api
    from nomothetic.hat import HatConnectionError

    mock_hat.set_servo_angle.side_effect = HatConnectionError("daemon gone")
    nomothetic.api._hat_client = mock_hat

    response = client.post("/api/hat/servo", json={"channel": 0, "angle_deg": 90.0})
    assert response.status_code == 503

    nomothetic.api._hat_client = None


def test_reset_mcu_no_client(client):
    """POST /api/hat/reset returns 503 when _hat_client is None."""
    import nomothetic.api

    nomothetic.api._hat_client = None
    response = client.post("/api/hat/reset")
    assert response.status_code == 503


def test_reset_mcu_success(client, mock_hat):
    """POST /api/hat/reset returns success and timestamp."""
    import nomothetic.api

    mock_hat.reset_mcu.return_value = None
    nomothetic.api._hat_client = mock_hat

    response = client.post("/api/hat/reset")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "timestamp" in data

    nomothetic.api._hat_client = None
