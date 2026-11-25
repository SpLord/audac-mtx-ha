"""Per-model device profiles (MTX48/MTX88 vs. M2).

Everything that differs between the AUDAC models lives here so the hub,
parser and entities stay model-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass

MODEL_MTX48 = "MTX48"
MODEL_MTX88 = "MTX88"
MODEL_M2 = "M2"

# MTX inputs are fixed in firmware — no name query exists on the wire.
_MTX_SOURCES: dict[int, str] = {
    0: "None",
    1: "Mic 1",
    2: "Mic 2",
    3: "Line 3",
    4: "Line 4",
    5: "Line 5",
    6: "Line 6",
    7: "WLI/MWX65",
    8: "WMI",
}

# M2 defaults per commands manual; replaced by GIN names at runtime.
# Selectable: direct inputs 1..9 (9 = Line In A–D). Prio inputs, test
# generators, wall-panel and fiber inputs are shown when routed but not
# offered for selection (a sine generator on a bar zone is never intended).
_M2_SOURCES: dict[int, str] = {
    0: "None",
    **{i: f"Input {i}" for i in range(1, 9)},
    9: "Line In",
}


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """Static description of one AUDAC model's protocol dialect."""

    model: str
    device_addr: str
    zones: int
    vol_max_raw: int  # raw value meaning "minimum volume"
    tone_min: int  # dB
    tone_max: int  # dB
    tone_step: int  # dB per step
    max_source_idx: int
    default_sources: dict[int, str]
    has_names: bool  # device answers GZN/GIN

    # ---- volume: wire 0 = max, vol_max_raw = min  →  HA 0..1 with 1 = max
    def wire_to_volume(self, raw: int) -> float:
        raw = max(0, min(self.vol_max_raw, int(raw)))
        return (self.vol_max_raw - raw) / self.vol_max_raw

    def volume_to_wire(self, level: float) -> int:
        level = max(0.0, min(1.0, float(level)))
        return round((1.0 - level) * self.vol_max_raw)

    def clamp_volume_raw(self, raw: int) -> int:
        # M2 reports 150 for unused/off outputs → treat as minimum
        return max(0, min(self.vol_max_raw, int(raw)))

    # ---- tone: MTX wire 0..14 (7 = 0 dB, 2 dB steps); M2 wire is signed dB
    def wire_to_tone_db(self, raw: int) -> int:
        if self.model == MODEL_M2:
            return max(self.tone_min, min(self.tone_max, int(raw)))
        raw = max(0, min(14, int(raw)))
        return (raw - 7) * 2

    def tone_db_to_wire(self, db: float) -> int:
        db = max(self.tone_min, min(self.tone_max, int(db)))
        if self.model == MODEL_M2:
            return db
        return (db // 2) + 7

    @property
    def tone_neutral_wire(self) -> int:
        return self.tone_db_to_wire(0)

    def clamp_source(self, idx: int) -> int:
        return max(0, min(self.max_source_idx, int(idx)))


PROFILES: dict[str, ModelProfile] = {
    MODEL_MTX88: ModelProfile(
        model=MODEL_MTX88, device_addr="X001", zones=8, vol_max_raw=70,
        tone_min=-14, tone_max=14, tone_step=2, max_source_idx=8,
        default_sources=_MTX_SOURCES, has_names=False,
    ),
    MODEL_MTX48: ModelProfile(
        model=MODEL_MTX48, device_addr="X001", zones=4, vol_max_raw=70,
        tone_min=-14, tone_max=14, tone_step=2, max_source_idx=8,
        default_sources=_MTX_SOURCES, has_names=False,
    ),
    MODEL_M2: ModelProfile(
        model=MODEL_M2, device_addr="M001", zones=8, vol_max_raw=100,
        tone_min=-9, tone_max=9, tone_step=1, max_source_idx=68,
        default_sources=_M2_SOURCES, has_names=True,
    ),
}


def get_profile(model: str | None, zones: int | None = None) -> ModelProfile:
    """Resolve a stored config-entry model; legacy entries only carry zones."""
    if model in PROFILES:
        return PROFILES[model]
    return PROFILES[MODEL_MTX48 if zones == 4 else MODEL_MTX88]


def detect_model(addr: str | None) -> str:
    """Map the address that answered GSV to a model (MTX48 can't be told apart)."""
    return MODEL_M2 if addr == PROFILES[MODEL_M2].device_addr else MODEL_MTX88
