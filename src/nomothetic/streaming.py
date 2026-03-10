"""Web streaming server for Raspberry Pi camera.

This module provides an HTTP server for streaming live camera
feed via MJPEG (Motion JPEG) protocol. The stream can be viewed
in any web browser without external plugins.

Classes
-------
StreamServer
    HTTP server for serving live camera MJPEG stream.
"""

import threading
from typing import Optional

try:
    from flask import Flask, Response, render_template_string
except ImportError:
    Flask = None  # type: ignore
    Response = None  # type: ignore
    render_template_string = None  # type: ignore

from nomothetic.camera import Camera

# HTML template for the viewer page
VIEWER_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nomon Camera Stream</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
                         Roboto, 'Helvetica Neue', Arial, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            background-color: #1a1a1a;
            color: #fff;
        }
        .container {
            text-align: center;
            padding: 20px;
            max-width: 1000px;
        }
        h1 {
            margin-top: 0;
            font-size: 28px;
        }
        .stream-wrapper {
            background-color: #000;
            border-radius: 8px;
            padding: 10px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
            margin: 20px 0;
        }
        .stream-wrapper img {
            max-width: 100%;
            height: auto;
            border-radius: 4px;
            display: block;
        }
        .info {
            font-size: 14px;
            color: #999;
            margin-top: 15px;
        }
        .info p {
            margin: 5px 0;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎥 Nomon Camera Stream</h1>
        <div class="stream-wrapper">
            <img src="/stream" alt="Camera Stream">
        </div>
        <div class="info">
            <p>Resolution: {{ width }}x{{ height }}</p>
            <p>Frame Rate: {{ fps }} fps</p>
            <p>Encoder: {{ encoder }}</p>
        </div>
    </div>
</body>
</html>
"""


class StreamServer:
    """HTTP server for Raspberry Pi camera MJPEG streaming.

    Provides a simple web interface to view live camera feed via
    MJPEG (Motion JPEG) protocol over HTTP. Works in any browser
    without plugins or external libraries.

    The server creates a Camera instance internally and streams
    frames via the `/stream` endpoint. The root `/` endpoint
    serves an HTML viewer page.

    Parameters
    ----------
    host : str, optional
        Host to bind to (default: "localhost")
    port : int, optional
        Port to bind to (default: 8000)
    camera_index : int, optional
        Camera index to use (default: 0)
    width : int, optional
        Capture width in pixels (default: 1280)
    height : int, optional
        Capture height in pixels (default: 720)
    fps : int, optional
        Frames per second (default: 30)
    encoder : str, optional
        Video encoder: 'h264' or 'mjpeg' (default: 'h264')
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8000,
        camera_index: int = 0,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        encoder: str = "h264",
    ) -> None:
        """Initialize the streaming server.

        Parameters
        ----------
        host : str, optional
            Host to bind to (default: "localhost")
        port : int, optional
            Port to bind to (default: 8000)
        camera_index : int, optional
            Camera index to use (default: 0)
        width : int, optional
            Capture width in pixels (default: 1280)
        height : int, optional
            Capture height in pixels (default: 720)
        fps : int, optional
            Frames per second (default: 30)
        encoder : str, optional
            Video encoder: 'h264' or 'mjpeg' (default: 'h264')

        Raises
        ------
        RuntimeError
            If Flask is not installed
        ValueError
            If port is not in valid range (1-65535)
        """
        if Flask is None:
            raise RuntimeError(
                "Flask not available. " "Install with: pip install 'nomothetic[web]'"
            )

        if not 1 <= port <= 65535:
            raise ValueError(f"Port must be between 1 and 65535, got {port}")

        self.host = host
        self.port = port
        self.width = width
        self.height = height
        self.fps = fps
        self.encoder = encoder

        # Create camera instance
        self.camera = Camera(
            camera_index=camera_index,
            width=width,
            height=height,
            fps=fps,
            encoder=encoder,
        )

        # Thread synchronization for frame sharing
        self._frame_lock = threading.Lock()
        self._current_frame: Optional[bytes] = None

        # Create Flask app
        self.app = Flask(__name__)
        self.app.add_url_rule("/", "viewer", self._viewer)
        self.app.add_url_rule("/stream", "stream", self._stream_endpoint)

    def _viewer(self) -> str:
        """Serve the HTML viewer page.

        Returns
        -------
        str
            Rendered HTML template with camera parameters
        """
        return render_template_string(
            VIEWER_TEMPLATE,
            width=self.width,
            height=self.height,
            fps=self.fps,
            encoder=self.encoder,
        )

    def _stream_endpoint(self) -> Response:
        """Stream MJPEG frames to the client.

        Returns
        -------
        Response
            Flask response with multipart/x-mixed-replace content type
            that streams JPEG frames continuously
        """

        def generate():
            """Generator that yields MJPEG boundary data."""
            try:
                for jpeg_frame in self.camera.get_jpeg_frame_generator():
                    # Wrap each frame in MJPEG boundary
                    boundary = b"--frame"
                    content_type = b"Content-Type: image/jpeg"
                    content_length = b"Content-Length: " + str(len(jpeg_frame)).encode()
                    crlf = b"\r\n"

                    yield boundary + crlf
                    yield content_type + crlf
                    yield content_length + crlf
                    yield crlf
                    yield jpeg_frame
                    yield crlf
            except GeneratorExit:
                # Client disconnected, clean up
                pass
            except Exception as e:
                # Log error but continue (client may have disconnected)
                print(f"Stream error: {e}")

        return Response(
            generate(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )

    def start(self, debug: bool = False) -> None:
        """Start the streaming server (blocking).

        Parameters
        ----------
        debug : bool, optional
            Enable Flask debug mode (default: False).
            Note: Not recommended for production.

        Notes
        -----
        This method blocks until the server is stopped.
        Navigate to http://localhost:8000 (or configured host:port)
        to view the stream.
        """
        try:
            self.app.run(
                host=self.host,
                port=self.port,
                debug=debug,
                use_reloader=False,
            )
        except KeyboardInterrupt:
            # Handle Ctrl+C gracefully
            pass
        finally:
            self.close()

    def start_background(self) -> threading.Thread:
        """Start the streaming server in a background thread.

        Returns
        -------
        threading.Thread
            The thread running the server. Call join() to wait
            for it to complete.

        Notes
        -----
        This is useful for testing or running the server
        alongside other code. The server will continue running
        until close() is called or the main program exits.
        """
        thread = threading.Thread(
            target=self.start,
            kwargs={"debug": False},
            daemon=True,
        )
        thread.start()
        return thread

    def close(self) -> None:
        """Clean up and close the server.

        Closes the camera and releases resources.
        """
        self.camera.close()

    def __repr__(self) -> str:
        """Return string representation of server."""
        return (
            f"StreamServer(host={self.host}, port={self.port}, "
            f"resolution={self.width}x{self.height}, "
            f"fps={self.fps}, encoder={self.encoder})"
        )
