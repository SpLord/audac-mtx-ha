"""Integration test: Hub (M2 profile) against an in-process mock M2."""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from audac_mtx.hub import AudacCommandRejected, AudacHub
from audac_mtx.models import MODEL_M2, PROFILES

ZONE_NAMES = "Output 1^Raum2^Output 3^Output 4^Output 5^Output 6^Output 7^Output 8^NOT USED^WP Out 1"
INPUT_NAMES = (
    "Bar^DJ^Input 3^Input 4^Input 5^Input 6^Input 7^Input 8^Line In^Voice Fl^NOT USED"
    "^Prio 1^Prio 2^Sine^W Noise^P Noise"
)


class MockM2Server:
    """Answers like the real M2: M001 address, every frame followed by 0x00."""

    def __init__(self) -> None:
        self.host = "127.0.0.1"
        self.port = 0
        self.received: list[str] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.overloaded = False
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for w in self.writers:
            w.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def _send(self, w: asyncio.StreamWriter, dest: str, cmd: str, args: str) -> None:
        w.write(f"#|{dest}|M001|{cmd}|{args}|abcd|\r\n\x00".encode())

    def _reply(self, w: asyncio.StreamWriter, src: str, cmd: str, args: str) -> None:
        replies = {
            "GSV": ("SV", "2.10"),
            "GZN": ("ZN", ZONE_NAMES),
            "GIN": ("IN", INPUT_NAMES),
            "GVALL": ("VALL", "039^035^029^035^031^050^150^033"),
            "GRALL": ("RALL", "009^001^009^009^009^000^000^009"),
            "GMALL": ("MALL", "0^0^0^0^1^1^1^0"),
        }
        if cmd in replies:
            self._send(w, src, *replies[cmd])
        elif cmd.startswith(("GB", "GT")):
            self._send(w, src, f"{cmd[1]}{cmd[2:]}", "-03" if cmd == "GB02" else "000")
        elif cmd.startswith("S"):
            if self.overloaded:
                self._send(w, src, cmd, "L")
                return
            self._send(w, src, cmd, "+")
            zone = int(cmd[2:])
            if cmd.startswith("SR"):
                self._send(w, "ALL", f"R{zone:02d}", f"000^{int(args):03d}")
            elif cmd.startswith(("SB", "ST", "SV", "SM")):
                kind = {"SB": "B", "ST": "T", "SV": "V", "SM": "M"}[cmd[:2]]
                self._send(w, "ALL", f"{kind}{zone:02d}", args)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        # The real M2 starts streaming VU frames right away, with a stray fragment
        writer.write(b"#|ALL#|ALL|M001|VU|100614142020141400000000000016160000040|f077|\r\n\x00")
        try:
            while True:
                line = (await reader.readuntil(b"\r\n")).decode().strip()
                self.received.append(line)
                parts = line.split("|")
                if len(parts) < 5 or parts[1] != "M001":
                    continue  # M2 ignores frames for other addresses (X001)
                self._reply(writer, parts[2], parts[3], parts[4])
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass


@pytest_asyncio.fixture
async def m2():
    s = MockM2Server()
    await s.start()
    yield s
    await s.stop()


async def _started_hub(server: MockM2Server) -> AudacHub:
    hub = AudacHub(host=server.host, port=server.port, profile=PROFILES[MODEL_M2])
    await hub.async_start()
    assert await hub.async_wait_synced(3.0)
    return hub


async def test_initial_sync_reads_state_and_names(m2):
    hub = await _started_hub(m2)
    z = hub.state.zones
    assert z[1].volume_raw == 39
    assert z[7].volume_raw == 100  # 150 = off → clamped to minimum
    assert z[2].source == 1
    assert z[5].muted and not z[1].muted
    assert z[2].bass_raw == -3
    assert z[1].treble_raw == 0
    assert hub.zone_name(2) == "Raum2"
    assert len(hub.state.zone_names) == 8
    assert hub.source_name(1) == "Bar"
    assert hub.state.firmware is None  # set by setup via get_firmware
    assert await hub.get_firmware() == "2.10"
    # Every command went to the M2 address
    assert all(line.startswith("#|M001|HA|") for line in m2.received)
    await hub.async_stop()


async def test_source_map_uses_device_names(m2):
    hub = await _started_hub(m2)
    names = hub.source_map()
    assert names[0] == "None"
    assert names[1] == "Bar"
    assert names[9] == "Line In"
    assert 14 not in names  # sine generator is not offered
    # A routed prio input shows up so the UI can display it
    assert hub.source_map(current=12)[12] == "Prio 1"
    await hub.async_stop()


async def test_set_commands_update_cache(m2):
    hub = await _started_hub(m2)
    await hub.set_volume_pct(3, 50)
    await hub.set_source(3, 2)
    await hub.set_bass_db(3, -5)
    await hub.set_treble_db(3, 12)  # clamped to +9
    await hub.set_mute(3, True)
    await asyncio.sleep(0.1)
    z3 = hub.state.zones[3]
    assert (z3.volume_raw, z3.source, z3.bass_raw, z3.treble_raw, z3.muted) == (50, 2, -5, 9, True)
    assert "#|M001|HA|SV03|50|U|" in m2.received
    assert "#|M001|HA|SB03|-5|U|" in m2.received
    await hub.async_stop()


async def test_overload_ack_raises(m2):
    hub = await _started_hub(m2)
    m2.overloaded = True
    with pytest.raises(AudacCommandRejected):
        await hub.set_volume_pct(1, 10)
    await hub.async_stop()


async def test_vu_parsed_as_pairs(m2):
    hub = await _started_hub(m2)
    assert hub.state.vu_levels[:4] == [10, 6, 14, 14]
    await hub.async_stop()
