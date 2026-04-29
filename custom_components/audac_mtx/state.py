"""Zone state cache."""
from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(slots=True)
class ZoneState:
    """Mutable per-zone state, fed from device updates."""

    zone: int
    volume_raw: int = 35  # 0..70 (mid)
    source: int = 0  # 0..8
    muted: bool = False
    bass_raw: int = 7  # 0..14, 7 = 0 dB
    treble_raw: int = 7

    def with_volume(self, raw: int) -> "ZoneState":
        return replace(self, volume_raw=max(0, min(70, int(raw))))

    def with_source(self, src: int) -> "ZoneState":
        return replace(self, source=max(0, min(8, int(src))))

    def with_mute(self, m: bool) -> "ZoneState":
        return replace(self, muted=bool(m))

    def with_bass(self, raw: int) -> "ZoneState":
        return replace(self, bass_raw=max(0, min(14, int(raw))))

    def with_treble(self, raw: int) -> "ZoneState":
        return replace(self, treble_raw=max(0, min(14, int(raw))))


@dataclass(slots=True)
class HubState:
    """All zones + global metadata."""

    zones: dict[int, ZoneState] = field(default_factory=dict)
    firmware: str | None = None
    vu_levels: list[int] = field(default_factory=list)
    connected: bool = False
    last_error: str | None = None

    def ensure_zone(self, zone: int) -> ZoneState:
        if zone not in self.zones:
            self.zones[zone] = ZoneState(zone=zone)
        return self.zones[zone]
