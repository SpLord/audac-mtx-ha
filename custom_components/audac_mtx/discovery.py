"""Finding AUDAC devices: probe one host, or sweep the local networks. HA-free.

All AUDAC devices listen on TCP 5001 and announce nothing (no mDNS/SSDP), so
the sweep tries port 5001 on every address and asks whatever answers who it
is: GSV to the matrix addresses (X001 MTX, M001 M2) and GTPS to D001 (XMP44).
Only the addressed model answers.
"""
from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from .models import detect_model
from .protocol import build_frame, decode_line, parse_frame
from .xmp import parse_tps

# Larger networks are cut down to the /24 around our own address
MAX_PREFIX = 24
SCAN_CONCURRENCY = 64
CONNECT_TIMEOUT = 0.8


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Outcome of probing one host:port."""

    reachable: bool
    model: str | None = None  # None: something listens, but no AUDAC answered
    firmware: str | None = None
    error: str | None = None


async def probe(host: str, port: int, timeout: float = 5.0) -> ProbeResult:
    """Open a brief connection and ask all known AUDAC addresses who is there."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError) as err:
        return ProbeResult(False, error=str(err) or "timeout")
    try:
        for addr in ("X001", "M001"):
            writer.write(build_frame(destination=addr, source="HA", command="GSV", args="0"))
        writer.write(build_frame(destination="D001", source="HA", command="GTPS", args="0"))
        await writer.drain()
        # The reply may be preceded by VU frames (the M2 streams them constantly)
        for _ in range(40):
            try:
                line = await asyncio.wait_for(reader.readuntil(b"\r\n"), 2.0)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                break
            frame = parse_frame(decode_line(line))
            if frame is None or not frame.args:
                continue
            if frame.command == "SV":
                return ProbeResult(True, detect_model(frame.source), frame.args)
            if frame.command == "TPS":
                labels = [label for _, label in parse_tps(frame.args).values() if label]
                return ProbeResult(True, detect_model(frame.source), " / ".join(labels) or None)
        return ProbeResult(True)
    except (OSError, ConnectionError) as err:
        return ProbeResult(True, error=str(err))
    finally:
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), 2.0)
        except Exception:  # noqa: BLE001
            pass


def scan_hosts(interfaces: Iterable[str]) -> list[str]:
    """Addresses to sweep for interfaces like "192.168.10.55/24" (own address excluded)."""
    hosts: dict[str, None] = {}
    for spec in interfaces:
        iface = ipaddress.ip_interface(spec)
        if iface.version != 4 or iface.is_loopback or iface.is_link_local:
            continue
        net = iface.network
        if net.prefixlen < MAX_PREFIX:
            net = ipaddress.ip_network(f"{iface.ip}/{MAX_PREFIX}", strict=False)
        for ip in net.hosts():
            if ip != iface.ip:
                hosts[str(ip)] = None
    return list(hosts)


async def _port_open(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), 1.0)
    except Exception:  # noqa: BLE001
        pass
    return True


async def scan(
    hosts: Iterable[str],
    port: int,
    prober: Callable[[str, int], Awaitable[ProbeResult]] = probe,
) -> dict[str, ProbeResult]:
    """Sweep hosts on port; return only the ones where an AUDAC answered."""
    sem = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def check(host: str) -> tuple[str, ProbeResult | None]:
        async with sem:
            if not await _port_open(host, port):
                return host, None
        return host, await prober(host, port)

    results = await asyncio.gather(*(check(h) for h in hosts))
    return {h: r for h, r in results if r is not None and r.model}
