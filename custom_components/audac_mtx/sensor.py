"""Diagnostic sensors: VU-meter, connection status, last error."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AudacCoordinator
from .entity import AudacEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AudacCoordinator = hass.data[DOMAIN][entry.entry_id]
    items: list[SensorEntity] = [
        AudacConnectionSensor(coordinator),
        AudacLastErrorSensor(coordinator),
        AudacFirmwareSensor(coordinator),
    ]
    # 10 VU-meter slots (device sends 10 levels, exact mapping per AUDAC docs varies
    # by config — expose all, user can rename / hide what's not needed)
    items.extend(AudacVuSensor(coordinator, i) for i in range(10))
    async_add_entities(items)


class AudacConnectionSensor(AudacEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connection"
    _attr_name = "Connection"

    def __init__(self, coordinator: AudacCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_connection"

    @property
    def native_value(self) -> str:
        return "connected" if self.coordinator.hub.state.connected else "disconnected"

    @property
    def available(self) -> bool:  # always available — it IS the diagnostic
        return True


class AudacLastErrorSensor(AudacEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Last error"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AudacCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_last_error"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.hub.state.last_error

    @property
    def available(self) -> bool:
        return True


class AudacFirmwareSensor(AudacEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Firmware"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AudacCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_firmware"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.hub.state.firmware


class AudacVuSensor(AudacEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "dB"
    _attr_entity_registry_enabled_default = False  # off by default — chatty

    def __init__(self, coordinator: AudacCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self.index = index
        self._attr_unique_id = f"{coordinator.entry.entry_id}_vu_{index}"
        self._attr_name = f"VU {index + 1}"

    @property
    def native_value(self) -> int | None:
        levels = self.coordinator.hub.state.vu_levels
        if self.index < len(levels):
            return levels[self.index]
        return None
