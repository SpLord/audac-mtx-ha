"""Integration test: XmpHub against an in-process mock XMP44 (frames from the manual)."""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from audac_mtx.xmp import MOD_EMPTY, MOD_FM, MOD_INTERNET, MOD_MEDIA, STATUS_PAUSED, STATUS_PLAYING
from audac_mtx.xmp_hub import XmpHub

TPS = "3^4^2^15^MMP40 V1.0^IMP40 V 1.0.4^TMP40 ^No Module "


class MockXmp:
    """D001 device: gets are answered as ALL broadcasts, sets with ack + update."""

    def __init__(self) -> None:
        self.received: list[str] = []
        self.gain = {1: "28", 2: "8", 3: "0"}
        self.presets_supported = True  # the manual lists GPRES only for the DMP40
        self._server: asyncio.base_events.Server | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    @staticmethod
    def _send(w: asyncio.StreamWriter, dest: str, cmd: str, args: str) -> None:
        w.write(f"#|{dest}|D001|{cmd}|{args}|abcd|\r\n".encode())

    def _reply(self, w: asyncio.StreamWriter, src: str, cmd: str, args: str) -> None:
        gets = {
            "GTPS": ("TPS", TPS),
            "GPSTAT1": ("PSTAT", "1^0^0"),  # the manual's answer has no slot digit
            "GPSI1": ("PSI1", "Song^Artist^Album^200^12"),
            "GSTN2": ("STN2", "Studio Brussel"),
            "GSON2": ("SON2", "Some track"),
            "GFAV2": ("FAV2", "0^Studio Brussel^4712^1^Radio 1^4713"),
            "GPRGN3": ("PRGN3", "Radio Saar"),
            "GFREQ3": ("FREQ3", "10410"),
        }
        if cmd in gets:
            self._send(w, "ALL", *gets[cmd])
        elif cmd.startswith("GOG"):
            self._send(w, "ALL", f"OG{cmd[-1]}", self.gain[int(cmd[-1])])
        elif cmd == "GPRES3" and self.presets_supported:
            self._send(w, src, "PRES3", "1^10360^103.60 ^1^10000^100.00 ")
        elif cmd.startswith("S") or cmd.startswith("DWSEST"):
            self._send(w, src, cmd, "+")
            if cmd.startswith("SOG"):
                self._send(w, "ALL", f"OG{cmd[-1]}", args)
            elif cmd == "SPPLAY1":
                self._send(w, "ALL", "PSTAT", "0^1^0")
            elif cmd == "DWSEST2":
                self._send(w, "ALL", "STN2", "Radio 1")
        # anything else (e.g. GPSI on a module without it) stays unanswered

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = (await reader.readuntil(b"\r\n")).decode().strip()
                self.received.append(line)
                parts = line.split("|")
                if len(parts) >= 5 and parts[1] == "D001":
                    self._reply(writer, parts[2], parts[3], parts[4])
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass


@pytest_asyncio.fixture
async def xmp():
    server = MockXmp()
    await server.start()
    hub = XmpHub("127.0.0.1", server.port)
    await hub.async_start()
    assert await hub.async_wait_synced(10)
    yield server, hub
    await hub.async_stop()
    await server.stop()


@pytest.mark.asyncio
async def test_sync_reads_slots(xmp) -> None:
    _, hub = xmp
    assert hub.present_slots() == [1, 2, 3]
    assert hub.slot(4).module == MOD_EMPTY
    s1, s2, s3 = hub.slot(1), hub.slot(2), hub.slot(3)
    assert s1.module == MOD_MEDIA and s1.status == STATUS_PAUSED
    assert (s1.title, s1.artist, s1.duration, s1.position) == ("Song", "Artist", 200, 12)
    assert hub.gain_db(1) == -20
    assert s2.module == MOD_INTERNET and s2.station == "Studio Brussel"
    assert [f.name for f in s2.favourites] == ["Studio Brussel", "Radio 1"]
    assert s3.module == MOD_FM and s3.frequency == 10410 and s3.station == "Radio Saar"
    assert s3.presets == (10360, 10000)
    assert hub.state.firmware == "MMP40 V1.0 / IMP40 V 1.0.4 / TMP40"
    assert hub.slot_name(2) == "Slot 2 IMP40"


@pytest.mark.asyncio
async def test_commands(xmp) -> None:
    server, hub = xmp
    await hub.set_gain_volume(1, 1.0)
    await hub.transport(1, "PLAY")
    await hub.select_favourite(2, "4713")
    await hub.select_preset(3, 4)
    await asyncio.sleep(0.1)
    assert hub.slot(1).gain_wire == 0
    assert hub.slot(1).status == STATUS_PLAYING
    assert hub.slot(2).station == "Radio 1"
    sent = [l.split("|")[3:5] for l in server.received]
    assert ["SOG1", "0"] in sent and ["SPPLAY1", "0"] in sent
    assert ["DWSEST2", "4713"] in sent and ["SELPR3", "4"] in sent


@pytest.mark.asyncio
async def test_unanswered_get_does_not_delay_setup() -> None:
    server = MockXmp()
    server.presets_supported = False
    await server.start()
    hub = XmpHub("127.0.0.1", server.port)
    try:
        await hub.async_start()
        # Setup only waits for the module list, not for every per-slot answer
        assert await hub.async_wait_synced(1.0)
        assert hub.present_slots() == [1, 2, 3] and hub.modules_known
        await asyncio.sleep(2.5)  # GPRES3 times out after 2 s, sync goes on
        assert hub.slot(3).presets == ()
        assert hub.slot(3).frequency == 10410 and hub.slot(1).title == "Song"
    finally:
        await hub.async_stop()
        await server.stop()
