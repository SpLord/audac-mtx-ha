"""Bass and Treble number entities per zone."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfSoundPressure
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AudacCoordinator
from .entity import AudacEntity
from .protocol import tone_db_to_wire, wire_to_tone_db


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AudacCoordinator = hass.data[DOMAIN][entry.entry_id]
    items: list[NumberEntity] = []
    for z in range(1, coordinator.hub.zones + 1):
        items.append(AudacToneNumber(coordinator, z, "bass"))
        items.append(AudacToneNumber(coordinator, z, "treble"))
    async_add_entities(items)


class AudacToneNumber(AudacEntity, NumberEntity):
    _attr_native_min_value = -14
    _attr_native_max_value = 14
    _attr_native_step = 2
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = "dB"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: AudacCoordinator, zone: int, kind: str) -> None:
        super().__init__(coordinator)
        self.zone = zone
        self.kind = kind  # "bass" or "treble"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_zone_{zone}_{kind}"
        self._attr_name = f"Zone {zone} {kind.capitalize()}"

    @property
    def native_value(self) -> float | None:
        zs = self.coordinator.hub.state.zones.get(self.zone)
        if zs is None:
            return None
        raw = zs.bass_raw if self.kind == "bass" else zs.treble_raw
        return wire_to_tone_db(raw)

    async def async_set_native_value(self, value: float) -> None:
        raw = tone_db_to_wire(int(value))
        if self.kind == "bass":
            await self.coordinator.hub.set_bass_raw(self.zone, raw)
        else:
            await self.coordinator.hub.set_treble_raw(self.zone, raw)
