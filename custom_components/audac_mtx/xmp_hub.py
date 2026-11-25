"""Hub for the AUDAC XMP44 audio player (SourceCon slots instead of zones).

Reuses AudacHub's connection handling (one TCP client, reconnect with backoff,
ack futures) and replaces the state side: GTPS tells which module sits in
which slot, then every present slot gets its gain and module-specific info.
"""
from __future__ import annotations

import asyncio
import logging

from .hub import AudacCommandRejected, AudacHub
from .models import MODEL_XMP44, PROFILES
from .protocol import Frame
from .xmp import (
    MOD_DAB,
    MOD_INTERNET,
    SLOTS,
    TRANSPORT,
    TUNERS,
    SlotState,
    gain_wire_to_db,
    parse_favourites,
    parse_presets,
    parse_psi,
    parse_pstat,
    parse_tps,
    split_slot,
    volume_to_gain_wire,
)

_LOGGER = logging.getLogger(__name__)

# Sync gets that a module may not support should not stall setup for 5 s each
SYNC_TIMEOUT = 2.0

# Update command → how its args land in SlotState
_TEXT_FIELDS = {"SON": "title", "PRGT": "title", "STN": "station", "PRGN": "station"}
_INT_FIELDS = {"FREQ": "frequency", "BND": "band", "PPTI": "position", "OG": "gain_wire"}


class XmpHub(AudacHub):
    """Persistent connection to one XMP44 (address D001)."""

    def __init__(self, host: str, port: int, source_id: str = "HA") -> None:
        super().__init__(host, port, source_id=source_id, profile=PROFILES[MODEL_XMP44])
        self.state.slots = {s: SlotState(slot=s) for s in range(1, SLOTS + 1)}
        # Slot of the last transport command; GPSTAT's answer may lack the slot digit
        self._pstat_slot: int | None = None
        self.modules_known = False  # GTPS answered at least once

    # ------------------------------------------------------------ queries
    def slot(self, slot: int) -> SlotState:
        return self.state.slots.get(slot) or SlotState(slot=slot)

    def present_slots(self) -> list[int]:
        return [s for s, st in sorted(self.state.slots.items()) if st.present]

    def slot_name(self, slot: int) -> str:
        st = self.slot(slot)
        return f"Slot {slot} {st.module_name}" if st.present else f"Slot {slot}"

    # ------------------------------------------------------------ commands
    async def set_gain_volume(self, slot: int, level: float) -> None:
        await self._send_and_ack(f"SOG{slot}", str(volume_to_gain_wire(level)))

    async def transport(self, slot: int, action: str) -> None:
        """action: PLAY, PAUS, STOP, NEXT, PREV (SPPLAYx …)."""
        self._pstat_slot = slot
        await self._send_and_ack(f"SP{action}{slot}", "0")

    async def select_preset(self, slot: int, preset: int) -> None:
        await self._send_and_ack(f"SELPR{slot}", str(preset))

    async def select_favourite(self, slot: int, pointer: str) -> None:
        await self._send_and_ack(f"DWSEST{slot}", pointer)

    async def get_firmware(self) -> str | None:
        # No GSV on the XMP; the module versions come with GTPS
        labels = [self.slot(s).label for s in self.present_slots()]
        return " / ".join(l for l in labels if l) or None

    # ------------------------------------------------------------ sync
    async def _initial_sync(self) -> None:
        try:
            await self._send_and_ack("GTPS", "0", expect_value=True)
        except (asyncio.TimeoutError, ConnectionError, AudacCommandRejected):
            _LOGGER.warning("XMP44 did not answer GTPS")
            return
        # Entities only need the module list; the per-slot details fill in as
        # they arrive (modules may leave some gets unanswered for 2 s each)
        self._synced.set()
        for slot in self.present_slots():
            for cmd, arg in self._sync_commands(self.slot(slot).module, slot):
                await self._query(cmd, arg)

    @staticmethod
    def _sync_commands(module: int, slot: int) -> list[tuple[str, str]]:
        cmds = [(f"GOG{slot}", "0")]
        if module in TRANSPORT:
            cmds += [(f"GPSTAT{slot}", "0"), (f"GPSI{slot}", "0")]
        if module == MOD_INTERNET:
            cmds += [(f"GSTN{slot}", "0"), (f"GSON{slot}", "0"), (f"GFAV{slot}", "0")]
        if module in TUNERS:
            cmds += [(f"GPRGN{slot}", "0"), (f"GFREQ{slot}", "0"), (f"GPRES{slot}", "1")]
        if module == MOD_DAB:
            cmds.append((f"GBND{slot}", "0"))
        return cmds

    async def _query(self, cmd: str, arg: str) -> None:
        if cmd.startswith("GPSTAT"):
            self._pstat_slot = int(cmd[-1])
        try:
            await self._send_and_ack(cmd, arg, expect_value=True, timeout=SYNC_TIMEOUT)
        except (asyncio.TimeoutError, ConnectionError, AudacCommandRejected):
            _LOGGER.debug("XMP44 sync %s got no answer", cmd)

    # ------------------------------------------------------------ frames
    def _dispatch(self, frame: Frame) -> None:
        # Unlike the matrices, the XMP answers gets as broadcast ("#|ALL|D001|OG1|28|")
        cmd = frame.command
        if cmd == "PSTAT" and self._pstat_target():
            cmd = f"PSTAT{self._pstat_target()}"
        if not frame.is_ack or frame.destination in (self.source_id, "web"):
            fut = self._pending.pop(cmd, None)
            if fut and not fut.done():
                fut.set_result(frame)
                if frame.is_ack:
                    return
        if not frame.is_ack:
            self._update_slots(cmd, frame.args)

    def _pstat_target(self) -> int | None:
        if self._pstat_slot:
            return self._pstat_slot
        transport = [s for s in self.present_slots() if self.slot(s).module in TRANSPORT]
        return transport[0] if len(transport) == 1 else None

    def _update_slots(self, cmd: str, args: str) -> None:
        if cmd == "TPS":
            self._apply_tps(args)
            return
        for prefix, apply in (
            ("PSTAT", lambda st: st.update(status=parse_pstat(args) or st.status)),
            ("PSI", lambda st: st.update(**parse_psi(args))),
            ("PRES", lambda st: st.update(presets=parse_presets(args))),
            ("FAV", lambda st: st.update(favourites=parse_favourites(args))),
            *((p, _text_setter(f, args)) for p, f in _TEXT_FIELDS.items()),
            *((p, _int_setter(f, args)) for p, f in _INT_FIELDS.items()),
        ):
            slot = split_slot(cmd, prefix)
            if slot is not None:
                self.state.slots[slot] = apply(self.slot(slot))
                self._notify()
                return

    def _apply_tps(self, args: str) -> None:
        self.modules_known = True
        for slot, (code, label) in parse_tps(args).items():
            self.state.slots[slot] = self.slot(slot).update(module=code, label=label)
        self.state.firmware = " / ".join(
            self.slot(s).label for s in self.present_slots() if self.slot(s).label
        ) or None
        self._notify()

    def gain_db(self, slot: int) -> int | None:
        raw = self.slot(slot).gain_wire
        return None if raw is None else gain_wire_to_db(raw)


def _text_setter(field_name: str, args: str):
    return lambda st: st.update(**{field_name: args.strip() or None})


def _int_setter(field_name: str, args: str):
    value = args.strip()
    return lambda st: st.update(**{field_name: int(value)}) if value.isdigit() else st

