"""Config-flow with connection test + model auto-detection (MTX vs. M2) via GSV."""
from __future__ import annotations

import asyncio
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
from .models import MODEL_M2, MODEL_MTX48, MODEL_MTX88, PROFILES, detect_model
from .protocol import build_frame, decode_line, parse_frame

_LOGGER = logging.getLogger(__name__)

PROBE_ADDRS = ("X001", "M001")

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_MODEL, default=MODEL_AUTO): vol.In(
            [MODEL_AUTO, MODEL_M2, MODEL_MTX88, MODEL_MTX48]
        ),
        vol.Optional(CONF_SOURCE_ID, default=DEFAULT_SOURCE_ID): str,
    }
)


async def _probe(host: str, port: int) -> tuple[bool, str | None, str | None]:
    """Open a brief connection and send GSV to both the MTX and the M2 address.

    Returns (ok, firmware_or_error, answering_address). Only the matching device
    answers GSV; its address ends up in the source field of the reply.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), 5.0
        )
    except (OSError, asyncio.TimeoutError) as err:
        return (False, str(err), None)
    try:
        for addr in PROBE_ADDRS:
            writer.write(build_frame(destination=addr, source="HA", command="GSV", args="0"))
        await writer.drain()
        # The reply may be preceded by VU frames (M2 streams them constantly)
        for _ in range(40):
            try:
                line = await asyncio.wait_for(reader.readuntil(b"\r\n"), 2.0)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                break
            frame = parse_frame(decode_line(line))
            if frame and frame.command == "SV" and frame.args:
                return (True, frame.args, frame.source)
        return (True, None, None)
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
            ok, fw, addr = await _probe(host, port)
            if not ok:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()
                model = user_input.get(CONF_MODEL, MODEL_AUTO)
                if model == MODEL_AUTO:
                    model = detect_model(addr)
                profile = PROFILES[model]
                data = {
                    **user_input,
                    CONF_MODEL: model,
                    CONF_ZONES: profile.zones,
                    "firmware": fw,
                }
                return self.async_create_entry(title=f"AUDAC {model}", data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "hint": "Default port 5001. Model 'auto' detects M2 vs. MTX "
                "(an MTX is assumed to be an MTX88 — pick MTX48 for 4 zones)."
            },
        )
