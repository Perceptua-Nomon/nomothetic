"""Fleet device persistence layer with in-memory and ArcadeDB backends.

Protocol-based store abstraction for vehicle registration and fleet queries.
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional, runtime_checkable

from pydantic import BaseModel
from typing_extensions import Protocol

from nomothetic.gremlin_utils import sanitize_gremlin_value as _sanitize_gremlin_value

if TYPE_CHECKING:
    from nomothetic.db import DatabaseClient

logger = logging.getLogger(__name__)


def _coerce_count(rows: list[Any]) -> int:
    if not rows:
        return 0
    first = rows[0]
    if isinstance(first, int):
        return first
    if isinstance(first, float):
        return int(first)
    if isinstance(first, dict):
        val = first.get("count", 0)
        if isinstance(val, (int, float)):
            return int(val)
    return 0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class DeviceItem(BaseModel):
    """Summary device entry returned in list responses."""

    vin: str
    model: str
    firmware_version: Optional[str] = None
    last_seen_at: Optional[str] = None
    registered_at: str
    role: str = "owner"


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FleetStore(Protocol):
    """Abstract interface for fleet device persistence."""

    async def get_devices(self, owner_email: str) -> list[DeviceItem]:
        """List all devices owned by a user.

        Parameters
        ----------
        owner_email : str
            Normalised email of the device owner.

        Returns
        -------
        list[DeviceItem]
        """
        ...  # pragma: no cover

    async def get_device(self, owner_email: str, vin: str) -> Optional[DeviceItem]:
        """Look up a single device by VIN scoped to its owner.

        Parameters
        ----------
        owner_email : str
            Normalised owner email.
        vin : str
            Vehicle identification number.

        Returns
        -------
        DeviceItem or None
        """
        ...  # pragma: no cover

    async def register_device(self, owner_email: str, vin: str, model: str) -> DeviceItem:
        """Register a new device under an owner.

        Parameters
        ----------
        owner_email : str
            Normalised owner email.
        vin : str
            Vehicle identification number.
        model : str
            Vehicle model name.

        Returns
        -------
        DeviceItem
            The newly registered device.

        Raises
        ------
        ValueError
            If the device is already registered for this owner.
        """
        ...  # pragma: no cover

    async def remove_device(self, owner_email: str, vin: str) -> bool:
        """Remove a device from an owner's fleet.

        Parameters
        ----------
        owner_email : str
            Normalised owner email.
        vin : str
            Vehicle identification number.

        Returns
        -------
        bool
            True if the device was found and removed.
        """
        ...  # pragma: no cover

    async def device_exists(self, vin: str) -> bool:
        """Check whether a device with the given VIN exists globally.

        Parameters
        ----------
        vin : str
            Vehicle identification number.

        Returns
        -------
        bool
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryFleetStore:
    """Dict-backed fleet store for testing and development.

    Keyed by ``(email, vin)`` internally to scope devices per user.
    """

    def __init__(self) -> None:
        # {email: {vin: DeviceItem}}
        self._devices: dict[str, dict[str, DeviceItem]] = {}

    async def get_devices(self, owner_email: str) -> list[DeviceItem]:
        """Return all devices owned by the user."""
        return list(self._devices.get(owner_email, {}).values())

    async def get_device(self, owner_email: str, vin: str) -> Optional[DeviceItem]:
        """Look up a single device by VIN scoped to user."""
        return self._devices.get(owner_email, {}).get(vin)

    async def register_device(self, owner_email: str, vin: str, model: str) -> DeviceItem:
        """Register a device under a user.

        Raises
        ------
        ValueError
            If the device is already registered for this user.
        """
        bucket = self._devices.setdefault(owner_email, {})
        if vin in bucket:
            raise ValueError(f"Device {vin} already registered")
        now = datetime.now(timezone.utc).isoformat()
        item = DeviceItem(
            vin=vin,
            model=model,
            registered_at=now,
            role="owner",
        )
        bucket[vin] = item
        return item

    async def remove_device(self, owner_email: str, vin: str) -> bool:
        """Remove a device from the user's fleet.

        Returns True if the device was found and removed.
        """
        bucket = self._devices.get(owner_email, {})
        return bucket.pop(vin, None) is not None

    async def device_exists(self, vin: str) -> bool:
        """Check whether any user owns a device with this VIN."""
        for bucket in self._devices.values():
            if vin in bucket:
                return True
        return False


# ---------------------------------------------------------------------------
# Gremlin implementation
# ---------------------------------------------------------------------------


class GremlinFleetStore:
    """ArcadeDB-backed fleet store using Gremlin traversals.

    Parameters
    ----------
    db : DatabaseClient
        An initialised database client.
    """

    def __init__(self, db: "DatabaseClient") -> None:
        self._db = db

    async def get_devices(self, owner_email: str) -> list[DeviceItem]:
        """List devices owned by the user via OwnsDevice edges."""
        safe_email = _sanitize_gremlin_value(owner_email)
        query = (
            f"g.V().hasLabel('User').has('email', '{safe_email}')"
            f".outE('OwnsDevice').as('e')"
            f".inV().hasLabel('Vehicle').as('v')"
            f".select('e', 'v').by(elementMap())"
        )
        rows = await self._db.execute_gremlin(query)
        items: list[DeviceItem] = []
        for row in rows:
            edge = row.get("e", {})
            vehicle = row.get("v", {})
            items.append(
                DeviceItem(
                    vin=vehicle.get("vin", ""),
                    model=vehicle.get("model", ""),
                    registered_at=edge.get("registered_at", ""),
                    role=edge.get("role", "owner"),
                    firmware_version=vehicle.get("firmware_version"),
                    last_seen_at=vehicle.get("last_seen_at"),
                )
            )
        return items

    async def get_device(self, owner_email: str, vin: str) -> Optional[DeviceItem]:
        """Fetch a single device by VIN scoped to the owner."""
        safe_email = _sanitize_gremlin_value(owner_email)
        safe_vin = _sanitize_gremlin_value(vin)
        query = (
            f"g.V().hasLabel('User').has('email', '{safe_email}')"
            f".outE('OwnsDevice').as('e')"
            f".inV().hasLabel('Vehicle').has('vin', '{safe_vin}').as('v')"
            f".select('e', 'v').by(elementMap())"
        )
        rows = await self._db.execute_gremlin(query)
        if not rows:
            return None
        edge = rows[0].get("e", {})
        vehicle = rows[0].get("v", {})
        return DeviceItem(
            vin=vehicle.get("vin", ""),
            model=vehicle.get("model", ""),
            registered_at=edge.get("registered_at", ""),
            role=edge.get("role", "owner"),
            firmware_version=vehicle.get("firmware_version"),
            last_seen_at=vehicle.get("last_seen_at"),
        )

    async def register_device(self, owner_email: str, vin: str, model: str) -> DeviceItem:
        """Create a Vehicle vertex and OwnsDevice edge.

        Raises
        ------
        ValueError
            If the device is already registered for this owner.
        """
        safe_email = _sanitize_gremlin_value(owner_email)
        safe_vin = _sanitize_gremlin_value(vin)
        safe_model = _sanitize_gremlin_value(model)

        # Check for duplicate
        existing = await self.get_device(owner_email, vin)
        if existing is not None:
            raise ValueError(f"Device {vin} already registered")

        now = datetime.now(timezone.utc).isoformat()
        safe_now = _sanitize_gremlin_value(now)

        # Create or reuse Vehicle vertex
        vehicle_query = (
            f"g.V().hasLabel('Vehicle').has('vin', '{safe_vin}')"
            f".fold().coalesce("
            f"unfold(),"
            f"addV('Vehicle').property('vin', '{safe_vin}')"
            f".property('model', '{safe_model}')"
            f".property('registered_at', '{safe_now}')"
            f").elementMap()"
        )
        await self._db.execute_gremlin(vehicle_query)

        # Create OwnsDevice edge
        edge_query = (
            f"g.V().hasLabel('User').has('email', '{safe_email}').as('u')"
            f".V().hasLabel('Vehicle').has('vin', '{safe_vin}').as('v')"
            f".addE('OwnsDevice').from('u').to('v')"
            f".property('role', 'owner')"
            f".property('registered_at', '{safe_now}')"
        )
        await self._db.execute_gremlin(edge_query)

        return DeviceItem(
            vin=vin,
            model=model,
            registered_at=now,
            role="owner",
        )

    async def remove_device(self, owner_email: str, vin: str) -> bool:
        """Remove the OwnsDevice edge (does not delete the Vehicle vertex)."""
        safe_email = _sanitize_gremlin_value(owner_email)
        safe_vin = _sanitize_gremlin_value(vin)
        query = (
            f"g.V().hasLabel('User').has('email', '{safe_email}')"
            f".outE('OwnsDevice')"
            f".where(inV().has('vin', '{safe_vin}'))"
            f".drop().iterate()"
        )
        # Check existence first
        existing = await self.get_device(owner_email, vin)
        if existing is None:
            return False
        await self._db.execute_gremlin(query)
        return True

    async def device_exists(self, vin: str) -> bool:
        """Check whether a Vehicle vertex with this VIN exists."""
        safe_vin = _sanitize_gremlin_value(vin)
        query = f"g.V().hasLabel('Vehicle').has('vin', '{safe_vin}').count()"
        rows = await self._db.execute_gremlin(query)
        return _coerce_count(rows) > 0
