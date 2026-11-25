"""Mixer card: serve the JS from the integration and feed it over the websocket.

The card gets its layout (channel names + entity IDs) and a VU stream from
here; volume/mute/source/tone go through the normal entity services. VU never
touches the HA state machine, so the recorder is not flooded with meter values.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .mixer import build_layout, vu_by_zone

CARD_FILE = Path(__file__).parent / "www" / "audac-mixer-card.js"
CARD_URL = f"/{DOMAIN}/audac-mixer-card.js"
# M2 sends ~10 VU frames/s; cap what one browser receives.
VU_MIN_INTERVAL = 0.1
_REGISTERED = f"{DOMAIN}_frontend"


async def async_register_frontend(hass: HomeAssistant, version: str) -> None:
    """Register static path, JS module and websocket commands once per HA run."""
    if hass.data.get(_REGISTERED):
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_FILE), True)]
    )
    hass.data[_REGISTERED] = True  # only after success, so a failed try is repeated
    # The iPad caches the JS hard; the version query forces a reload after updates.
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    websocket_api.async_register_command(hass, ws_get_layout)
    websocket_api.async_register_command(hass, ws_subscribe_vu)


def _coordinator(hass: HomeAssistant, entry_id: str | None) -> Any:
    """Coordinator for entry_id, or the first AUDAC entry when none is given."""
    entries = hass.data.get(DOMAIN, {})
    if entry_id:
        return entries.get(entry_id)
    return next(iter(entries.values()), None)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/get_layout", vol.Optional("entry_id"): str}
)
@callback
def ws_get_layout(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    coordinator = _coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "No AUDAC device configured")
        return
    hub = coordinator.hub
    registry = er.async_get(hass)
    layout = build_layout(
        coordinator.entry.entry_id,
        hub.profile,
        hub.zones,
        hub.zone_name,
        lambda platform, uid: registry.async_get_entity_id(platform, DOMAIN, uid),
    )
    layout["title"] = coordinator.entry.title
    connection.send_result(msg["id"], layout)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/subscribe_vu", vol.Optional("entry_id"): str}
)
@callback
def ws_subscribe_vu(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    coordinator = _coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "No AUDAC device configured")
        return
    hub = coordinator.hub
    last = 0.0

    @callback
    def forward(levels: list[int]) -> None:
        nonlocal last
        now = time.monotonic()
        if now - last < VU_MIN_INTERVAL:
            return
        last = now
        connection.send_message(
            websocket_api.event_message(
                msg["id"],
                {"vu": {str(z): lr for z, lr in vu_by_zone(hub.profile, levels, hub.zones).items()}},
            )
        )

    connection.subscriptions[msg["id"]] = hub.add_vu_listener(forward)
    connection.send_result(msg["id"])
