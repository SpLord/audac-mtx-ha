"""Discovery: probe identifies each model, the sweep keeps only AUDAC answers."""
from __future__ import annotations

import asyncio

import pytest

from audac_mtx.discovery import probe, scan, scan_hosts
from audac_mtx.models import MODEL_M2, MODEL_XMP44

from .test_hub_m2 import MockM2Server
from .test_hub_xmp import MockXmp


def test_scan_hosts_limits_to_24() -> None:
    hosts = scan_hosts(["192.168.10.55/16", "127.0.0.1/8", "fe80::1/64"])
    assert len(hosts) == 253
    assert "192.168.10.55" not in hosts
    assert hosts[0] == "192.168.10.1" and hosts[-1] == "192.168.10.254"


def test_scan_hosts_small_network() -> None:
    assert scan_hosts(["10.0.0.2/30"]) == ["10.0.0.1"]


@pytest.mark.asyncio
async def test_probe_detects_m2_and_xmp() -> None:
    m2, xmp = MockM2Server(), MockXmp()
    await m2.start()
    await xmp.start()
    try:
        r_m2 = await probe("127.0.0.1", m2.port)
        r_xmp = await probe("127.0.0.1", xmp.port)
    finally:
        await m2.stop()
        await xmp.stop()
    assert (r_m2.model, r_m2.firmware) == (MODEL_M2, "2.10")
    assert r_xmp.model == MODEL_XMP44 and "IMP40" in r_xmp.firmware


@pytest.mark.asyncio
async def test_probe_unreachable_and_silent() -> None:
    assert not (await probe("127.0.0.1", 1, timeout=1)).reachable

    async def silent(reader, writer):
        await asyncio.sleep(5)

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        result = await probe("127.0.0.1", port)
    finally:
        server.close()
    assert result.reachable and result.model is None


@pytest.mark.asyncio
async def test_scan_keeps_only_audac() -> None:
    xmp = MockXmp()
    await xmp.start()
    try:
        found = await scan(["127.0.0.1", "127.0.0.2"], xmp.port)
    finally:
        await xmp.stop()
    # 127.0.0.2 is also loopback on Linux → same mock answers; both are AUDAC
    assert set(found) <= {"127.0.0.1", "127.0.0.2"} and "127.0.0.1" in found
    assert found["127.0.0.1"].model == MODEL_XMP44
