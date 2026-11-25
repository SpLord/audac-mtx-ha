"""Config flow: network search, manual entry and DHCP discovery.

The model (MTX / M2 / XMP44) is detected by asking every AUDAC address who
answers (see discovery.py).
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import (
    CONF_MODEL,
    CONF_SOURCE_ID,
    CONF_ZONES,
    DEFAULT_PORT,
    DEFAULT_SOURCE_ID,
    DOMAIN,
    MODEL_AUTO,
)
from .discovery import ProbeResult, probe, scan, scan_hosts
from .models import MODEL_M2, MODEL_MTX48, MODEL_MTX88, MODEL_XMP44, PROFILES

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE = "device"

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_MODEL, default=MODEL_AUTO): vol.In(
            [MODEL_AUTO, MODEL_M2, MODEL_MTX88, MODEL_MTX48, MODEL_XMP44]
        ),
        vol.Optional(CONF_SOURCE_ID, default=DEFAULT_SOURCE_ID): str,
    }
)


def _label(host: str, port: int, result: ProbeResult) -> str:
    fw = f", {result.firmware}" if result.firmware else ""
    suffix = "" if port == DEFAULT_PORT else f":{port}"
    return f"AUDAC {result.model} – {host}{suffix}{fw}"


class AudacMtxFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._found: dict[str, ProbeResult] = {}
        self._discovered: tuple[str, ProbeResult] | None = None

    # ------------------------------------------------------------ entry points
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["scan", "manual"])

    async def async_step_scan(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            host = user_input[CONF_DEVICE]
            return await self._async_create(host, DEFAULT_PORT, self._found[host])

        configured = {e.data.get(CONF_HOST) for e in self._async_current_entries()}
        # Skip configured devices: the MTX allows one client, a probe would kick HA out
        hosts = [h for h in scan_hosts(await self._async_interfaces()) if h not in configured]
        self._found = await scan(hosts, DEFAULT_PORT)
        if not self._found:
            return self.async_show_form(
                step_id="manual", data_schema=DATA_SCHEMA, errors={"base": "no_devices_found"}
            )
        options = {h: _label(h, DEFAULT_PORT, r) for h, r in sorted(self._found.items())}
        return self.async_show_form(
            step_id="scan",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE): vol.In(options)}),
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            result = await probe(host, port)
            if not result.reachable:
                errors["base"] = "cannot_connect"
            else:
                model = user_input.get(CONF_MODEL, MODEL_AUTO)
                if model == MODEL_AUTO:
                    # Connected but nobody answered: historically an MTX88
                    model = result.model or MODEL_MTX88
                return await self._async_create(
                    host,
                    port,
                    ProbeResult(True, model, result.firmware),
                    source_id=user_input.get(CONF_SOURCE_ID, DEFAULT_SOURCE_ID),
                )
        return self.async_show_form(step_id="manual", data_schema=DATA_SCHEMA, errors=errors)

    async def async_step_dhcp(self, discovery_info: Any) -> ConfigFlowResult:
        """A device with an AUDAC network module (OUI 54:10:EC) got a DHCP lease."""
        host = discovery_info.ip
        await self.async_set_unique_id(f"{host}:{DEFAULT_PORT}")
        self._abort_if_unique_id_configured()
        if any(e.data.get(CONF_HOST) == host for e in self._async_current_entries()):
            return self.async_abort(reason="already_configured")
        # The OUI belongs to a chip vendor, so ask whether it really is AUDAC
        result = await probe(host, DEFAULT_PORT)
        if not result.model:
            return self.async_abort(reason="not_audac_device")
        self._discovered = (host, result)
        self.context["title_placeholders"] = {"name": f"AUDAC {result.model} ({host})"}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovered is not None
        host, result = self._discovered
        if user_input is not None:
            return await self._async_create(host, DEFAULT_PORT, result)
        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"model": result.model or "", "host": host},
        )

    # ------------------------------------------------------------ helpers
    async def _async_interfaces(self) -> list[str]:
        from homeassistant.components import network

        interfaces: list[str] = []
        for adapter in await network.async_get_adapters(self.hass):
            if not adapter["enabled"]:
                continue
            interfaces.extend(f"{ip['address']}/{ip['network_prefix']}" for ip in adapter["ipv4"])
        return interfaces

    async def _async_create(
        self,
        host: str,
        port: int,
        result: ProbeResult,
        source_id: str = DEFAULT_SOURCE_ID,
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(f"{host}:{port}")
        self._abort_if_unique_id_configured()
        model = result.model or MODEL_MTX88
        data = {
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_MODEL: model,
            CONF_ZONES: PROFILES[model].zones,
            CONF_SOURCE_ID: source_id,
            "firmware": result.firmware,
        }
        return self.async_create_entry(title=f"AUDAC {model}", data=data)
