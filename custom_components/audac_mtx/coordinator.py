"""DataUpdateCoordinator — push-driven (no polling)."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .hub import AudacHub
from .state import HubState

_LOGGER = logging.getLogger(__name__)


class AudacCoordinator(DataUpdateCoordinator[HubState]):
    """Wraps the hub for HA: hub-listener → coordinator-update → entity refresh."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, hub: AudacHub) -> None:
        super().__init__(hass, _LOGGER, name=f"audac_mtx_{entry.entry_id}")
        self.hub = hub
        self.entry = entry
        self._unsub = hub.add_listener(self._on_hub_change)

    def _on_hub_change(self) -> None:
        # hub callback is sync; schedule HA-side update
        self.hass.loop.call_soon_threadsafe(
            self.async_set_updated_data, self.hub.state
        )

    async def async_shutdown(self) -> None:
        self._unsub()
        await self.hub.async_stop()
        await super().async_shutdown()

    async def _async_update_data(self) -> HubState:
        # Push-driven: just return the current cache. Real updates come via _on_hub_change.
        return self.hub.state
