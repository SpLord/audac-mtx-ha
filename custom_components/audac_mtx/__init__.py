"""AUDAC MTX/M2 integration. HA-imports are lazy so core modules stay test-friendly."""
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

    from .const import (
        CONF_MODEL,
        CONF_SOURCE_ID,
        CONF_ZONES,
        DEFAULT_PORT,
        DEFAULT_SOURCE_ID,
        DOMAIN,
        NAME_SYNC_TIMEOUT,
    )
    from .coordinator import AudacCoordinator
    from .hub import AudacHub
    from .models import MODEL_XMP44, get_profile
    from .xmp_hub import XmpHub

    host: str = entry.data[CONF_HOST]
    port: int = entry.data.get(CONF_PORT, DEFAULT_PORT)
    source_id: str = entry.data.get(CONF_SOURCE_ID, DEFAULT_SOURCE_ID)
    profile = get_profile(entry.data.get(CONF_MODEL), entry.data.get(CONF_ZONES))
    model = profile.model

    legacy_title = f"AUDAC {model} ({host})"
    if entry.title == legacy_title:
        hass.config_entries.async_update_entry(entry, title=f"AUDAC {model}")

    if model == MODEL_XMP44:
        hub = XmpHub(host=host, port=port, source_id=source_id)
    else:
        hub = AudacHub(host=host, port=port, source_id=source_id, profile=profile)
    try:
        await hub.async_start()
    except (OSError, TimeoutError) as err:
        raise ConfigEntryNotReady(f"Cannot reach AUDAC at {host}:{port}: {err}") from err

    if profile.has_names and not (
        hub.state.connected
        and await hub.async_wait_synced(NAME_SYNC_TIMEOUT)
        and hub.state.zone_names
    ):
        # Entity IDs are minted from the first name: without the device names
        # they would be stuck at "zone_n" forever → let HA retry later instead.
        await hub.async_stop()
        raise ConfigEntryNotReady(
            f"AUDAC {model} at {host}:{port} not ready: {hub.state.last_error or 'no names received'}"
        )

    if model == MODEL_XMP44 and not (
        hub.state.connected
        and await hub.async_wait_synced(NAME_SYNC_TIMEOUT)
        and hub.present_slots()
    ):
        # One media player per installed module → the slots must be known first
        reason = hub.state.last_error or (
            "no modules installed" if hub.modules_known else "no answer to GTPS"
        )
        await hub.async_stop()
        raise ConfigEntryNotReady(f"AUDAC XMP44 at {host}:{port} not ready: {reason}")

    fw = await hub.get_firmware()
    if fw:
        hub.state.firmware = fw

    coordinator = AudacCoordinator(hass, entry, hub)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    from homeassistant.loader import async_get_integration

    from .frontend import async_register_frontend

    integration = await async_get_integration(hass, DOMAIN)
    await async_register_frontend(hass, str(integration.version))

    await hass.config_entries.async_forward_entry_setups(entry, _platforms(model))
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    from .const import CONF_MODEL, CONF_ZONES, DOMAIN
    from .models import get_profile

    model = get_profile(entry.data.get(CONF_MODEL), entry.data.get(CONF_ZONES)).model
    unload_ok = await hass.config_entries.async_unload_platforms(entry, _platforms(model))
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok


async def _async_update_listener(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _platforms(model: str) -> list:
    from homeassistant.const import Platform

    from .models import MODEL_XMP44

    if model == MODEL_XMP44:
        # Source player: no tone controls, no SAVE/DEF
        return [Platform.MEDIA_PLAYER, Platform.SENSOR]
    return [Platform.MEDIA_PLAYER, Platform.NUMBER, Platform.SENSOR, Platform.BUTTON]
