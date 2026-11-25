"""AUDAC MTX/M2 hub: persistent TCP connection, frame dispatcher, state cache.

Model differences (address, value ranges, names) come from a ModelProfile.

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
import time
from collections.abc import Callable

from .const import (
    EOL,
    RECONNECT_BASE,
    RECONNECT_MAX,
    TIMEOUT,
)
from .models import MODEL_M2, ModelProfile, get_profile
from .protocol import (
    Frame,
    build_frame,
    decode_line,
    parse_frame,
    parse_signed,
    parse_vu,
    reply_command,
    split_update_command,
)
from .state import HubState, ZoneState

_LOGGER = logging.getLogger(__name__)

# M2 streams ~10 VU frames per second; don't wake every entity that often.
VU_NOTIFY_INTERVAL = 1.0
# Ack argument the M2 sends instead of "+" when its DSP load is above 90 %.
ACK_OVERLOAD = "L"


class AudacCommandRejected(Exception):
    """The device refused a command (M2: DSP load above 90 %)."""


class AudacHub:
    """Owns the TCP connection and dispatches frames into the state cache."""

    def __init__(
        self,
        host: str,
        port: int,
        zones: int | None = None,
        source_id: str = "HA",
        profile: ModelProfile | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.profile = profile or get_profile(None, zones)
        self.zones = zones or self.profile.zones
        self.source_id = source_id

        self.state = HubState()
        neutral = self.profile.tone_neutral_wire
        for z in range(1, self.zones + 1):
            zs = self.state.ensure_zone(z)
            self.state.zones[z] = zs.with_bass(neutral).with_treble(neutral)
        self._synced = asyncio.Event()
        self._last_vu_notify = 0.0

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
        # VU listeners get every VU frame (mixer card stream, bypasses HA state)
        self._vu_listeners: list[Callable[[list[int]], None]] = []

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

    def add_vu_listener(self, cb: Callable[[list[int]], None]) -> Callable[[], None]:
        """Register a callback fired with the levels of every VU frame. Returns unsubscribe."""
        self._vu_listeners.append(cb)

        def _unsub() -> None:
            try:
                self._vu_listeners.remove(cb)
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

    async def async_wait_synced(self, timeout: float) -> bool:
        """Wait until the first initial sync finished (names known on M2)."""
        try:
            await asyncio.wait_for(self._synced.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    # ----- names -----
    def source_name(self, idx: int) -> str:
        """Display name of an input index (device name on M2)."""
        if idx == 0:
            return "None"
        name = self.state.input_names.get(idx)
        if name:
            return name
        if idx in self.profile.default_sources:
            return self.profile.default_sources[idx]
        if 51 <= idx <= 58:
            return f"Scene {idx - 50}"
        if 61 <= idx <= 68:
            return f"Global scene {idx - 60}"
        return f"Input {idx}"

    def source_map(self, current: int | None = None) -> dict[int, str]:
        """Selectable inputs → unique display names (HA needs unique options).

        The currently routed input is included even if it is not selectable
        (e.g. a prio input or scene on the M2), so the UI can show it.
        """
        indices = list(self.profile.default_sources)
        if current is not None and current not in indices:
            indices.append(current)
        result: dict[int, str] = {}
        seen: set[str] = set()
        for idx in indices:
            name = self.source_name(idx)
            if name in seen:
                name = f"{name} ({idx})"
            seen.add(name)
            result[idx] = name
        return result

    def zone_name(self, zone: int) -> str:
        return self.state.zone_names.get(zone) or f"Zone {zone}"

    # ----- commands -----
    def _zone_cmd(self, prefix: str, zone: int) -> str:
        # MTX takes SV1/SR1, M2 manual uses SV02 — both pad where documented
        if self.profile.model == MODEL_M2:
            return f"{prefix}{zone:02d}"
        return f"{prefix}{zone}"

    async def set_volume_pct(self, zone: int, percent: int) -> None:
        raw = self.profile.volume_to_wire(max(0, min(100, percent)) / 100)
        await self._send_and_ack(self._zone_cmd("SV", zone), str(raw))

    async def volume_up(self, zone: int) -> None:
        await self._send_and_ack(f"SVU{zone:02d}", "0")

    async def volume_down(self, zone: int) -> None:
        await self._send_and_ack(f"SVD{zone:02d}", "0")

    async def set_source(self, zone: int, idx: int) -> None:
        idx = self.profile.clamp_source(idx)
        await self._send_and_ack(self._zone_cmd("SR", zone), str(idx))

    async def source_up(self, zone: int) -> None:
        await self._send_and_ack(f"SRU{zone:02d}", "0")

    async def source_down(self, zone: int) -> None:
        await self._send_and_ack(f"SRD{zone:02d}", "0")

    async def set_mute(self, zone: int, muted: bool) -> None:
        await self._send_and_ack(f"SM{zone:02d}", "1" if muted else "0")

    async def set_bass_db(self, zone: int, db: float) -> None:
        raw = self.profile.tone_db_to_wire(db)
        await self._send_and_ack(f"SB{zone:02d}", str(raw))

    async def set_treble_db(self, zone: int, db: float) -> None:
        raw = self.profile.tone_db_to_wire(db)
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
        try:
            await self._initial_sync()
        finally:
            self._synced.set()

    async def _initial_sync(self) -> None:
        m2 = self.profile.model == MODEL_M2
        head = ("GZN", "GIN") if m2 else ()
        for cmd in (*head, "GVALL", "GRALL", "GMALL"):
            try:
                await self._send_and_ack(cmd, "0", expect_value=True)
            except (asyncio.TimeoutError, ConnectionError, AudacCommandRejected):
                _LOGGER.warning("Initial sync %s failed", cmd)
        # Bass/Treble per zone (no GBALL/GTALL in the protocol). The M2's GZI
        # fans out into ~10 frames incl. all names, so ask GB/GT directly there.
        per_zone = ("GB", "GT") if m2 else ("GZI",)
        for z in range(1, self.zones + 1):
            for prefix in per_zone:
                try:
                    await self._send_and_ack(f"{prefix}{z:02d}", "0", expect_value=True)
                except (asyncio.TimeoutError, ConnectionError, AudacCommandRejected):
                    pass

    # ------------------------------------------------------------ internal
    async def _connect(self) -> None:
        async with self._connect_lock:
            if self._writer is not None:
                return
            _LOGGER.info("Connecting to AUDAC %s %s:%s", self.profile.model, self.host, self.port)
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
                _LOGGER.info("AUDAC closed connection (likely another client connected)")
                self.state.last_error = "remote closed (lockout?)"
                await self._close_socket()
                await self._backoff()
                continue
            except (ConnectionError, OSError) as err:
                self.state.last_error = str(err)
                await self._close_socket()
                await self._backoff()
                continue
            text = decode_line(line)
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
            self.state.vu_levels = parse_vu(args)
            for vu_cb in list(self._vu_listeners):
                try:
                    vu_cb(self.state.vu_levels)
                except Exception:  # noqa: BLE001 — a broken listener must not kill the reader
                    _LOGGER.exception("VU listener failed")
            now = time.monotonic()
            if now - self._last_vu_notify >= VU_NOTIFY_INTERVAL:
                self._last_vu_notify = now
                self._notify()
            return

        if cmd in ("ZN", "IN"):
            self._update_names(cmd, args)
            return

        p = self.profile
        # GVALL response is "VALL" with args 40^40^20^... (M2: 150 = output off)
        if cmd == "VALL":
            for i, part in enumerate(args.split("^"), start=1):
                if part.isdigit() and i <= self.zones:
                    self.state.zones[i] = self.state.ensure_zone(i).with_volume(
                        p.clamp_volume_raw(int(part))
                    )
            self._notify()
            return
        if cmd == "RALL":
            for i, part in enumerate(args.split("^"), start=1):
                if part.isdigit() and i <= self.zones:
                    self.state.zones[i] = self.state.ensure_zone(i).with_source(
                        p.clamp_source(int(part))
                    )
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
        updated = self._apply_zone_update(z, kind, args)
        if updated is None:
            return
        self.state.zones[zone] = updated
        self._notify()

    def _apply_zone_update(self, z: ZoneState, kind: str, args: str) -> ZoneState | None:
        p = self.profile
        if kind == "ZI":
            try:
                vol_s, route_s, mute_s, bass_s, treble_s = args.split("^")
                return (
                    z.with_volume(p.clamp_volume_raw(int(vol_s)))
                    .with_source(p.clamp_source(int(route_s)))
                    .with_mute(mute_s == "1")
                    .with_bass(int(bass_s))
                    .with_treble(int(treble_s))
                )
            except ValueError:
                return None
        # M2 routing updates are "000^009": the last field is the input
        value = parse_signed(args.split("^")[-1])
        if value is None:
            return None
        if kind == "V":
            return z.with_volume(p.clamp_volume_raw(value))
        if kind == "R":
            return z.with_source(p.clamp_source(value))
        if kind == "M":
            return z.with_mute(value == 1)
        if kind == "B":
            return z.with_bass(value)
        if kind == "T":
            return z.with_treble(value)
        return None

    def _update_names(self, cmd: str, args: str) -> None:
        names = [n.strip() for n in args.split("^")]
        if cmd == "ZN":
            # Only the first entries are real outputs; the rest are WP/fiber
            self.state.zone_names = {
                i: n for i, n in enumerate(names[: self.zones], start=1) if n
            }
        else:
            self.state.input_names = {
                i: n for i, n in enumerate(names, start=1) if n and n != "NOT USED"
            }
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
        key = self._ack_key(reply_command(command) if expect_value else command, args)
        # Replace any stale future under the same key
        old = self._pending.pop(key, None)
        if old and not old.done():
            old.cancel()
        self._pending[key] = fut

        async with self._send_lock:
            payload = build_frame(
                destination=self.profile.device_addr,
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
        except asyncio.TimeoutError:
            self._pending.pop(key, None)
            _LOGGER.debug("Timeout awaiting ack for %s", command)
            raise
        if not expect_value and result.args == ACK_OVERLOAD:
            raise AudacCommandRejected(f"{command} rejected: device load above 90 %")
        return result
