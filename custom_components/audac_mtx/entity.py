"""Common base entity that wires into the coordinator + device_info."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AudacCoordinator


class AudacEntity(CoordinatorEntity[AudacCoordinator]):
    """All entities share device_info so they group under one MTX hub."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AudacCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="AUDAC",
            model=entry.data.get("model", "MTX"),
            name=entry.title,
            configuration_url=f"http://{coordinator.hub.host}/",
            sw_version=coordinator.hub.state.firmware,
        )

    @property
    def available(self) -> bool:
        return self.coordinator.hub.state.connected and super().available
