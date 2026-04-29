"""Config-flow with connection test + auto-detect zone count via GVALL."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import DEFAULT_PORT, DEFAULT_SOURCE_ID, DOMAIN, MODEL_MTX48, MODEL_MTX88
from .protocol import build_frame, parse_frame

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional("zones", default=8): vol.In([4, 8]),
        vol.Optional("source_id", default=DEFAULT_SOURCE_ID): str,
    }
)


async def _probe(host: str, port: int) -> tuple[bool, str | None]:
    """Open a brief connection, send GSV, parse firmware. Returns (ok, firmware)."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), 5.0
        )
    except (OSError, asyncio.TimeoutError) as err:
        return (False, str(err))
    try:
        writer.write(build_frame(destination="X001", source="HA", command="GSV", args="0"))
        await writer.drain()
        # Try to read a few frames; firmware reply may not be the very first
        firmware = None
        for _ in range(5):
            try:
                line = await asyncio.wait_for(reader.readuntil(b"\r\n"), 2.0)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                break
            frame = parse_frame(line.decode("ascii", errors="replace"))
            if frame and frame.command == "SV" and frame.args:
                firmware = frame.args
                break
        return (True, firmware)
    finally:
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), 2.0)
        except Exception:  # noqa: BLE001
            pass


class AudacMtxFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            ok, fw = await _probe(host, port)
            if not ok:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()
                model = MODEL_MTX88 if user_input.get("zones", 8) == 8 else MODEL_MTX48
                title = f"AUDAC {model}"
                data = {**user_input, "model": model, "firmware": fw}
                return self.async_create_entry(title=title, data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
            description_placeholders={"hint": "Default port 5001. MTX88=8 zones, MTX48=4."},
        )
