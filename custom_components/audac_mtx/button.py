"""Buttons: Save settings, Factory reset."""
from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
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
    async_add_entities([
        AudacSaveButton(coordinator),
        AudacResetButton(coordinator),
    ])


class AudacSaveButton(AudacEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Save settings"
    _attr_icon = "mdi:content-save"

    def __init__(self, coordinator: AudacCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_save"

    async def async_press(self) -> None:
        await self.coordinator.hub.save_settings()


class AudacResetButton(AudacEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_name = "Factory reset"
    _attr_icon = "mdi:restore"
    _attr_entity_registry_enabled_default = False  # destructive — opt-in

    def __init__(self, coordinator: AudacCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_reset"

    async def async_press(self) -> None:
        await self.coordinator.hub.factory_reset()
