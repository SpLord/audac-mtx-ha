"""Media-Player entity per MTX zone."""
from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SOURCES
from .coordinator import AudacCoordinator
from .entity import AudacEntity
from .protocol import wire_to_volume

_NAME_TO_IDX = {v: k for k, v in SOURCES.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AudacCoordinator = hass.data[DOMAIN][entry.entry_id]
    zones = coordinator.hub.zones
    async_add_entities(AudacZone(coordinator, z) for z in range(1, zones + 1))


class AudacZone(AudacEntity, MediaPlayerEntity):
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )
    _attr_source_list = [v for k, v in SOURCES.items() if k > 0]

    def __init__(self, coordinator: AudacCoordinator, zone: int) -> None:
        super().__init__(coordinator)
        self.zone = zone
        self._attr_unique_id = f"{coordinator.entry.entry_id}_zone_{zone}"
        self._attr_translation_key = "zone"
        self._attr_name = f"Zone {zone}"

    @property
    def state(self) -> MediaPlayerState:
        if not self.coordinator.hub.state.connected:
            return MediaPlayerState.OFF
        return MediaPlayerState.ON

    @property
    def _zone_state(self):
        return self.coordinator.hub.state.zones.get(self.zone)

    @property
    def volume_level(self) -> float | None:
        zs = self._zone_state
        return wire_to_volume(zs.volume_raw) if zs else None

    @property
    def is_volume_muted(self) -> bool | None:
        zs = self._zone_state
        return zs.muted if zs else None

    @property
    def source(self) -> str | None:
        zs = self._zone_state
        return SOURCES.get(zs.source) if zs else None

    async def async_set_volume_level(self, volume: float) -> None:
        await self.coordinator.hub.set_volume_pct(self.zone, int(round(volume * 100)))

    async def async_volume_up(self) -> None:
        await self.coordinator.hub.volume_up(self.zone)

    async def async_volume_down(self) -> None:
        await self.coordinator.hub.volume_down(self.zone)

    async def async_mute_volume(self, mute: bool) -> None:
        await self.coordinator.hub.set_mute(self.zone, mute)

    async def async_select_source(self, source: str) -> None:
        idx = _NAME_TO_IDX.get(source)
        if idx is None:
            return
        await self.coordinator.hub.set_source(self.zone, idx)
