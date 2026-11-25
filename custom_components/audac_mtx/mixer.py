"""HA-free helpers behind the mixer card: VU mapping and the channel layout."""
from __future__ import annotations

from collections.abc import Callable

from .models import MODEL_M2, ModelProfile

# Bar scale for the card. Observed M2 values reach the mid 20s at bar volume;
# adjust after a live look if the meters sit too low or clip.
VU_MAX = 40


def vu_by_zone(profile: ModelProfile, levels: list[int], zones: int) -> dict[int, list[int]]:
    """Map a raw VU frame to {zone: [left, right]}.

    M2: the first 16 values are outputs 1–8 as L/R pairs (verified live: muted
    and switched-off outputs read 00/00, active ones carry signal). The tail of
    the frame is not mapped. The MTX layout is undocumented → no meters.
    """
    if profile.model != MODEL_M2:
        return {}
    out: dict[int, list[int]] = {}
    for zone in range(1, zones + 1):
        i = (zone - 1) * 2
        if i + 1 < len(levels):
            out[zone] = [levels[i], levels[i + 1]]
    return out


def volume_db(profile: ModelProfile, raw: int) -> int | None:
    """Wire volume → dB (0 = max). None means "off / minimum" (shown as −∞)."""
    raw = profile.clamp_volume_raw(raw)
    if raw >= profile.vol_max_raw:
        return None
    return -raw


def build_layout(
    entry_id: str,
    profile: ModelProfile,
    zones: int,
    zone_name: Callable[[int], str],
    lookup: Callable[[str, str], str | None],
) -> dict:
    """Channel list for the card; lookup(platform, unique_id) → entity_id."""
    channels = []
    for zone in range(1, zones + 1):
        base = f"{entry_id}_zone_{zone}"
        channels.append(
            {
                "zone": zone,
                "name": zone_name(zone),
                "media_player": lookup("media_player", base),
                "bass": lookup("number", f"{base}_bass"),
                "treble": lookup("number", f"{base}_treble"),
            }
        )
    return {
        "entry_id": entry_id,
        "model": profile.model,
        "vol_max": profile.vol_max_raw,
        "vu": profile.model == MODEL_M2,
        "vu_max": VU_MAX,
        "channels": channels,
    }
