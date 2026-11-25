"""AUDAC MTX/M2 wire protocol — frame parsing and building.

Wire format (ASCII, terminated with CRLF):
    #|destination|source|command|args|checksum|<CR><LF>

- destination/source: 4-char ID (e.g. "X001" MTX, "M001" M2, "web", "HA", "ALL")
- command: e.g. "SV1", "GZI01", "VU", "V01"
- args: pipe-separated, may contain "^" for sub-fields (e.g. "20^3^0^07^07")
- checksum: CRC-16 hex OR literal "U" (always accepted as substitute)

Three frame categories from the device:
- Ack:        #|<src>|X001|<cmd>|+|U|     (mirror of cmd we sent, args="+")
- Update:     #|ALL|X001|<code><zone>|<value>|<crc>|   broadcast after change
- Unsolicited #|web|X001|VU|<10 levels>|U|              VU meter, periodic
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Frame:
    """Parsed AUDAC protocol frame."""

    destination: str
    source: str
    command: str
    args: str
    checksum: str

    @property
    def is_ack(self) -> bool:
        # M2 answers "L" instead of "+" when it rejects a command (load > 90 %)
        return self.args in ("+", "L")

    @property
    def is_broadcast(self) -> bool:
        return self.destination == "ALL"

    @property
    def is_vu(self) -> bool:
        return self.command == "VU"


def decode_line(raw: bytes) -> str:
    """Device names may contain umlauts: try UTF-8, fall back to Latin-1."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def parse_frame(line: str) -> Frame | None:
    """Parse a single line into a Frame. Returns None if malformed."""
    # M2 terminates every TCP answer with 0x00, which ends up in front of the
    # next line. A frame can also be cut off by the next one ("#|ALL#|ALL|…"),
    # so the last start symbol wins ("#" is never allowed inside a field).
    line = line.replace("\x00", "").strip()
    start = line.rfind("#")
    if start < 0:
        return None
    line = line[start:]
    parts = line[1:].split("|")
    # Expected layout: |dest|src|cmd|args|crc|  → 5 fields after stripping #
    # Some frames may have empty trailing field after final |
    parts = [p for p in parts if p != ""]
    if len(parts) < 5:
        return None
    dest, src, cmd, args, crc = parts[0], parts[1], parts[2], parts[3], parts[4]
    return Frame(destination=dest, source=src, command=cmd, args=args, checksum=crc)


def build_frame(*, destination: str, source: str, command: str, args: str = "0") -> bytes:
    """Build a wire frame ready to write. Always uses 'U' as checksum substitute."""
    line = f"#|{destination}|{source}|{command}|{args}|U|\r\n"
    return line.encode("ascii")


# ---- Volume mapping helpers ----
# Wire: 0..70 with 0 = max, 70 = min  →  HA float 0..1 with 1 = max
def wire_to_volume(raw: int) -> float:
    raw = max(0, min(70, int(raw)))
    return (70 - raw) / 70.0


def volume_to_wire(level: float) -> int:
    level = max(0.0, min(1.0, float(level)))
    return round((1.0 - level) * 70)


# ---- Tone mapping ----
# Wire 0..14 → dB -14..+14 in 2dB steps, with 7 = 0 dB
def wire_to_tone_db(raw: int) -> int:
    raw = max(0, min(14, int(raw)))
    return (raw - 7) * 2


def tone_db_to_wire(db: int) -> int:
    db = max(-14, min(14, int(db)))
    return (db // 2) + 7


# ---- Update-frame command parsing ----
# Update frames have command like V01, R02, M03, B04, T05, ZI01, VALL, RALL, MALL
# We need (kind, zone) — kind ∈ {V,R,M,B,T,ZI}, zone int or 0 for *ALL
def split_update_command(cmd: str) -> tuple[str, int] | None:
    """Return (kind, zone) for V01/R01/M01/B01/T01/ZI01-style commands.

    For VALL/RALL/MALL returns (kind_with_ALL, 0). VU returns None — handled separately.
    Returns None if not a recognised update command.
    """
    if cmd in ("VALL", "RALL", "MALL"):
        return (cmd, 0)
    for prefix in ("ZI", "V", "R", "M", "B", "T"):
        if cmd.startswith(prefix):
            tail = cmd[len(prefix):]
            if tail.isdigit():
                return (prefix, int(tail))
    return None


def reply_command(command: str) -> str:
    """Command name of the value reply to a get-command (GVALL → VALL, GB01 → B01).

    Get-commands are answered with the value under the command name minus the
    leading "G", not with a "+" ack — so that is what we wait for.
    """
    return command[1:] if command.startswith("G") else command


def parse_signed(value: str) -> int | None:
    """Parse "007", "-03" or "+3"; None if not a number."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_vu(args: str) -> list[int]:
    """MTX sends "003^000^…"; M2 sends a run of 2-digit levels without separators."""
    if "^" in args:
        return [int(x) for x in args.split("^") if x.isdigit()]
    return [int(args[i:i + 2]) for i in range(0, len(args) - 1, 2) if args[i:i + 2].isdigit()]
