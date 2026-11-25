"""Constants for the AUDAC MTX/M2 integration."""
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
DEFAULT_SOURCE_ID: Final = "HA"  # max 4 chars, no | or #
BROADCAST: Final = "ALL"
WEB_SOURCE: Final = "web"  # MTX uses this when broadcasting

# Config entry keys
CONF_MODEL: Final = "model"
CONF_ZONES: Final = "zones"
CONF_SOURCE_ID: Final = "source_id"
MODEL_AUTO: Final = "auto"

# Seconds to wait for zone/input names before entities are created (M2)
NAME_SYNC_TIMEOUT: Final = 10.0

# Model-specific values (address, ranges, inputs) live in models.py
