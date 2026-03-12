"""HTTP REST API for camera control and monitoring.

This module provides a FastAPI-based REST API for remote camera
operations with HTTPS/TLS support and CORS for mobile clients.

Classes
-------
APIServer
    Manages the FastAPI application and uvicorn server lifecycle.

Functions
---------
create_app
    Factory function to create a configured FastAPI application.
create_self_signed_cert
    Generate self-signed TLS certificates for development/testing.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from nomothetic.audio import AudioPlayer, AudioRecorder, list_audio_files
from nomothetic.camera import Camera
from nomothetic.hat import HatClient, HatConnectionError, HatError
try:
    from nomothetic.streaming import StreamServer
except ImportError:
    class StreamServer:  # type: ignore[no-redef]
        """Placeholder StreamServer used when Flask/web streaming is unavailable.

        This fallback ensures that the API module remains importable even if the
        optional Flask-based streaming stack is not installed. Any attempt to
        instantiate this class will raise a RuntimeError with guidance on how
        to enable streaming support.
        """

        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError(
                "nomothetic.streaming.StreamServer requires Flask. "
                "Install the 'nomothetic[web]' extra to enable streaming endpoints."
            )
logger = logging.getLogger(__name__)

# ============================================================================
# Data Models
# ============================================================================


class CaptureRequest(BaseModel):
    """Still image capture request."""

    filename: str = Field(..., description="Filename for captured image (no path)")


class CaptureResponse(BaseModel):
    """Successful still image capture response."""

    success: bool
    filename: str
    timestamp: str
    message: str


class RecordRequest(BaseModel):
    """Video recording request."""

    filename: str = Field(..., description="Filename for video (no path)")
    encoder: Optional[str] = Field(default="h264", description="Video encoder: h264 or mjpeg")


class RecordStartResponse(BaseModel):
    """Video recording start response."""

    success: bool
    filename: str
    timestamp: str
    message: str


class RecordStopResponse(BaseModel):
    """Video recording stop response."""

    success: bool
    timestamp: str
    message: str


class CameraStatus(BaseModel):
    """Current camera and recording status."""

    camera_ready: bool
    recording: bool
    resolution: str
    fps: int
    encoder: str
    timestamp: str


class ErrorResponse(BaseModel):
    """Standard error response."""

    success: bool = False
    error: str
    timestamp: str


class BatteryResponse(BaseModel):
    """HAT battery voltage response."""

    voltage_v: float
    timestamp: str


class ServoRequest(BaseModel):
    """Servo angle control request."""

    channel: int = Field(..., ge=0, le=11, description="PWM channel (0–11)")
    angle_deg: float = Field(..., ge=0.0, le=180.0, description="Target angle (0–180°)")
    ttl_ms: int = Field(default=500, ge=100, le=5000, description="Lease TTL in ms")


class ServoResponse(BaseModel):
    """Servo angle control response."""

    channel: int
    angle_deg: float
    timestamp: str


class ResetResponse(BaseModel):
    """MCU reset response."""

    success: bool
    timestamp: str


class ServoLeaseItem(BaseModel):
    """A single active servo TTL lease entry."""

    channel: int
    ttl_remaining_ms: int
    conn_id: int


class ServoStatusResponse(BaseModel):
    """Active servo lease table response."""

    active_leases: list[ServoLeaseItem]
    timestamp: str


class McuStatusResponse(BaseModel):
    """MCU reset statistics response."""

    resets_since_start: int
    last_reset_s_ago: Optional[int]
    timestamp: str


class MotorRequest(BaseModel):
    """DC motor speed control request."""

    channel: int = Field(..., ge=0, le=3, description="Motor channel index (0–3)")
    speed_pct: float = Field(
        ..., ge=-100.0, le=100.0, description="Signed speed (-100 reverse to +100 forward)"
    )
    ttl_ms: int = Field(default=500, ge=100, le=5000, description="Lease TTL in ms")


class MotorResponse(BaseModel):
    """DC motor speed control response."""

    channel: int
    speed_pct: float
    timestamp: str


class StopMotorsResponse(BaseModel):
    """Response for stop_all_motors."""

    stopped: int
    timestamp: str


class MotorLeaseItem(BaseModel):
    """A single active motor TTL lease entry."""

    channel: int
    ttl_remaining_ms: int
    conn_id: int


class MotorStatusResponse(BaseModel):
    """Active motor lease table response."""

    active_leases: list[MotorLeaseItem]
    timestamp: str


# ---------------------------------------------------------------------------
# Vehicle-level request/response models (high-level convenience API)
# ---------------------------------------------------------------------------


class DriveRequest(BaseModel):
    """Coordinated all-motor drive request."""

    speed_pct: float = Field(
        ..., ge=-100.0, le=100.0, description="Signed speed (-100 reverse to +100 forward)"
    )
    ttl_ms: int = Field(default=500, ge=100, le=5000, description="Lease TTL in ms")


class DriveResponse(BaseModel):
    """Coordinated drive response."""

    speed_pct: float
    motors: int
    timestamp: str


class SteerRequest(BaseModel):
    """Steering servo request."""

    angle_deg: float = Field(
        ..., ge=0.0, le=180.0, description="Steering angle degrees (90 = straight)"
    )
    ttl_ms: int = Field(default=500, ge=100, le=5000, description="Lease TTL in ms")


class SteerResponse(BaseModel):
    """Steering response."""

    angle_deg: float
    timestamp: str


class PanRequest(BaseModel):
    """Camera pan servo request."""

    angle_deg: float = Field(..., ge=0.0, le=180.0, description="Pan angle degrees (90 = centre)")
    ttl_ms: int = Field(default=500, ge=100, le=5000, description="Lease TTL in ms")


class PanResponse(BaseModel):
    """Camera pan response."""

    angle_deg: float
    timestamp: str


class TiltRequest(BaseModel):
    """Camera tilt servo request."""

    angle_deg: float = Field(..., ge=0.0, le=180.0, description="Tilt angle degrees (90 = centre)")
    ttl_ms: int = Field(default=500, ge=100, le=5000, description="Lease TTL in ms")


class TiltResponse(BaseModel):
    """Camera tilt response."""

    angle_deg: float
    timestamp: str


class GrayscaleResponse(BaseModel):
    """Grayscale sensor ADC readings."""

    channels: list[int]
    values: list[int]
    timestamp: str


# ---------------------------------------------------------------------------
# Ultrasonic sensor models
# ---------------------------------------------------------------------------


class UltrasonicResponse(BaseModel):
    """Ultrasonic distance sensor reading."""

    distance_cm: float
    timestamp: str


# ---------------------------------------------------------------------------
# Speaker models
# ---------------------------------------------------------------------------


class SpeakerRequest(BaseModel):
    """Speaker amplifier enable/disable request."""

    enabled: bool = Field(..., description="True to enable amplifier, False to disable")


class SpeakerResponse(BaseModel):
    """Speaker amplifier state response."""

    enabled: bool
    timestamp: str


# ---------------------------------------------------------------------------
# Stream start/stop models
# ---------------------------------------------------------------------------


class StreamStartRequest(BaseModel):
    """Stream server start request (all fields optional — defaults from config)."""

    host: Optional[str] = Field(
        default=None, description="Bind host (default: from config or 0.0.0.0)"
    )
    port: Optional[int] = Field(
        default=None, ge=1, le=65535, description="Port (default: from config or 8000)"
    )


class StreamStartResponse(BaseModel):
    """Stream server started successfully."""

    url: str
    host: str
    port: int
    timestamp: str


class StreamStopResponse(BaseModel):
    """Stream server stopped."""

    success: bool
    timestamp: str


class StreamStatusResponse(BaseModel):
    """Current stream server status."""

    running: bool
    url: Optional[str]
    timestamp: str


# ---------------------------------------------------------------------------
# Audio recording models
# ---------------------------------------------------------------------------


class AudioRecordStartRequest(BaseModel):
    """Start audio recording request."""

    filename: Optional[str] = Field(
        default=None,
        description="Output WAV filename (basename only). Auto-generated if absent.",
    )


class AudioRecordStartResponse(BaseModel):
    """Audio recording started."""

    recording: bool
    filename: str
    timestamp: str


class AudioRecordStopResponse(BaseModel):
    """Audio recording stopped."""

    recording: bool
    filename: Optional[str]
    timestamp: str


# ---------------------------------------------------------------------------
# Audio playback models
# ---------------------------------------------------------------------------


class AudioPlayRequest(BaseModel):
    """Start audio playback request."""

    filename: str = Field(..., description="WAV filename or absolute path to play")


class AudioPlayResponse(BaseModel):
    """Playback started."""

    playing: bool
    filename: str
    timestamp: str


class AudioPlayStopResponse(BaseModel):
    """Playback stopped."""

    success: bool
    timestamp: str


class AudioFilesResponse(BaseModel):
    """List of available audio files."""

    files: list[str]
    timestamp: str


class AudioStatusResponse(BaseModel):
    """Current audio recorder and player state."""

    recording: bool
    recording_file: Optional[str]
    playing: bool
    playback_file: Optional[str]
    timestamp: str


# ============================================================================
# Utility Functions
# ============================================================================


def create_self_signed_cert(cert_path: Path, key_path: Path) -> None:
    """Generate a self-signed certificate for HTTPS.

    Creates a self-signed certificate and key pair suitable for
    development and testing. In production, use proper certificates.

    Parameters
    ----------
    cert_path : Path
        Path where certificate file (.pem) will be saved
    key_path : Path
        Path where private key file (.pem) will be saved

    Raises
    ------
    ImportError
        If cryptography package is not installed
    """
    try:
        from ipaddress import IPv4Address

        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as e:
        raise ImportError(
            "cryptography package required for certificate generation. "
            "Install with: pip install nomothetic[api]"
        ) from e

    # Skip if files already exist
    if cert_path.exists() and key_path.exists():
        return

    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )

    # Build certificate subject
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "State"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "City"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "nomothetic"),
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ]
    )

    # Create certificate
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365 * 10))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(IPv4Address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(private_key, hashes.SHA256(), default_backend())
    )

    # Write certificate
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    # Write private key
    key_path.parent.mkdir(parents=True, exist_ok=True)
    with open(key_path, "wb") as f:
        f.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )


# ============================================================================
# Global service instances
# ============================================================================


_camera: Optional[Camera] = None
_hat_client: Optional[HatClient] = None
_stream_server: Optional[StreamServer] = None
_stream_host: str = "0.0.0.0"
_stream_port: int = 8000
_audio_recorder: Optional[AudioRecorder] = None
_audio_player: Optional[AudioPlayer] = None
_media_dir: Path = Path("~/perceptua-nomon/media").expanduser()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage camera, HAT client, and audio initialization and cleanup."""
    global _camera, _hat_client, _audio_recorder, _audio_player, _media_dir
    # Resolve media directory from environment (set by start.sh from config.toml)
    _media_dir = Path(os.environ.get("NOMON_MEDIA_DIR", "~/perceptua-nomon/media")).expanduser()
    # Startup: Initialize camera
    try:
        _camera = Camera(
            directory=_media_dir / "videos",
            photo_directory=_media_dir / "photos",
        )
    except RuntimeError as e:
        logger.warning("Camera initialization failed; API will run without camera: %s", e)

    # Startup: Create HAT client (lazy connect — daemon may not be running yet)
    _hat_client = HatClient()

    # Startup: Initialize audio recorder and player
    _audio_recorder = AudioRecorder(audio_dir=_media_dir / "audio")
    _audio_player = AudioPlayer(audio_dir=_media_dir / "audio")

    yield

    # Shutdown: stop any active audio sessions
    if _audio_recorder and _audio_recorder.is_recording:
        _audio_recorder.stop()
    if _audio_player and _audio_player.is_playing:
        _audio_player.stop()

    # Shutdown: stop stream server if running
    if _stream_server is not None:
        _stream_server.close()

    # Shutdown: Cleanup camera and HAT
    if _camera:
        _camera.close()
    if _hat_client:
        _hat_client.close()


# ============================================================================
# FastAPI Application
# ============================================================================


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns
    -------
    FastAPI
        Configured FastAPI application with CORS and camera endpoints.
    """

    app = FastAPI(
        title="nomon Camera API",
        description="HTTP REST API for Raspberry Pi camera control",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ========================================================================
    # CORS Middleware (Mobile & Web Client Support)
    # ========================================================================

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, limit to specific origins
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )

    # ========================================================================
    # Routes
    # ========================================================================

    @app.get("/", tags=["Health"])
    async def health():
        """Health check endpoint."""
        return {"status": "ok", "service": "nomon-camera-api", "version": "0.1.0"}

    @app.get("/api/camera/status", response_model=CameraStatus, tags=["Camera"])
    async def get_camera_status():
        """Get current camera and recording status.

        Returns
        -------
        CameraStatus
            Camera readiness, recording state, resolution, and settings
        """
        if not _camera:
            raise HTTPException(status_code=500, detail="Camera not initialized")

        return CameraStatus(
            camera_ready=True,
            recording=_camera._is_recording,
            resolution=f"{_camera.width}x{_camera.height}",
            fps=_camera.fps,
            encoder=_camera.encoder,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @app.post("/api/camera/capture", response_model=CaptureResponse, tags=["Camera"])
    async def capture_image(request: CaptureRequest):
        """Capture a still image from the camera.

        Parameters
        ----------
        request : CaptureRequest
            Filename for the captured image (no path components)

        Returns
        -------
        CaptureResponse
            Success status and captured filename

        Raises
        ------
        HTTPException
            If filename is invalid or capture fails
        """
        if not _camera:
            raise HTTPException(status_code=500, detail="Camera not initialized")

        try:
            _camera.capture_image(request.filename)
            return CaptureResponse(
                success=True,
                filename=request.filename,
                timestamp=datetime.now(timezone.utc).isoformat(),
                message=f"Image captured: {request.filename}",
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Capture failed: {str(e)}") from e

    @app.post("/api/camera/record/start", response_model=RecordStartResponse, tags=["Camera"])
    async def start_recording(request: RecordRequest):
        """Start video recording.

        Parameters
        ----------
        request : RecordRequest
            Filename and optional encoder selection

        Returns
        -------
        RecordStartResponse
            Success status and filename

        Raises
        ------
        HTTPException
            If recording is already active, filename is invalid, or start fails
        """
        if not _camera:
            raise HTTPException(status_code=500, detail="Camera not initialized")

        if _camera._is_recording:
            raise HTTPException(status_code=409, detail="Recording already in progress")

        try:
            # If encoder is specified, update camera settings
            if request.encoder and request.encoder.lower() in ["h264", "mjpeg"]:
                _camera.encoder = request.encoder.lower()

            await asyncio.to_thread(_camera.start_recording, request.filename)
            return RecordStartResponse(
                success=True,
                filename=request.filename,
                timestamp=datetime.now(timezone.utc).isoformat(),
                message=f"Recording started: {request.filename}",
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Recording start failed: {str(e)}") from e

    @app.post("/api/camera/record/stop", response_model=RecordStopResponse, tags=["Camera"])
    async def stop_recording():
        """Stop the current video recording.

        Returns
        -------
        RecordStopResponse
            Success status and timestamp

        Raises
        ------
        HTTPException
            If no recording is in progress or stop fails
        """
        if not _camera:
            raise HTTPException(status_code=500, detail="Camera not initialized")

        if not _camera._is_recording:
            raise HTTPException(status_code=409, detail="No recording in progress")

        try:
            await asyncio.to_thread(_camera.stop_recording)
            return RecordStopResponse(
                success=True,
                timestamp=datetime.now(timezone.utc).isoformat(),
                message="Recording stopped",
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Recording stop failed: {str(e)}") from e

    # ========================================================================
    # HAT Endpoints
    # ========================================================================

    @app.get("/api/hat/battery", response_model=BatteryResponse, tags=["HAT"])
    async def get_battery():
        """Read the Robot HAT V4 battery voltage.

        Returns
        -------
        BatteryResponse
            Battery voltage in volts and UTC timestamp.

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on hardware read failure.
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            voltage = await asyncio.to_thread(_hat_client.get_battery_voltage)
            return BatteryResponse(
                voltage_v=voltage,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.post("/api/hat/servo", response_model=ServoResponse, tags=["HAT"])
    async def set_servo(request: ServoRequest):
        """Set a servo channel to the requested angle.

        Parameters
        ----------
        request : ServoRequest
            Target channel, angle in degrees, and optional TTL.

        Returns
        -------
        ServoResponse
            Echoed channel, angle, and UTC timestamp.

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on hardware write failure.
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            await asyncio.to_thread(
                _hat_client.set_servo_angle,
                request.channel,
                request.angle_deg,
                request.ttl_ms,
            )
            return ServoResponse(
                channel=request.channel,
                angle_deg=request.angle_deg,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.post("/api/hat/reset", response_model=ResetResponse, tags=["HAT"])
    async def reset_mcu():
        """Assert and release the Robot HAT V4 MCU reset line.

        Returns
        -------
        ResetResponse
            Success status and UTC timestamp.

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on GPIO failure.
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            await asyncio.to_thread(_hat_client.reset_mcu)
            return ResetResponse(
                success=True,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/hat/servo/status", response_model=ServoStatusResponse, tags=["HAT"])
    async def get_servo_status():
        """Return the daemon's active servo TTL lease table.

        Returns
        -------
        ServoStatusResponse
            List of active leases with channel, TTL remaining, and connection ID.

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on error.
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            status = await asyncio.to_thread(_hat_client.get_servo_status)
            return ServoStatusResponse(
                active_leases=[
                    ServoLeaseItem(
                        channel=e.channel,
                        ttl_remaining_ms=e.ttl_remaining_ms,
                        conn_id=e.conn_id,
                    )
                    for e in status.active_leases
                ],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/hat/mcu/status", response_model=McuStatusResponse, tags=["HAT"])
    async def get_mcu_status():
        """Return MCU reset statistics tracked by the daemon.

        Returns
        -------
        McuStatusResponse
            Reset count since daemon start and seconds since last reset (null if none).

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on error.
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            status = await asyncio.to_thread(_hat_client.get_mcu_status)
            return McuStatusResponse(
                resets_since_start=status.resets_since_start,
                last_reset_s_ago=status.last_reset_s_ago,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.post("/api/hat/motor", response_model=MotorResponse, tags=["HAT"])
    async def set_motor(request: MotorRequest):
        """Set a DC motor channel to the requested speed.

        Parameters
        ----------
        request : MotorRequest
            Target channel (0–3), signed speed percentage, and optional TTL.

        Returns
        -------
        MotorResponse
            Echoed channel, speed_pct, and UTC timestamp.

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on hardware write failure.
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            await asyncio.to_thread(
                _hat_client.set_motor_speed,
                request.channel,
                request.speed_pct,
                request.ttl_ms,
            )
            return MotorResponse(
                channel=request.channel,
                speed_pct=request.speed_pct,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.post("/api/hat/motor/stop", response_model=StopMotorsResponse, tags=["HAT"])
    async def stop_motors():
        """Immediately stop all DC motors and clear their leases.

        Returns
        -------
        StopMotorsResponse
            Number of motors stopped and UTC timestamp.

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on hardware failure.
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            stopped = await asyncio.to_thread(_hat_client.stop_all_motors)
            return StopMotorsResponse(
                stopped=stopped,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/hat/motor/status", response_model=MotorStatusResponse, tags=["HAT"])
    async def get_motor_status():
        """Return the daemon's active motor TTL lease table.

        Returns
        -------
        MotorStatusResponse
            List of active leases with channel, TTL remaining, and connection ID.

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on error.
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            status = await asyncio.to_thread(_hat_client.get_motor_status)
            return MotorStatusResponse(
                active_leases=[
                    MotorLeaseItem(
                        channel=e.channel,
                        ttl_remaining_ms=e.ttl_remaining_ms,
                        conn_id=e.conn_id,
                    )
                    for e in status.active_leases
                ],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    # ========================================================================
    # Vehicle Endpoints (high-level convenience API)
    # ========================================================================

    @app.post("/api/drive", response_model=DriveResponse, tags=["Vehicle"])
    async def drive(request: DriveRequest):
        """Drive all configured DC motors at the same speed simultaneously.

        Sends a single coordinated ``drive`` IPC command that sets all motors
        in one atomic operation, ensuring synchronised wheel movement.

        Parameters
        ----------
        request : DriveRequest
            Signed speed (−100–100) and optional TTL lease.

        Returns
        -------
        DriveResponse
            Echoed speed, number of motors commanded, and UTC timestamp.

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on hardware write failure.
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            motors = await asyncio.to_thread(_hat_client.drive, request.speed_pct, request.ttl_ms)
            return DriveResponse(
                speed_pct=request.speed_pct,
                motors=motors,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.post("/api/steer", response_model=SteerResponse, tags=["Vehicle"])
    async def steer(request: SteerRequest):
        """Set the steering servo angle.

        Parameters
        ----------
        request : SteerRequest
            Angle in degrees (0–180, 90 = straight ahead) and optional TTL.

        Returns
        -------
        SteerResponse
            Echoed angle and UTC timestamp.

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on hardware write failure.
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            await asyncio.to_thread(_hat_client.steer, request.angle_deg, request.ttl_ms)
            return SteerResponse(
                angle_deg=request.angle_deg,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.post("/api/camera/pan", response_model=PanResponse, tags=["Vehicle"])
    async def pan_camera(request: PanRequest):
        """Set the camera pan (horizontal) servo angle.

        Parameters
        ----------
        request : PanRequest
            Angle in degrees (0–180, 90 = centre) and optional TTL.

        Returns
        -------
        PanResponse
            Echoed angle and UTC timestamp.

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on hardware write failure.
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            await asyncio.to_thread(_hat_client.pan_camera, request.angle_deg, request.ttl_ms)
            return PanResponse(
                angle_deg=request.angle_deg,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.post("/api/camera/tilt", response_model=TiltResponse, tags=["Vehicle"])
    async def tilt_camera(request: TiltRequest):
        """Set the camera tilt (vertical) servo angle.

        Parameters
        ----------
        request : TiltRequest
            Angle in degrees (0–180, 90 = centre) and optional TTL.

        Returns
        -------
        TiltResponse
            Echoed angle and UTC timestamp.

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on hardware write failure.
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            await asyncio.to_thread(_hat_client.tilt_camera, request.angle_deg, request.ttl_ms)
            return TiltResponse(
                angle_deg=request.angle_deg,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/sensor/grayscale", response_model=GrayscaleResponse, tags=["Vehicle"])
    async def get_grayscale():
        """Read all three grayscale sensor ADC channels.

        Returns raw 12-bit ADC values for the left, center, and right
        grayscale sensors used for cliff and line detection.

        Returns
        -------
        GrayscaleResponse
            ADC channel numbers and raw readings (0–4095) per channel.

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on hardware read failure.
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            result = await asyncio.to_thread(_hat_client.read_grayscale)
            return GrayscaleResponse(
                channels=result.channels,
                values=result.values,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    # ========================================================================
    # Ultrasonic Sensor
    # ========================================================================

    @app.get("/api/sensor/ultrasonic", response_model=UltrasonicResponse, tags=["Sensor"])
    async def get_ultrasonic():
        """Trigger the ultrasonic sensor and return the measured distance.

        The nomopractic daemon drives the ultrasonic trigger line for 10 µs and times
        the echo pulse to compute the distance. GPIO mappings for trigger and echo
        are defined in the nomopractic configuration and IPC schema
        (see ``docs/hat_ipc_schema.md``). Valid range is 2–400 cm for
        HC-SR04-compatible sensors.

        Returns
        -------
        UltrasonicResponse
            ``distance_cm``: distance in centimetres.

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on hardware error (timeout, no echo, GPIO failure).
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            result = await asyncio.to_thread(_hat_client.read_ultrasonic)
            return UltrasonicResponse(
                distance_cm=result.distance_cm,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    # ========================================================================
    # Speaker amplifier
    # ========================================================================

    @app.post("/api/hat/speaker", response_model=SpeakerResponse, tags=["HAT"])
    async def set_speaker(request: SpeakerRequest):
        """Enable or disable the speaker amplifier on the Robot HAT V4.

        The amplifier is controlled by asserting BCM 20 (``spk_en``) HIGH
        (enabled) or LOW (disabled) via the nomopractic daemon.

        Parameters
        ----------
        request : SpeakerRequest
            ``enabled``: true to power the amplifier on, false to power off.

        Returns
        -------
        SpeakerResponse
            Current ``enabled`` state.

        Raises
        ------
        HTTPException
            503 if the nomopractic daemon is unavailable.
            500 on GPIO error.
        """
        if _hat_client is None:
            raise HTTPException(status_code=503, detail="nomopractic daemon not available")
        try:
            if request.enabled:
                await asyncio.to_thread(_hat_client.enable_speaker)
            else:
                await asyncio.to_thread(_hat_client.disable_speaker)
            return SpeakerResponse(
                enabled=request.enabled,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except HatConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except HatError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    # ========================================================================
    # MJPEG stream start / stop
    # ========================================================================

    @app.post("/api/stream/start", response_model=StreamStartResponse, tags=["Stream"])
    async def start_stream(request: StreamStartRequest):
        """Start the MJPEG stream server in the background.

        Uses the host and port from the request (or defaults from the server
        config) to start a Flask-based MJPEG stream server.  If a stream is
        already running the existing URL is returned without restarting.

        Returns
        -------
        StreamStartResponse
            ``url``: base URL of the stream viewer (``http://host:port``).

        Raises
        ------
        HTTPException
            503 if the camera is not available.
            500 if the stream server fails to start.
        """
        global _stream_server, _stream_host, _stream_port
        if _camera is None:
            raise HTTPException(status_code=503, detail="camera not available")

        host = request.host or _stream_host
        port = request.port or _stream_port

        if _stream_server is not None:
            url = f"http://{_stream_server.host}:{_stream_server.port}"
            return StreamStartResponse(
                url=url,
                host=_stream_server.host,
                port=_stream_server.port,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        try:
            server = StreamServer(host=host, port=port)
            server.start_background()
            _stream_server = server
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to start stream: {e}") from e

        url = f"http://{host}:{port}"
        return StreamStartResponse(
            url=url,
            host=host,
            port=port,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @app.post("/api/stream/stop", response_model=StreamStopResponse, tags=["Stream"])
    async def stop_stream():
        """Stop the MJPEG stream server.

        Stops the background stream server so that the camera is available
        for capture and recording operations again.

        Returns
        -------
        StreamStopResponse
            ``success``: true if a running stream was stopped.
        """
        global _stream_server
        if _stream_server is None:
            return StreamStopResponse(
                success=False,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        try:
            _stream_server.close()
        except Exception:
            pass
        _stream_server = None
        return StreamStopResponse(
            success=True,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @app.get("/api/stream/status", response_model=StreamStatusResponse, tags=["Stream"])
    async def get_stream_status():
        """Return the current stream server state."""
        running = _stream_server is not None
        url = (
            f"http://{_stream_server.host}:{_stream_server.port}"
            if _stream_server is not None
            else None
        )
        return StreamStatusResponse(
            running=running,
            url=url,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # ========================================================================
    # Audio recording
    # ========================================================================

    @app.post("/api/audio/record/start", response_model=AudioRecordStartResponse, tags=["Audio"])
    async def start_audio_recording(request: AudioRecordStartRequest):
        """Start recording audio from the USB microphone.

        Records from the built-in USB microphone (PCM2902 codec) to a WAV
        file in the configured audio directory.  Recording continues until
        ``POST /api/audio/record/stop`` is called.

        Parameters
        ----------
        request : AudioRecordStartRequest
            Optional ``filename`` (basename only).  A timestamped name is
            generated when absent.

        Returns
        -------
        AudioRecordStartResponse
            ``filename``: absolute path of the output WAV file.

        Raises
        ------
        HTTPException
            409 if a recording is already in progress.
            500 on hardware error.
        """
        if _audio_recorder is None:
            raise HTTPException(status_code=503, detail="audio not available")
        if _audio_recorder.is_recording:
            raise HTTPException(status_code=409, detail="A recording is already in progress")
        try:
            filename = await asyncio.to_thread(_audio_recorder.start, request.filename)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        return AudioRecordStartResponse(
            recording=True,
            filename=filename,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @app.post("/api/audio/record/stop", response_model=AudioRecordStopResponse, tags=["Audio"])
    async def stop_audio_recording():
        """Stop the active audio recording session.

        Finalises and flushes the WAV file.

        Returns
        -------
        AudioRecordStopResponse
            ``filename``: path of the completed recording, or null if no
            recording was active.
        """
        if _audio_recorder is None:
            raise HTTPException(status_code=503, detail="audio not available")
        filename = await asyncio.to_thread(_audio_recorder.stop)
        return AudioRecordStopResponse(
            recording=False,
            filename=filename,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # ========================================================================
    # Audio playback
    # ========================================================================

    @app.post("/api/audio/play", response_model=AudioPlayResponse, tags=["Audio"])
    async def play_audio(request: AudioPlayRequest):
        """Play a WAV audio file over the speaker.

        Enables the speaker amplifier via the nomopractic HAT daemon, then
        starts playback of the specified WAV file through the HifiBerry DAC.

        Parameters
        ----------
        request : AudioPlayRequest
            ``filename``: path or basename of the WAV file to play.

        Returns
        -------
        AudioPlayResponse
            ``filename``: resolved path of the file being played.

        Raises
        ------
        HTTPException
            404 if the file does not exist.
            409 if playback is already in progress.
            500 on hardware error.
        """
        if _audio_player is None:
            raise HTTPException(status_code=503, detail="audio not available")
        if _audio_player.is_playing:
            raise HTTPException(status_code=409, detail="Playback is already in progress")

        # Enable speaker amplifier before playback (best-effort).
        if _hat_client is not None:
            try:
                await asyncio.to_thread(_hat_client.enable_speaker)
            except (HatError, HatConnectionError):
                pass  # Continue without amplifier enable if daemon unavailable.

        try:
            await asyncio.to_thread(_audio_player.play, request.filename)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except RuntimeError as e:
            message = str(e)
            lower_message = message.lower()
            if "pyaudio not installed" in lower_message or "pyaudio not available" in lower_message:
                raise HTTPException(status_code=503, detail=message) from e
            raise HTTPException(status_code=409, detail=message) from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

        return AudioPlayResponse(
            playing=True,
            filename=_audio_player.current_file or request.filename,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @app.post("/api/audio/play/stop", response_model=AudioPlayStopResponse, tags=["Audio"])
    async def stop_audio_playback():
        """Stop ongoing audio playback.

        Stops the player and disables the speaker amplifier.

        Returns
        -------
        AudioPlayStopResponse
            ``success``: true.
        """
        if _audio_player is None:
            raise HTTPException(status_code=503, detail="audio not available")
        await asyncio.to_thread(_audio_player.stop)

        # Disable speaker amplifier after playback (best-effort).
        if _hat_client is not None:
            try:
                await asyncio.to_thread(_hat_client.disable_speaker)
            except (HatError, HatConnectionError):
                pass

        return AudioPlayStopResponse(
            success=True,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @app.get("/api/audio/files", response_model=AudioFilesResponse, tags=["Audio"])
    async def list_audio():
        """List available WAV audio files in the configured audio directory.

        Returns
        -------
        AudioFilesResponse
            ``files``: sorted list of WAV file basenames.
        """
        files = await asyncio.to_thread(list_audio_files, _media_dir / "audio")
        return AudioFilesResponse(
            files=files,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @app.get("/api/audio/status", response_model=AudioStatusResponse, tags=["Audio"])
    async def get_audio_status():
        """Return current audio recorder and player state."""
        return AudioStatusResponse(
            recording=_audio_recorder.is_recording if _audio_recorder else False,
            recording_file=_audio_recorder.current_file if _audio_recorder else None,
            playing=_audio_player.is_playing if _audio_player else False,
            playback_file=_audio_player.current_file if _audio_player else None,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # Global exception handler
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request, exc):
        """Format HTTP exceptions as JSON."""
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error=exc.detail, timestamp=datetime.now(timezone.utc).isoformat()
            ).model_dump(),
        )

    return app


# ============================================================================
# Server Wrapper
# ============================================================================


class APIServer:
    """Manages the FastAPI application and uvicorn server lifecycle.

    Parameters
    ----------
    host : str, optional
        Bind address (default: "127.0.0.1" for local only)
    port : int, optional
        Listen port (default: 8443)
    use_ssl : bool, optional
        Enable HTTPS with self-signed certificate (default: True)
    cert_dir : Path, optional
        Directory for certificates (default: ".certs")
    reload : bool, optional
        Auto-reload on code changes (default: False)

    Raises
    ------
    ValueError
        If port is out of valid range (1-65535)
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8443,
        use_ssl: bool = True,
        cert_dir: Optional[Path] = None,
        reload: bool = False,
    ):
        if not 1 <= port <= 65535:
            raise ValueError(f"Invalid port {port}: must be between 1 and 65535")

        self.host = host
        self.port = port
        self.reload = reload
        self.use_ssl = use_ssl
        self.cert_dir = Path(cert_dir or ".certs")
        self.app = create_app()

        if self.use_ssl:
            self.cert_file = self.cert_dir / "cert.pem"
            self.key_file = self.cert_dir / "key.pem"
            create_self_signed_cert(self.cert_file, self.key_file)

    def get_config(self) -> dict:
        """Get uvicorn configuration dictionary.

        Returns
        -------
        dict
            Configuration for uvicorn.run()
        """
        config = {
            "app": self.app,
            "host": self.host,
            "port": self.port,
            "reload": self.reload,
            "log_level": "info",
        }

        if self.use_ssl:
            config["ssl_certfile"] = str(self.cert_file)
            config["ssl_keyfile"] = str(self.key_file)

        return config

    def run(self) -> None:
        """Start the API server (blocking).

        Raises
        ------
        ImportError
            If uvicorn is not installed
        """
        try:
            import uvicorn
        except ImportError as e:
            raise ImportError(
                "uvicorn package required to run API server. "
                "Install with: pip install nomothetic[api]"
            ) from e

        config = self.get_config()
        protocol = "https" if self.use_ssl else "http"
        logger.info("Starting API server at %s://%s:%s", protocol, self.host, self.port)
        uvicorn.run(**config)

    def start_background(self):
        """Start the API server in a background thread.

        Returns
        -------
        threading.Thread
            The server thread (daemon thread)

        Raises
        ------
        ImportError
            If uvicorn is not installed
        """
        import threading

        try:
            import uvicorn
        except ImportError as e:
            raise ImportError(
                "uvicorn package required to run API server. "
                "Install with: pip install nomothetic[api]"
            ) from e

        config = self.get_config()

        def run_server():
            uvicorn.run(**config)

        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        return thread
