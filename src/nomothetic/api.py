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
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from nomothetic.camera import Camera
from nomothetic.hat import HatClient, HatConnectionError, HatError

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage camera and HAT client initialization and cleanup."""
    global _camera, _hat_client
    # Startup: Initialize camera
    try:
        _camera = Camera()
    except RuntimeError as e:
        logger.warning("Camera initialization failed; API will run without camera: %s", e)

    # Startup: Create HAT client (lazy connect — daemon may not be running yet)
    _hat_client = HatClient()

    yield

    # Shutdown: Cleanup
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
