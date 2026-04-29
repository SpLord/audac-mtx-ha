"""Integration test: Hub against an in-process Mock-MTX TCP server."""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio


from audac_mtx.hub import AudacHub


class MockMtxServer:
    """Minimal MTX server: replies with ack + broadcast update + occasional VU."""

    def __init__(self) -> None:
        self.host = "127.0.0.1"
        self.port = 0  # OS-assigned
        self.connections: list[asyncio.StreamWriter] = []
        self._tasks: set[asyncio.Task] = set()
        self.received: list[str] = []
        self._server: asyncio.base_events.Server | None = None
        self._max_connections = 1  # match real device

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        # Close all client writers first to unblock their readers
        for w in list(self.connections):
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass
        # Cancel handler tasks
        for t in list(self._tasks):
            t.cancel()
        # Close server
        if self._server:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 1.0)
            except (asyncio.TimeoutError, Exception):
                pass
        # Wait briefly for handler tasks
        for t in list(self._tasks):
            try:
                await asyncio.wait_for(t, 0.5)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        self._tasks.clear()
        self.connections.clear()


    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._tasks.add(asyncio.current_task())
        try:
            await self._handle_inner(reader, writer)
        finally:
            self._tasks.discard(asyncio.current_task())

    async def _handle_inner(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self.connections) >= self._max_connections:
            # Mimic device: lock out additional clients
            writer.close()
            await writer.wait_closed()
            return
        self.connections.append(writer)
        try:
            while True:
                data = await reader.readuntil(b"\r\n")
                line = data.decode("ascii", errors="replace")
                self.received.append(line.strip())
                # Parse the cmd
                parts = line.strip().strip("\r\n").split("|")
                if len(parts) < 5:
                    continue
                src, cmd = parts[2], parts[3]
                args = parts[4] if len(parts) > 4 else "0"
                # Reply with ack
                ack = f"#|{src}|X001|{cmd}|+|U|\r\n".encode()
                writer.write(ack)
                # If it's a get-something command, also send value
                if cmd.startswith("GZI"):
                    zone = cmd[3:]
                    writer.write(f"#|web|X001|ZI{zone}|20^3^0^07^07|U|\r\n".encode())
                elif cmd == "GVALL":
                    writer.write(b"#|web|X001|VALL|40^40^20^20^20^20^20^20|U|\r\n")
                elif cmd == "GRALL":
                    writer.write(b"#|web|X001|RALL|3^1^1^1^1^1^1^1|U|\r\n")
                elif cmd == "GMALL":
                    writer.write(b"#|web|X001|MALL|0^0^0^0^0^0^0^0|U|\r\n")
                elif cmd == "GSV":
                    writer.write(b"#|web|X001|SV|V1.1|U|\r\n")
                elif cmd.startswith("SV") and not cmd.startswith("SVU") and not cmd.startswith("SVD"):
                    # Set Volume — also broadcast update
                    zone = cmd[2:].lstrip("0") or "0"
                    writer.write(f"#|ALL|X001|V{int(zone):02d}|{args}|7378|\r\n".encode())
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            try:
                self.connections.remove(writer)
            except ValueError:
                pass

    async def push_vu(self) -> None:
        """Broadcast a VU frame to all connected clients."""
        for w in list(self.connections):
            try:
                w.write(b"#|web|X001|VU|003^000^000^002^001^006^000^000^000^000|U|\r\n")
                await w.drain()
            except ConnectionError:
                pass

    async def push_state_change(self, zone: int, raw: int) -> None:
        """Simulate another client (Audac Touch) changing a zone volume."""
        for w in list(self.connections):
            try:
                w.write(f"#|ALL|X001|V{zone:02d}|{raw}|7378|\r\n".encode())
                await w.drain()
            except ConnectionError:
                pass


@pytest_asyncio.fixture
async def server():
    s = MockMtxServer()
    await s.start()
    yield s
    await s.stop()


@pytest.mark.asyncio
async def test_initial_sync_populates_state(server):
    hub = AudacHub(host=server.host, port=server.port, zones=8)
    await hub.async_start()
    # After start, initial_sync runs in recv-loop; give it a moment
    await asyncio.sleep(0.3)
    assert hub.state.connected
    z1 = hub.state.zones[1]
    assert z1.volume_raw == 20  # final value from GZI01 (overrides GVALL)
    assert z1.source == 3
    assert not z1.muted
    await hub.async_stop()


@pytest.mark.asyncio
async def test_set_volume_broadcasts_to_state(server):
    hub = AudacHub(host=server.host, port=server.port, zones=8)
    await hub.async_start()
    await asyncio.sleep(0.2)
    await hub.set_volume_pct(2, 50)
    # Set 50% → wire 35 → broadcast V02|35
    await asyncio.sleep(0.1)
    assert hub.state.zones[2].volume_raw == 35
    await hub.async_stop()


@pytest.mark.asyncio
async def test_unsolicited_vu_updates_state(server):
    hub = AudacHub(host=server.host, port=server.port, zones=8)
    await hub.async_start()
    await asyncio.sleep(0.2)
    await server.push_vu()
    await asyncio.sleep(0.1)
    assert hub.state.vu_levels == [3, 0, 0, 2, 1, 6, 0, 0, 0, 0]
    await hub.async_stop()


@pytest.mark.asyncio
async def test_external_change_propagates(server):
    """Another client changing volume must show up in our state cache."""
    hub = AudacHub(host=server.host, port=server.port, zones=8)
    await hub.async_start()
    await asyncio.sleep(0.2)
    await server.push_state_change(zone=3, raw=15)
    await asyncio.sleep(0.1)
    assert hub.state.zones[3].volume_raw == 15
    await hub.async_stop()


@pytest.mark.asyncio
async def test_listener_called_on_change(server):
    hub = AudacHub(host=server.host, port=server.port, zones=8)
    counter = {"n": 0}

    def cb():
        counter["n"] += 1

    hub.add_listener(cb)
    await hub.async_start()
    await asyncio.sleep(0.3)
    assert counter["n"] > 0  # at least connect + initial sync notifications
    await hub.async_stop()
