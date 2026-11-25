"""Zone state cache. Values are stored as wire values; the hub clamps them per model."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .xmp import SlotState


@dataclass(slots=True)
class ZoneState:
    """Mutable per-zone state, fed from device updates."""

    zone: int
    volume_raw: int = 35  # 0 = max; MTX 0..70, M2 0..100
    source: int = 0  # input index, 0 = none
    muted: bool = False
    bass_raw: int = 7  # MTX 0..14 (7 = 0 dB); M2 signed dB
    treble_raw: int = 7

    def with_volume(self, raw: int) -> "ZoneState":
        return replace(self, volume_raw=int(raw))

    def with_source(self, src: int) -> "ZoneState":
        return replace(self, source=int(src))

    def with_mute(self, m: bool) -> "ZoneState":
        return replace(self, muted=bool(m))

    def with_bass(self, raw: int) -> "ZoneState":
        return replace(self, bass_raw=int(raw))

    def with_treble(self, raw: int) -> "ZoneState":
        return replace(self, treble_raw=int(raw))


@dataclass(slots=True)
class HubState:
    """All zones + global metadata."""

    zones: dict[int, ZoneState] = field(default_factory=dict)
    firmware: str | None = None
    vu_levels: list[int] = field(default_factory=list)
    connected: bool = False
    last_error: str | None = None
    # Names reported by the device (M2 only): zone → name, input → name
    zone_names: dict[int, str] = field(default_factory=dict)
    input_names: dict[int, str] = field(default_factory=dict)
    # XMP44 only: SourceCon slot → module state
    slots: dict[int, SlotState] = field(default_factory=dict)

    def ensure_zone(self, zone: int) -> ZoneState:
        if zone not in self.zones:
            self.zones[zone] = ZoneState(zone=zone)
        return self.zones[zone]
