"""Media-Player entity per XMP44 slot (one per installed SourceCon module)."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from .coordinator import AudacCoordinator
from .entity import AudacEntity
from .xmp import (
    MOD_INTERNET,
    STATUS_PAUSED,
    STATUS_PLAYING,
    STATUS_RECORDING,
    TRANSPORT,
    TUNERS,
    SlotState,
    GAIN_WIRE_MAX,
    format_frequency,
    gain_wire_to_volume,
)

_TRANSPORT_FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
)
_STATUS = {
    STATUS_PLAYING: MediaPlayerState.PLAYING,
    STATUS_RECORDING: MediaPlayerState.PLAYING,
    STATUS_PAUSED: MediaPlayerState.PAUSED,
}


class XmpSlotPlayer(AudacEntity, MediaPlayerEntity):
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_icon = "mdi:radio"
    # ▲/▼ in 3 dB steps like the zones (the default 10 % would be ~8 dB)
    _attr_volume_step = 3 / GAIN_WIRE_MAX

    def __init__(self, coordinator: AudacCoordinator, slot: int) -> None:
        super().__init__(coordinator)
        self.slot = slot
        self._attr_unique_id = f"{coordinator.entry.entry_id}_slot_{slot}"
        st = coordinator.hub.slot(slot)
        self._position: int | None = st.position if st.module in TRANSPORT else None
        self._position_at: datetime | None = dt_util.utcnow() if self._position is not None else None

    @property
    def _st(self) -> SlotState:
        return self.coordinator.hub.slot(self.slot)

    @property
    def name(self) -> str:
        return self.coordinator.hub.slot_name(self.slot)

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.VOLUME_STEP
        module = self._st.module
        if module in TRANSPORT:
            features |= _TRANSPORT_FEATURES
        if (module in TUNERS and self._st.presets) or (module == MOD_INTERNET and self._st.favourites):
            features |= MediaPlayerEntityFeature.SELECT_SOURCE
        return features

    @property
    def state(self) -> MediaPlayerState:
        if not self.coordinator.hub.state.connected:
            return MediaPlayerState.OFF
        if self._st.module in TRANSPORT:
            return _STATUS.get(self._st.status or "", MediaPlayerState.IDLE)
        # Tuners and internet radio play whatever is tuned
        return MediaPlayerState.PLAYING if self._st.station or self._st.frequency else MediaPlayerState.ON

    @property
    def volume_level(self) -> float | None:
        raw = self._st.gain_wire
        return None if raw is None else gain_wire_to_volume(raw)

    @property
    def media_title(self) -> str | None:
        st = self._st
        if st.module in TUNERS:
            return st.station or format_frequency(st.frequency)
        return st.title or st.station

    @property
    def media_artist(self) -> str | None:
        st = self._st
        # Radio text (PRGT) or the internet track rides along under the station
        if st.module in TUNERS or st.module == MOD_INTERNET:
            return st.title if st.station else None
        return st.artist

    @property
    def media_album_name(self) -> str | None:
        return self._st.album

    @property
    def media_duration(self) -> int | None:
        return self._st.duration if self._st.module in TRANSPORT else None

    @property
    def media_position(self) -> int | None:
        return self._position

    @property
    def media_position_updated_at(self) -> datetime | None:
        return self._position_at

    # ------------------------------------------------------------ sources
    def _sources(self) -> dict[str, str]:
        """Display name → argument (preset number or favourite pointer)."""
        st = self._st
        if st.module in TUNERS:
            return {
                f"Preset {i} ({format_frequency(f)})": str(i)
                for i, f in enumerate(st.presets, start=1)
                if f
            }
        if st.module == MOD_INTERNET:
            return {fav.name: fav.pointer for fav in st.favourites}
        return {}

    @property
    def source_list(self) -> list[str] | None:
        return list(self._sources()) or None

    @property
    def source(self) -> str | None:
        st = self._st
        if st.module in TUNERS and st.frequency:
            return next(
                (f"Preset {i} ({format_frequency(f)})" for i, f in enumerate(st.presets, start=1) if f == st.frequency),
                None,
            )
        if st.module == MOD_INTERNET:
            return st.station if st.station in self._sources() else None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        st = self._st
        return {
            "slot": self.slot,
            "module": st.module_name,
            "gain_db": self.coordinator.hub.gain_db(self.slot),
            "frequency": format_frequency(st.frequency),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        position = self._st.position if self._st.module in TRANSPORT else None
        if position != self._position:
            self._position = position
            self._position_at = dt_util.utcnow() if position is not None else None
        super()._handle_coordinator_update()

    # ------------------------------------------------------------ commands
    async def async_set_volume_level(self, volume: float) -> None:
        await self._command(self.coordinator.hub.set_gain_volume(self.slot, volume))

    async def async_media_play(self) -> None:
        await self._command(self.coordinator.hub.transport(self.slot, "PLAY"))

    async def async_media_pause(self) -> None:
        await self._command(self.coordinator.hub.transport(self.slot, "PAUS"))

    async def async_media_stop(self) -> None:
        await self._command(self.coordinator.hub.transport(self.slot, "STOP"))

    async def async_media_next_track(self) -> None:
        await self._command(self.coordinator.hub.transport(self.slot, "NEXT"))

    async def async_media_previous_track(self) -> None:
        await self._command(self.coordinator.hub.transport(self.slot, "PREV"))

    async def async_select_source(self, source: str) -> None:
        arg = self._sources().get(source)
        if arg is None:
            return
        hub = self.coordinator.hub
        if self._st.module in TUNERS:
            await self._command(hub.select_preset(self.slot, int(arg)))
        else:
            await self._command(hub.select_favourite(self.slot, arg))
