"""Tests for streaming server module."""

import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock picamera2 before importing to allow testing on non-RPi
mock_picamera2_module = MagicMock()
mock_picamera2_module.Picamera2 = MagicMock()
mock_picamera2_module.H264Encoder = MagicMock()
mock_picamera2_module.MJPEGEncoder = MagicMock()

mock_encoders = MagicMock()
mock_encoders.H264Encoder = MagicMock()
mock_encoders.MJPEGEncoder = MagicMock()
mock_picamera2_module.encoders = mock_encoders

sys.modules["picamera2"] = mock_picamera2_module
sys.modules["picamera2.encoders"] = mock_encoders

# Mock Flask
mock_flask_module = MagicMock()
mock_flask_module.Flask = MagicMock()
mock_flask_module.Response = MagicMock()
mock_flask_module.render_template_string = MagicMock()

sys.modules["flask"] = mock_flask_module

from nomothetic.streaming import StreamServer  # noqa: E402


class TestStreamServerInitialization:
    """Tests for StreamServer initialization."""

    @patch("nomothetic.streaming.Camera")
    @patch("nomothetic.streaming.Flask")
    def test_server_init_defaults(self, mock_flask, mock_camera):
        """Test server initialization with defaults."""
        mock_flask.return_value = MagicMock()
        mock_camera.return_value = MagicMock()

        server = StreamServer()

        assert server.host == "localhost"
        assert server.port == 8000
        assert server.width == 1280
        assert server.height == 720
        assert server.fps == 30
        assert server.encoder == "h264"

        # Verify Camera was initialized
        mock_camera.assert_called_once_with(
            camera_index=0,
            width=1280,
            height=720,
            fps=30,
            encoder="h264",
        )

    @patch("nomothetic.streaming.Camera")
    @patch("nomothetic.streaming.Flask")
    def test_server_init_custom_params(self, mock_flask, mock_camera):
        """Test server initialization with custom parameters."""
        mock_flask.return_value = MagicMock()
        mock_camera.return_value = MagicMock()

        server = StreamServer(
            host="0.0.0.0",
            port=9000,
            camera_index=1,
            width=1920,
            height=1080,
            fps=15,
            encoder="mjpeg",
        )

        assert server.host == "0.0.0.0"
        assert server.port == 9000
        assert server.width == 1920
        assert server.height == 1080
        assert server.fps == 15
        assert server.encoder == "mjpeg"

    @patch("nomothetic.streaming.Camera")
    @patch("nomothetic.streaming.Flask")
    def test_server_init_invalid_port_low(self, mock_flask, mock_camera):
        """Test server initialization with port too low."""
        mock_flask.return_value = MagicMock()
        mock_camera.return_value = MagicMock()

        with pytest.raises(ValueError, match="Port must be between"):
            StreamServer(port=0)

    @patch("nomothetic.streaming.Camera")
    @patch("nomothetic.streaming.Flask")
    def test_server_init_invalid_port_high(self, mock_flask, mock_camera):
        """Test server initialization with port too high."""
        mock_flask.return_value = MagicMock()
        mock_camera.return_value = MagicMock()

        with pytest.raises(ValueError, match="Port must be between"):
            StreamServer(port=65536)

    @patch("nomothetic.streaming.Flask", None)
    def test_server_init_flask_not_available(self):
        """Test server initialization when Flask is not available."""
        with pytest.raises(RuntimeError, match="Flask not available"):
            StreamServer()

    @patch("nomothetic.streaming.Camera")
    @patch("nomothetic.streaming.Flask")
    def test_server_repr(self, mock_flask, mock_camera):
        """Test server string representation."""
        mock_flask.return_value = MagicMock()
        mock_camera.return_value = MagicMock()

        server = StreamServer(
            host="0.0.0.0",
            port=9000,
            width=1920,
            height=1080,
            encoder="mjpeg",
        )

        repr_str = repr(server)
        assert "0.0.0.0" in repr_str
        assert "9000" in repr_str
        assert "1920" in repr_str
        assert "1080" in repr_str
        assert "mjpeg" in repr_str


class TestStreamServerFlaskSetup:
    """Tests for Flask app configuration."""

    @patch("nomothetic.streaming.Camera")
    @patch("nomothetic.streaming.Flask")
    def test_flask_routes_registered(self, mock_flask, mock_camera):
        """Test that Flask routes are registered."""
        mock_app = MagicMock()
        mock_flask.return_value = mock_app
        mock_camera.return_value = MagicMock()

        StreamServer()

        # Verify routes were added
        assert mock_app.add_url_rule.call_count == 2
        calls = mock_app.add_url_rule.call_args_list

        # Check for / route
        route_paths = [call[0][0] for call in calls]
        assert "/" in route_paths
        assert "/stream" in route_paths


class TestStreamServerViewerPage:
    """Tests for HTML viewer page rendering."""

    @patch("nomothetic.streaming.render_template_string")
    @patch("nomothetic.streaming.Camera")
    @patch("nomothetic.streaming.Flask")
    def test_viewer_renders_template(self, mock_flask, mock_camera, mock_render):
        """Test that viewer endpoint renders template."""
        mock_app = MagicMock()
        mock_flask.return_value = mock_app
        mock_camera.return_value = MagicMock()
        mock_render.return_value = "<html>test</html>"

        server = StreamServer(width=1920, height=1080, fps=24, encoder="mjpeg")

        # Call the viewer method directly
        result = server._viewer()

        assert result == "<html>test</html>"
        mock_render.assert_called_once()

        # Verify template was called with correct parameters
        call_kwargs = mock_render.call_args[1]
        assert call_kwargs["width"] == 1920
        assert call_kwargs["height"] == 1080
        assert call_kwargs["fps"] == 24
        assert call_kwargs["encoder"] == "mjpeg"


class TestStreamServerMJPEGStream:
    """Tests for MJPEG streaming endpoint."""

    @patch("nomothetic.streaming.Response")
    @patch("nomothetic.streaming.Camera")
    @patch("nomothetic.streaming.Flask")
    def test_stream_endpoint_returns_response(self, mock_flask, mock_camera, mock_response):
        """Test that stream endpoint returns Flask Response."""
        mock_app = MagicMock()
        mock_flask.return_value = mock_app
        mock_cam = MagicMock()
        mock_camera.return_value = mock_cam

        # Mock the frame generator
        mock_cam.get_frame_generator.return_value = iter([b"frame1", b"frame2"])

        server = StreamServer()

        # Call the stream endpoint
        server._stream_endpoint()

        # Verify Response was called with correct mimetype
        assert mock_response.called
        call_args = mock_response.call_args
        assert "multipart/x-mixed-replace" in str(call_args)
        assert "boundary=frame" in str(call_args)

    @patch("nomothetic.streaming.Camera")
    @patch("nomothetic.streaming.Flask")
    def test_stream_endpoint_frame_format(self, mock_flask, mock_camera):
        """Test that stream endpoint returns Response object."""
        mock_app = MagicMock()
        mock_flask.return_value = mock_app
        mock_cam = MagicMock()
        mock_camera.return_value = mock_cam

        # Create mock frame data
        frame_data = b"fake_jpeg_data"
        mock_cam.get_frame_generator.return_value = iter([frame_data])

        server = StreamServer()

        # The response is returned but the generator hasn't run yet
        # (it runs when the response is consumed by the client)
        response = server._stream_endpoint()
        assert response is not None


class TestStreamServerLifecycle:
    """Tests for server lifecycle management."""

    @patch("nomothetic.streaming.Camera")
    @patch("nomothetic.streaming.Flask")
    def test_close_cleans_up_camera(self, mock_flask, mock_camera):
        """Test that close() cleans up the camera."""
        mock_app = MagicMock()
        mock_flask.return_value = mock_app
        mock_cam = MagicMock()
        mock_camera.return_value = mock_cam

        server = StreamServer()
        server.close()

        mock_cam.close.assert_called_once()

    @patch("nomothetic.streaming.Camera")
    @patch("nomothetic.streaming.Flask")
    def test_background_thread_starts(self, mock_flask, mock_camera):
        """Test that background thread can be started."""
        mock_app = MagicMock()
        mock_flask.return_value = mock_app
        mock_cam = MagicMock()
        mock_camera.return_value = mock_cam

        server = StreamServer()

        # Mock the start method to avoid actually running the server
        with patch.object(server, "start"):
            thread = server.start_background()

            assert thread is not None
            assert hasattr(thread, "join")
            assert thread.daemon is True

    @patch("nomothetic.streaming.Camera")
    @patch("nomothetic.streaming.Flask")
    def test_start_calls_make_server(self, mock_flask, mock_camera):
        """Test that start() uses werkzeug make_server."""
        mock_app = MagicMock()
        mock_flask.return_value = mock_app
        mock_cam = MagicMock()
        mock_camera.return_value = mock_cam

        server = StreamServer(host="0.0.0.0", port=5000)

        mock_httpd = MagicMock()
        with patch("werkzeug.serving.make_server", return_value=mock_httpd) as mock_ms:
            server.start()

            mock_ms.assert_called_once_with("0.0.0.0", 5000, server.app)
            mock_httpd.serve_forever.assert_called_once()

    @patch("nomothetic.streaming.Camera")
    @patch("nomothetic.streaming.Flask")
    def test_start_with_debug(self, mock_flask, mock_camera):
        """Test that start() completes without error (debug param accepted but unused)."""
        mock_app = MagicMock()
        mock_flask.return_value = mock_app
        mock_cam = MagicMock()
        mock_camera.return_value = mock_cam

        server = StreamServer()

        mock_httpd = MagicMock()
        with patch("werkzeug.serving.make_server", return_value=mock_httpd):
            server.start(debug=True)
            mock_httpd.serve_forever.assert_called_once()
