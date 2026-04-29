# AUDAC MTX48/88 — Home Assistant Integration

Native Home Assistant integration for the [AUDAC MTX48 and MTX88](https://audac.eu/eu/products/d/mtx88---8-zone-audio-matrix) multi-zone audio matrices, controlled over their TCP/IP protocol on port 5001.

## Features

- **Per-zone media-player entity**: volume, volume-step (3 dB), mute, source select
- **Bass / treble controls** (number entities, -14 to +14 dB in 2 dB steps)
- **VU-meter sensors** (10 levels, disabled by default)
- **Save settings & factory-reset** buttons
- **Connection / firmware diagnostics**
- **Push-driven updates** — listens to broadcasts, no constant polling
- **Auto-reconnect with exponential backoff**
- **Lockout-safe**: when the AUDAC Touch app or web UI takes the single permitted TCP slot, the integration shows "disconnected" and reconnects automatically when the slot frees up

## Why a new integration?

The MTX firmware allows **only ONE simultaneous TCP/IP connection** and broadcasts every state change to the active client. Existing community integrations modelled the protocol as simple request/response and crashed in tight loops when other clients connected — this implementation owns one persistent connection, dispatches frames into a state cache, and gracefully steps aside on lockout.

## Requirements

- Home Assistant 2024.1+
- AUDAC MTX48 or MTX88 reachable on your LAN (default port 5001)
- HACS (recommended)

## Installation

### Via HACS (recommended)

1. HACS → ⋮ → **Custom repositories**
2. URL: `https://github.com/SpLord/audac-mtx-ha`, Category: **Integration**
3. Install **AUDAC MTX**, restart Home Assistant
4. Settings → Devices & Services → **+ Add Integration** → search **AUDAC MTX**
5. Enter the device IP, port (5001), zone count (4 or 8)

### Manual

Copy `custom_components/audac_mtx/` into your `<config>/custom_components/` directory and restart HA.

## Configuration options

| Field      | Default | Notes                                          |
|------------|---------|------------------------------------------------|
| Host       | —       | IP or hostname of the MTX                      |
| Port       | 5001    | TCP control port                               |
| Zones      | 8       | 4 for MTX48, 8 for MTX88                       |
| Source ID  | HA      | 1-4 chars, identifies us in the broadcast log  |

## Entities created (per device)

| Type          | Count       | Notes                                       |
|---------------|-------------|---------------------------------------------|
| media_player  | 4 or 8      | one per zone                                |
| number        | 8 or 16     | bass + treble per zone, dB                  |
| sensor        | 13          | connection, firmware, last error, 10 × VU   |
| button        | 2           | Save settings, Factory reset (disabled)     |

VU sensors and the Last-Error / Firmware diagnostic sensors are **disabled by default** — enable them in the entity registry if you need them.

## Protocol notes

The integration speaks the official AUDAC MTX command protocol on TCP port 5001:

```
Frame: #|destination|source|command|args|U|<CR><LF>
```

Volume values are inverted on the wire (0 = max, 70 = min); we map to/from HA's 0.0-1.0 internally. Bass/treble are 0..14 raw with 7 = 0 dB.

The MTX broadcasts an Update frame to every connected client after any state change (its own front-panel knobs, the AUDAC Touch app, wall panels) — so HA stays in sync without polling.

## Limitations

- **Single TCP connection** is a hard device limit. While the AUDAC Touch app or web UI is open, this integration is "disconnected" — controls are ignored. Close the other client to regain control.
- VU-meter mapping (which physical input/output corresponds to which of the 10 levels) varies by firmware/configuration; the entities are exposed as `VU 1`..`VU 10` and you can rename them to match your setup.

## Tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install pytest pytest-asyncio pytest-timeout voluptuous
pytest tests
```

Tests use an in-process Mock-MTX server — no hardware required.

## License

MIT
