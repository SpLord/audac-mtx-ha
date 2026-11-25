"""AUDAC XMP44 (SourceCon audio player): module types, slot state, frame parsing.

HA-free. Built from the "Audio sources commands manual" (no live device yet):
address D001, port 5001, one TCP client, every command carries the slot
number ("SOG1"), get-replies drop the leading G ("GOG1" → "OG1").
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

SLOTS = 4

# GTPS module codes
MOD_DAB = 1  # DMP40 (DAB/DAB+ & FM)
MOD_FM = 2  # TMP40
MOD_MEDIA = 3  # MMP40 (media player / recorder)
MOD_INTERNET = 4  # IMP40
MOD_VOICE = 6  # FMP40 (voice file player)
MOD_BLUETOOTH = 8  # BMP40
MOD_EMPTY = 15
MOD_UNSUPPORTED = 255

MODULE_NAMES: dict[int, str] = {
    MOD_DAB: "DMP40",
    MOD_FM: "TMP40",
    MOD_MEDIA: "MMP40",
    MOD_INTERNET: "IMP40",
    MOD_VOICE: "FMP40",
    MOD_BLUETOOTH: "BMP40",
}
TUNERS = frozenset({MOD_DAB, MOD_FM})
TRANSPORT = frozenset({MOD_MEDIA, MOD_BLUETOOTH})

# Output gain: wire 0 = +8 dB, 8 = 0 dB, 28 = -20 dB. The bottom of the range
# "depends on the module type" — provisional, verify on a real device.
GAIN_MAX_DB = 8
GAIN_MIN_DB = -70
GAIN_WIRE_MAX = GAIN_MAX_DB - GAIN_MIN_DB

PRESETS = 10

# Status codes kept in SlotState.status
STATUS_STOPPED = "stopped"
STATUS_PLAYING = "playing"
STATUS_PAUSED = "paused"
STATUS_RECORDING = "recording"


@dataclass(frozen=True, slots=True)
class Favourite:
    """Internet radio favourite: pointer is what DWSEST expects."""

    index: int
    name: str
    pointer: str


@dataclass(frozen=True, slots=True)
class SlotState:
    """One SourceCon slot. Immutable; the hub replaces it on every update."""

    slot: int
    module: int = MOD_EMPTY
    label: str = ""  # GTPS text, e.g. "IMP40 V 1.0.4"
    gain_wire: int | None = None
    status: str | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration: int | None = None
    position: int | None = None
    station: str | None = None  # tuner program name / internet station name
    frequency: int | None = None  # FM: 10410 = 104.10 MHz
    band: int | None = None  # DMP40: 0 = DAB, 1 = FM
    presets: tuple[int | None, ...] = field(default_factory=tuple)
    favourites: tuple[Favourite, ...] = field(default_factory=tuple)

    @property
    def present(self) -> bool:
        return self.module in MODULE_NAMES

    @property
    def module_name(self) -> str:
        return MODULE_NAMES.get(self.module, "Unknown")

    def update(self, **changes: object) -> "SlotState":
        return replace(self, **changes)


# ------------------------------------------------------------------ gain
def gain_db_to_wire(db: float) -> int:
    db = max(GAIN_MIN_DB, min(GAIN_MAX_DB, round(db)))
    return GAIN_MAX_DB - db


def gain_wire_to_db(raw: int) -> int:
    return GAIN_MAX_DB - max(0, min(GAIN_WIRE_MAX, int(raw)))


def gain_wire_to_volume(raw: int) -> float:
    """Wire → HA 0..1 (1 = +8 dB)."""
    return (GAIN_WIRE_MAX - max(0, min(GAIN_WIRE_MAX, int(raw)))) / GAIN_WIRE_MAX


def volume_to_gain_wire(level: float) -> int:
    level = max(0.0, min(1.0, float(level)))
    return round((1.0 - level) * GAIN_WIRE_MAX)


# ------------------------------------------------------------------ parsing
def split_slot(command: str, prefix: str) -> int | None:
    """"OG3" with prefix "OG" → 3; None if it is not that command."""
    if not command.startswith(prefix):
        return None
    tail = command[len(prefix):]
    if len(tail) == 1 and tail.isdigit() and 1 <= int(tail) <= SLOTS:
        return int(tail)
    return None


def parse_tps(args: str) -> dict[int, tuple[int, str]]:
    """"4^1^15^6^IMP40 V 1.0.4^DMP40 ^No Module ^FMP40 V1.4.29" → slot → (code, label)."""
    parts = args.split("^")
    codes, labels = parts[:SLOTS], parts[SLOTS:]
    result: dict[int, tuple[int, str]] = {}
    for i, code in enumerate(codes, start=1):
        code = code.strip()
        if not code.isdigit():
            continue
        label = labels[i - 1].strip() if i - 1 < len(labels) else ""
        result[i] = (int(code), label)
    return result


def parse_pstat(args: str) -> str | None:
    """"paused^playing^recording" flags → status."""
    flags = args.split("^")
    if len(flags) < 3 or not all(f.strip() in ("0", "1") for f in flags[:3]):
        return None
    paused, playing, recording = (f.strip() == "1" for f in flags[:3])
    if recording:
        return STATUS_RECORDING
    if playing:
        return STATUS_PLAYING
    if paused:
        return STATUS_PAUSED
    return STATUS_STOPPED


def _int_or_none(value: str) -> int | None:
    value = value.strip()
    return int(value) if value.isdigit() else None


def parse_psi(args: str) -> dict[str, object]:
    """"song^artist^album^length^played[^image]" → SlotState fields."""
    parts = args.split("^")
    parts += [""] * (5 - len(parts))
    return {
        "title": parts[0].strip() or None,
        "artist": parts[1].strip() or None,
        "album": parts[2].strip() or None,
        "duration": _int_or_none(parts[3]),
        "position": _int_or_none(parts[4]),
    }


def parse_presets(args: str) -> tuple[int | None, ...]:
    """"1^10360^103.60 ^1^10000^100.00 …" → frequency per preset (index 0 = preset 1)."""
    parts = args.split("^")
    freqs: list[int | None] = []
    for i in range(0, len(parts) - 2, 3):
        freqs.append(_int_or_none(parts[i + 1]))
    return tuple(freqs[:PRESETS])


def parse_favourites(args: str) -> tuple[Favourite, ...]:
    """"0^Studio Brussel^4712^1^…" → favourites with a name and a pointer."""
    parts = args.split("^")
    favs: list[Favourite] = []
    for i in range(0, len(parts) - 2, 3):
        idx, name, pointer = parts[i].strip(), parts[i + 1].strip(), parts[i + 2].strip()
        if idx.isdigit() and name and pointer:
            favs.append(Favourite(int(idx), name, pointer))
    return tuple(favs)


def format_frequency(freq: int | None) -> str | None:
    return None if freq is None else f"{freq / 100:.2f} MHz"
