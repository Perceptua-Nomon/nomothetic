"""Fleet management endpoints for central-mode deployment.

Provides device registration, listing, detail queries, and removal.
All endpoints require JWT authentication and are tagged ``Fleet``
in the OpenAPI docs.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field

from nomothetic.auth import TokenPayload, jwt_required
from nomothetic.fleet_store import DeviceItem, FleetStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class DeviceRegisterRequest(BaseModel):
    """Register a new device to the authenticated user."""

    vin: str = Field(..., min_length=1, max_length=64, description="Vehicle identification number")
    model: str = Field(..., min_length=1, max_length=64, description="Vehicle model name")


class DeviceRegisterResponse(BaseModel):
    """Successful device registration response."""

    vin: str
    model: str
    registered_at: str
    timestamp: str


class DeviceListResponse(BaseModel):
    """List of devices owned by the authenticated user."""

    devices: list[DeviceItem]
    timestamp: str


class DeviceDetailResponse(BaseModel):
    """Detailed device view with optional telemetry."""

    vin: str
    model: str
    firmware_version: Optional[str] = None
    last_seen_at: Optional[str] = None
    registered_at: str
    role: str
    latest_telemetry: Optional[dict] = None
    timestamp: str


class DeviceRemoveResponse(BaseModel):
    """Device removal confirmation."""

    vin: str
    removed: bool
    timestamp: str


# Module-level store (set by create_app).
_fleet_store: Optional[FleetStore] = None


def set_fleet_store(store: FleetStore) -> None:
    """Store the fleet store instance for use by fleet routes."""
    global _fleet_store
    _fleet_store = store


def get_fleet_store() -> Optional[FleetStore]:
    """Return the current fleet store (if configured)."""
    return _fleet_store


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def create_fleet_router() -> APIRouter:
    """Build and return the fleet management API router.

    Returns
    -------
    APIRouter
        Router with device CRUD endpoints.
    """
    router = APIRouter(prefix="/api/fleet", tags=["Fleet"])

    def _require_store() -> FleetStore:
        store = get_fleet_store()
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Fleet service not configured",
            )
        return store

    @router.post("/devices", response_model=DeviceRegisterResponse, status_code=201)
    async def register_device(
        request: DeviceRegisterRequest,
        claims: TokenPayload = Depends(jwt_required),
    ):
        """Register a new device and link it to the authenticated user.

        Returns
        -------
        DeviceRegisterResponse
            VIN, model, and registration timestamp.

        Raises
        ------
        HTTPException
            409 if the device is already registered.
        """
        store = _require_store()
        try:
            item = await store.register_device(claims.sub, request.vin, request.model)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return DeviceRegisterResponse(
            vin=item.vin,
            model=item.model,
            registered_at=item.registered_at,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @router.get("/devices", response_model=DeviceListResponse)
    async def list_devices(claims: TokenPayload = Depends(jwt_required)):
        """List all devices owned by the authenticated user.

        Returns
        -------
        DeviceListResponse
            List of device summaries.
        """
        store = _require_store()
        devices = await store.get_devices(claims.sub)
        return DeviceListResponse(
            devices=devices,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @router.get("/devices/{vin}", response_model=DeviceDetailResponse)
    async def get_device(
        vin: str = Path(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
        claims: TokenPayload = Depends(jwt_required),
    ):
        """Return detail for a single device including latest telemetry.

        Returns
        -------
        DeviceDetailResponse
            Device info with optional telemetry snapshot.

        Raises
        ------
        HTTPException
            404 if the device is not found or owned by another user.
        """
        store = _require_store()
        item = await store.get_device(claims.sub, vin)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Device {vin} not found",
            )
        return DeviceDetailResponse(
            vin=item.vin,
            model=item.model,
            firmware_version=item.firmware_version,
            last_seen_at=item.last_seen_at,
            registered_at=item.registered_at,
            role=item.role,
            latest_telemetry=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @router.delete("/devices/{vin}", response_model=DeviceRemoveResponse)
    async def remove_device(
        vin: str = Path(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
        claims: TokenPayload = Depends(jwt_required),
    ):
        """Remove a device from the authenticated user's fleet.

        Does not delete the underlying Vehicle record — only removes the
        ownership edge.

        Returns
        -------
        DeviceRemoveResponse
            Confirmation with VIN and removed status.

        Raises
        ------
        HTTPException
            404 if the device is not found.
        """
        store = _require_store()
        removed = await store.remove_device(claims.sub, vin)
        if not removed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Device {vin} not found",
            )
        return DeviceRemoveResponse(
            vin=vin,
            removed=True,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    return router
