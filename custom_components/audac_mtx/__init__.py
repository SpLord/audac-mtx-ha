"""AUDAC MTX integration. HA-imports are lazy so core modules stay test-friendly."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    from homeassistant.const import CONF_HOST, CONF_PORT, Platform
    from homeassistant.exceptions import ConfigEntryNotReady

    from .const import DEFAULT_PORT, DEFAULT_SOURCE_ID, DOMAIN
    from .coordinator import AudacCoordinator
    from .hub import AudacHub

    host: str = entry.data[CONF_HOST]
    port: int = entry.data.get(CONF_PORT, DEFAULT_PORT)
    zones: int = entry.data.get("zones", 8)
    source_id: str = entry.data.get("source_id", DEFAULT_SOURCE_ID)

    hub = AudacHub(host=host, port=port, zones=zones, source_id=source_id)
    try:
        await hub.async_start()
    except (OSError, TimeoutError) as err:
        raise ConfigEntryNotReady(f"Cannot reach AUDAC at {host}:{port}: {err}") from err

    fw = await hub.get_firmware()
    if fw:
        hub.state.firmware = fw

    coordinator = AudacCoordinator(hass, entry, hub)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    platforms = [Platform.MEDIA_PLAYER, Platform.NUMBER, Platform.SENSOR, Platform.BUTTON]
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    from homeassistant.const import Platform

    from .const import DOMAIN

    platforms = [Platform.MEDIA_PLAYER, Platform.NUMBER, Platform.SENSOR, Platform.BUTTON]
    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok


async def _async_update_listener(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
    await hass.config_entries.async_reload(entry.entry_id)
