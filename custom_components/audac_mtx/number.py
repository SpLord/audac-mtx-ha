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
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = "dB"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: AudacCoordinator, zone: int, kind: str) -> None:
        super().__init__(coordinator)
        self.zone = zone
        self.kind = kind  # "bass" or "treble"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_zone_{zone}_{kind}"
        profile = coordinator.hub.profile
        self._attr_native_min_value = profile.tone_min
        self._attr_native_max_value = profile.tone_max
        self._attr_native_step = profile.tone_step

    @property
    def name(self) -> str:
        return f"{self.coordinator.hub.zone_name(self.zone)} {self.kind.capitalize()}"

    @property
    def native_value(self) -> float | None:
        zs = self.coordinator.hub.state.zones.get(self.zone)
        if zs is None:
            return None
        raw = zs.bass_raw if self.kind == "bass" else zs.treble_raw
        return self.coordinator.hub.profile.wire_to_tone_db(raw)

    async def async_set_native_value(self, value: float) -> None:
        if self.kind == "bass":
            await self._command(self.coordinator.hub.set_bass_db(self.zone, value))
        else:
            await self._command(self.coordinator.hub.set_treble_db(self.zone, value))
