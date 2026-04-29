"""Tests for protocol parsing/building/mappings."""
from __future__ import annotations

import pytest

# Make package importable in tests without HA

from audac_mtx.protocol import (
    Frame,
    build_frame,
    parse_frame,
    split_update_command,
    tone_db_to_wire,
    volume_to_wire,
    wire_to_tone_db,
    wire_to_volume,
)


class TestParseFrame:
    def test_ack(self):
        f = parse_frame("#|HA|X001|SV2|+|U|\r\n")
        assert f is not None
        assert f.destination == "HA"
        assert f.source == "X001"
        assert f.command == "SV2"
        assert f.is_ack
        assert not f.is_broadcast

    def test_broadcast_update(self):
        f = parse_frame("#|ALL|X001|V02|40|7378|\r\n")
        assert f is not None
        assert f.is_broadcast
        assert f.command == "V02"
        assert f.args == "40"
        assert f.checksum == "7378"

    def test_zi_response(self):
        f = parse_frame("#|web|X001|ZI01|20^3^0^07^07|U|\r\n")
        assert f is not None
        assert f.command == "ZI01"
        assert f.args == "20^3^0^07^07"

    def test_vu(self):
        f = parse_frame("#|web|X001|VU|000^000^000^000^000^006^000^000^000^000|U|\r\n")
        assert f is not None
        assert f.is_vu

    def test_malformed_returns_none(self):
        assert parse_frame("garbage") is None
        assert parse_frame("") is None
        assert parse_frame("#|too|few|") is None

    def test_strips_whitespace(self):
        f = parse_frame("  #|HA|X001|SV1|+|U|  \r\n")
        assert f is not None
        assert f.command == "SV1"


class TestBuildFrame:
    def test_volume_command(self):
        out = build_frame(destination="X001", source="HA", command="SV2", args="40")
        assert out == b"#|X001|HA|SV2|40|U|\r\n"

    def test_default_args(self):
        out = build_frame(destination="X001", source="HA", command="GVALL")
        assert out == b"#|X001|HA|GVALL|0|U|\r\n"


class TestVolumeMapping:
    def test_max(self):
        assert wire_to_volume(0) == pytest.approx(1.0)

    def test_min(self):
        assert wire_to_volume(70) == pytest.approx(0.0)

    def test_round_trip(self):
        for raw in (0, 10, 35, 50, 70):
            v = wire_to_volume(raw)
            back = volume_to_wire(v)
            assert abs(back - raw) <= 1

    def test_clamps(self):
        assert volume_to_wire(-5.0) == 70
        assert volume_to_wire(2.0) == 0


class TestToneMapping:
    def test_center_zero_db(self):
        assert wire_to_tone_db(7) == 0

    def test_extremes(self):
        assert wire_to_tone_db(0) == -14
        assert wire_to_tone_db(14) == 14

    def test_db_to_wire(self):
        assert tone_db_to_wire(0) == 7
        assert tone_db_to_wire(-14) == 0
        assert tone_db_to_wire(14) == 14
        assert tone_db_to_wire(2) == 8


class TestSplitUpdateCommand:
    def test_volume(self):
        assert split_update_command("V01") == ("V", 1)

    def test_zone_info(self):
        assert split_update_command("ZI04") == ("ZI", 4)

    def test_all_variants(self):
        assert split_update_command("VALL") == ("VALL", 0)
        assert split_update_command("RALL") == ("RALL", 0)

    def test_unknown(self):
        assert split_update_command("XYZ") is None
        assert split_update_command("VU") is None  # handled separately
