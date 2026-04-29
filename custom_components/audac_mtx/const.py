"""Constants for the AUDAC MTX integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "audac_mtx"

# Network
DEFAULT_PORT: Final = 5001
TIMEOUT: Final = 5.0
RECONNECT_BASE: Final = 2.0
RECONNECT_MAX: Final = 60.0

# Protocol
EOL: Final = b"\r\n"
START: Final = "#"
DEVICE_ADDR: Final = "X001"
DEFAULT_SOURCE_ID: Final = "HA"  # max 4 chars, no | or #
BROADCAST: Final = "ALL"
WEB_SOURCE: Final = "web"  # MTX uses this when broadcasting

# Models
MODEL_MTX48: Final = "MTX48"
MODEL_MTX88: Final = "MTX88"
MODEL_ZONES: Final = {MODEL_MTX48: 4, MODEL_MTX88: 8}

# Inputs (1..8) per programmer's manual
SOURCES: Final = {
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

# Bass/Treble: 0..14 raw, where 7 = 0 dB; step 2 dB → -14..+14 dB
TONE_MIN: Final = 0
TONE_MAX: Final = 14
TONE_CENTER: Final = 7
TONE_DB_PER_STEP: Final = 2

# Volume protocol: 0..70 (0 = max, 70 = min)
VOL_MIN_RAW: Final = 0
VOL_MAX_RAW: Final = 70
