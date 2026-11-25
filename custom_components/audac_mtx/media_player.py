"""Media-Player entity per zone (MTX zone / M2 output)."""
from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
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
    zones = coordinator.hub.zones
    async_add_entities(AudacZone(coordinator, z) for z in range(1, zones + 1))


class AudacZone(AudacEntity, MediaPlayerEntity):
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_icon = "mdi:speaker"
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )

    def __init__(self, coordinator: AudacCoordinator, zone: int) -> None:
        super().__init__(coordinator)
        self.zone = zone
        self._attr_unique_id = f"{coordinator.entry.entry_id}_zone_{zone}"
        self._attr_translation_key = "zone"

    @property
    def name(self) -> str:
        # M2: output name from the device (GZN); MTX: "Zone n"
        return self.coordinator.hub.zone_name(self.zone)

    def _source_map(self) -> dict[int, str]:
        zs = self._zone_state
        return self.coordinator.hub.source_map(zs.source if zs else None)

    @property
    def source_list(self) -> list[str]:
        # includes "None" (idx 0) so HA can render an unrouted zone
        return list(self._source_map().values())

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
        return self.coordinator.hub.profile.wire_to_volume(zs.volume_raw) if zs else None

    @property
    def is_volume_muted(self) -> bool | None:
        zs = self._zone_state
        return zs.muted if zs else None

    @property
    def source(self) -> str | None:
        zs = self._zone_state
        return self._source_map().get(zs.source) if zs else None

    async def async_set_volume_level(self, volume: float) -> None:
        await self._command(self.coordinator.hub.set_volume_pct(self.zone, int(round(volume * 100))))

    async def async_volume_up(self) -> None:
        await self._command(self.coordinator.hub.volume_up(self.zone))

    async def async_volume_down(self) -> None:
        await self._command(self.coordinator.hub.volume_down(self.zone))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._command(self.coordinator.hub.set_mute(self.zone, mute))

    async def async_select_source(self, source: str) -> None:
        idx = next((i for i, n in self._source_map().items() if n == source), None)
        if idx is None:
            return
        await self._command(self.coordinator.hub.set_source(self.zone, idx))
