"""AUDAC MTX hub: persistent TCP connection, frame dispatcher, state cache.

The MTX accepts MAX 1 simultaneous TCP/IP connection. A second client
(web-UI, AUDAC Touch app, wall-panel via RJ45-bus) will lock us out —
we detect that gracefully and reconnect with backoff.

The protocol is broadcast-style: every state change is mirrored to ALL
clients as an Update frame. We listen continuously; entities read from
the local state cache. We do NOT poll periodically.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from .const import (
    BROADCAST,
    DEVICE_ADDR,
    EOL,
    RECONNECT_BASE,
    RECONNECT_MAX,
    TIMEOUT,
)
from .protocol import Frame, build_frame, parse_frame, split_update_command
from .state import HubState

_LOGGER = logging.getLogger(__name__)


class AudacHub:
    """Owns the TCP connection and dispatches frames into the state cache."""

    def __init__(
        self,
        host: str,
        port: int,
        zones: int,
        source_id: str = "HA",
    ) -> None:
        self.host = host
        self.port = port
        self.zones = zones
        self.source_id = source_id

        self.state = HubState()
        for z in range(1, zones + 1):
            self.state.ensure_zone(z)

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._stop = asyncio.Event()

        # Pending acks: command-prefix → Future. Key is "cmd|args" so set+set on
        # different zones don't collide.
        self._pending: dict[str, asyncio.Future[Frame]] = {}
        self._bg_tasks: set[asyncio.Task] = set()

        # Listeners called on every state change (HA coordinator wires up here)
        self._listeners: list[Callable[[], None]] = []

        self._reconnect_delay = RECONNECT_BASE

    # ------------------------------------------------------------------ public
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired after every state mutation. Returns unsubscribe."""
        self._listeners.append(cb)

        def _unsub() -> None:
            try:
                self._listeners.remove(cb)
            except ValueError:
                pass

        return _unsub

    async def async_start(self) -> None:
        """Start the receive loop (which owns connect/reconnect). Idempotent.

        Waits briefly for the first connect attempt so callers see a sensible
        connected/last_error state, but does NOT block on full initial-sync.
        """
        if self._recv_task and not self._recv_task.done():
            return
        self._stop.clear()
        self._recv_task = asyncio.create_task(self._recv_loop(), name="audac_mtx_recv")
        # Give the loop one event-loop tick to attempt the initial connect
        for _ in range(20):
            await asyncio.sleep(0.05)
            if self.state.connected or self.state.last_error:
                break

    async def async_stop(self) -> None:
        """Tear down. Closes socket FIRST (unblocks readuntil), then cancels tasks."""
        self._stop.set()
        # Close socket first so any readuntil/drain unblocks with an error.
        await self._close_socket()
        bg = list(self._bg_tasks)
        for t in bg:
            t.cancel()
        for t in bg:
            try:
                await asyncio.wait_for(t, 1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await asyncio.wait_for(self._recv_task, 2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        # Cancel any pending acks
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    # ----- commands -----
    async def set_volume_pct(self, zone: int, percent: int) -> None:
        raw = round((100 - max(0, min(100, percent))) * 70 / 100)
        await self._send_and_ack(f"SV{zone}", str(raw))

    async def volume_up(self, zone: int) -> None:
        await self._send_and_ack(f"SVU{zone:02d}", "0")

    async def volume_down(self, zone: int) -> None:
        await self._send_and_ack(f"SVD{zone:02d}", "0")

    async def set_source(self, zone: int, idx: int) -> None:
        idx = max(0, min(8, int(idx)))
        await self._send_and_ack(f"SR{zone}", str(idx))

    async def source_up(self, zone: int) -> None:
        await self._send_and_ack(f"SRU{zone:02d}", "0")

    async def source_down(self, zone: int) -> None:
        await self._send_and_ack(f"SRD{zone:02d}", "0")

    async def set_mute(self, zone: int, muted: bool) -> None:
        await self._send_and_ack(f"SM{zone:02d}", "1" if muted else "0")

    async def set_bass_raw(self, zone: int, raw: int) -> None:
        raw = max(0, min(14, int(raw)))
        await self._send_and_ack(f"SB{zone:02d}", str(raw))

    async def set_treble_raw(self, zone: int, raw: int) -> None:
        raw = max(0, min(14, int(raw)))
        await self._send_and_ack(f"ST{zone:02d}", str(raw))

    async def save_settings(self) -> None:
        await self._send_and_ack("SAVE", "0")

    async def factory_reset(self) -> None:
        await self._send_and_ack("DEF", "0")

    async def get_firmware(self) -> str | None:
        try:
            ack = await self._send_and_ack("GSV", "0", expect_value=True)
        except (asyncio.TimeoutError, ConnectionError):
            return None
        return ack.args if ack else None

    async def initial_sync(self) -> None:
        """Pull complete state once after connect. Device pushes updates afterwards."""
        for cmd in ("GVALL", "GRALL", "GMALL"):
            try:
                await self._send_and_ack(cmd, "0", expect_value=True)
            except (asyncio.TimeoutError, ConnectionError):
                _LOGGER.warning("Initial sync %s failed", cmd)
        # Bass/Treble per zone (no GBALL/GTALL in the protocol)
        for z in range(1, self.zones + 1):
            try:
                await self._send_and_ack(f"GZI{z:02d}", "0", expect_value=True)
            except (asyncio.TimeoutError, ConnectionError):
                pass

    # ------------------------------------------------------------ internal
    async def _connect(self) -> None:
        async with self._connect_lock:
            if self._writer is not None:
                return
            _LOGGER.info("Connecting to AUDAC MTX %s:%s", self.host, self.port)
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), TIMEOUT
            )
            self.state.connected = True
            self.state.last_error = None
            self._reconnect_delay = RECONNECT_BASE
            self._notify()

    async def _close_socket(self) -> None:
        if self._writer:
            try:
                self._writer.close()
                await asyncio.wait_for(self._writer.wait_closed(), 2.0)
            except Exception:  # noqa: BLE001
                pass
        self._reader = None
        self._writer = None
        if self.state.connected:
            self.state.connected = False
            self._notify()

    async def _recv_loop(self) -> None:
        """Continuously read frames; reconnect on errors."""
        while not self._stop.is_set():
            if self._reader is None:
                try:
                    await self._connect()
                except (asyncio.TimeoutError, OSError) as err:
                    self.state.last_error = str(err)
                    await self._close_socket()
                    await self._backoff()
                    continue
                # Schedule initial sync in a SEPARATE task so the recv-loop
                # can dispatch its replies — avoids deadlock on the ack-future.
                t = asyncio.create_task(self._safe_initial_sync())
                self._bg_tasks.add(t)
                t.add_done_callback(self._bg_tasks.discard)
            try:
                line = await self._reader.readuntil(EOL)
            except asyncio.IncompleteReadError:
                _LOGGER.info("MTX closed connection (likely another client connected)")
                self.state.last_error = "remote closed (lockout?)"
                await self._close_socket()
                await self._backoff()
                continue
            except (ConnectionError, OSError) as err:
                self.state.last_error = str(err)
                await self._close_socket()
                await self._backoff()
                continue
            text = line.decode("ascii", errors="replace")
            frame = parse_frame(text)
            if frame is None:
                continue
            self._dispatch(frame)

    async def _safe_initial_sync(self) -> None:
        try:
            await self.initial_sync()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("initial_sync failed")

    async def _backoff(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), self._reconnect_delay)
        except asyncio.TimeoutError:
            pass
        self._reconnect_delay = min(self._reconnect_delay * 2, RECONNECT_MAX)

    def _dispatch(self, frame: Frame) -> None:
        """Route an incoming frame to ack-future or state-cache."""
        # 1) Ack? (source = our id, dest = X001 mirror style)
        if frame.is_ack and frame.destination == self.source_id:
            key = self._ack_key(frame.command, "")
            fut = self._pending.pop(key, None)
            if fut and not fut.done():
                fut.set_result(frame)
                return
            # Some get-commands return value as args (not '+'); handled below
        # 2) Get-response with value (e.g. ZI01 -> #|web|X001|ZI01|20^3^0^07^07|U|)
        # We registered the future under cmd-key; if dest != ALL and source = web-ish
        if not frame.is_broadcast and frame.destination in (self.source_id, "web"):
            key = self._ack_key(frame.command, "")
            fut = self._pending.pop(key, None)
            if fut and not fut.done():
                fut.set_result(frame)
                # do NOT return — also feed cache below
        # 3) State updates (broadcast OR get-response value)
        self._update_cache(frame)

    @staticmethod
    def _ack_key(cmd: str, args: str) -> str:
        # Acks come back as the same command. Args differ but cmd+zone is unique
        # enough at the message-rate we operate at.
        return cmd

    def _update_cache(self, frame: Frame) -> None:
        cmd = frame.command
        args = frame.args

        if cmd == "VU":
            try:
                self.state.vu_levels = [int(x) for x in args.split("^") if x.isdigit()]
            except ValueError:
                pass
            self._notify()
            return

        # GVALL response is "VALL" with args 40^40^20^...
        if cmd == "VALL":
            for i, part in enumerate(args.split("^"), start=1):
                if part.isdigit() and i <= self.zones:
                    self.state.zones[i] = self.state.ensure_zone(i).with_volume(int(part))
            self._notify()
            return
        if cmd == "RALL":
            for i, part in enumerate(args.split("^"), start=1):
                if part.isdigit() and i <= self.zones:
                    self.state.zones[i] = self.state.ensure_zone(i).with_source(int(part))
            self._notify()
            return
        if cmd == "MALL":
            for i, part in enumerate(args.split("^"), start=1):
                if part.isdigit() and i <= self.zones:
                    self.state.zones[i] = self.state.ensure_zone(i).with_mute(part == "1")
            self._notify()
            return

        # Composite: ZI01 → "vol^route^mute^bass^treble"
        split = split_update_command(cmd)
        if split is None:
            return
        kind, zone = split
        if zone < 1 or zone > self.zones:
            return
        z = self.state.ensure_zone(zone)
        if kind == "ZI":
            try:
                vol_s, route_s, mute_s, bass_s, treble_s = args.split("^")
                z = (
                    z.with_volume(int(vol_s))
                    .with_source(int(route_s))
                    .with_mute(mute_s == "1")
                    .with_bass(int(bass_s))
                    .with_treble(int(treble_s))
                )
            except (ValueError, KeyError):
                return
        elif kind == "V" and args.isdigit():
            z = z.with_volume(int(args))
        elif kind == "R" and args.isdigit():
            z = z.with_source(int(args))
        elif kind == "M":
            z = z.with_mute(args == "1")
        elif kind == "B" and args.isdigit():
            z = z.with_bass(int(args))
        elif kind == "T" and args.isdigit():
            z = z.with_treble(int(args))
        else:
            return
        self.state.zones[zone] = z
        self._notify()

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Listener error")

    async def _send_and_ack(
        self,
        command: str,
        args: str,
        *,
        expect_value: bool = False,
    ) -> Frame | None:
        """Send a frame and wait for ack (or value-response). Reconnects if needed."""
        if self._writer is None:
            await self._connect()
        assert self._writer is not None

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Frame] = loop.create_future()
        key = self._ack_key(command, args)
        # Replace any stale future under the same key
        old = self._pending.pop(key, None)
        if old and not old.done():
            old.cancel()
        self._pending[key] = fut

        async with self._send_lock:
            payload = build_frame(
                destination=DEVICE_ADDR,
                source=self.source_id,
                command=command,
                args=args,
            )
            try:
                self._writer.write(payload)
                await self._writer.drain()
            except (ConnectionError, OSError) as err:
                self._pending.pop(key, None)
                self.state.last_error = str(err)
                await self._close_socket()
                raise

        try:
            result = await asyncio.wait_for(fut, TIMEOUT)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(key, None)
            _LOGGER.debug("Timeout awaiting ack for %s", command)
            raise
