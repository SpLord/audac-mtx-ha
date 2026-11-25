"""M2 dialect: NUL-separated frames, signed tone, 0..100 volume, names, VU."""
from __future__ import annotations

import pytest

from audac_mtx.models import MODEL_M2, MODEL_MTX48, MODEL_MTX88, PROFILES, detect_model, get_profile
from audac_mtx.protocol import decode_line, parse_frame, parse_signed, parse_vu, reply_command

M2 = PROFILES[MODEL_M2]
MTX = PROFILES[MODEL_MTX88]


class TestParseFrameM2:
    def test_leading_nul_is_stripped(self):
        f = parse_frame("\x00#|HA|M001|V01|039|1715|\r\n")
        assert f is not None
        assert (f.destination, f.source, f.command, f.args) == ("HA", "M001", "V01", "039")

    def test_truncated_frame_before_real_one(self):
        f = parse_frame("#|ALL#|ALL|M001|VU|0900141422|5904|\r\n")
        assert f is not None
        assert f.command == "VU"
        assert f.source == "M001"

    def test_lone_fragment_is_rejected(self):
        assert parse_frame("\x00#|ALL") is None

    def test_umlaut_names_survive(self):
        for raw in ("#|HA|M001|ZN|Küche^Raum2|ab12|\r\n".encode("utf-8"),
                    "#|HA|M001|ZN|Küche^Raum2|ab12|\r\n".encode("latin-1")):
            f = parse_frame(decode_line(raw))
            assert f is not None and f.args == "Küche^Raum2"

    def test_overload_ack_counts_as_ack(self):
        f = parse_frame("#|HA|M001|SV02|L|U|")
        assert f is not None and f.is_ack


class TestHelpers:
    @pytest.mark.parametrize(
        ("cmd", "reply"),
        [("GVALL", "VALL"), ("GSV", "SV"), ("GZN", "ZN"), ("GIN", "IN"), ("GB01", "B01"), ("SV02", "SV02")],
    )
    def test_reply_command(self, cmd, reply):
        assert reply_command(cmd) == reply

    @pytest.mark.parametrize(("text", "value"), [("000", 0), ("-03", -3), ("+9", 9), ("x", None), ("", None)])
    def test_parse_signed(self, text, value):
        assert parse_signed(text) == value

    def test_parse_vu_mtx(self):
        assert parse_vu("003^000^012") == [3, 0, 12]

    def test_parse_vu_m2_pairs(self):
        assert parse_vu("1006141420") == [10, 6, 14, 14, 20]

    def test_parse_vu_m2_odd_tail_ignored(self):
        assert parse_vu("10060") == [10, 6]


class TestProfiles:
    def test_m2_volume_range(self):
        assert M2.wire_to_volume(0) == pytest.approx(1.0)
        assert M2.wire_to_volume(100) == pytest.approx(0.0)
        assert M2.wire_to_volume(150) == pytest.approx(0.0)  # "off" output
        assert M2.volume_to_wire(0.5) == 50
        assert M2.clamp_volume_raw(150) == 100

    def test_mtx_volume_unchanged(self):
        assert MTX.volume_to_wire(0.5) == 35
        assert MTX.wire_to_volume(70) == pytest.approx(0.0)

    def test_m2_tone_is_signed_db(self):
        assert M2.tone_db_to_wire(-3) == -3
        assert M2.tone_db_to_wire(12) == 9
        assert M2.wire_to_tone_db(-9) == -9
        assert M2.tone_neutral_wire == 0

    def test_mtx_tone_raw(self):
        assert MTX.tone_db_to_wire(0) == 7
        assert MTX.wire_to_tone_db(14) == 14
        assert MTX.tone_neutral_wire == 7

    def test_source_clamp(self):
        assert M2.clamp_source(61) == 61
        assert MTX.clamp_source(61) == 8

    def test_legacy_entries_resolve(self):
        assert get_profile(None, 8).model == MODEL_MTX88
        assert get_profile(None, 4).model == MODEL_MTX48
        assert get_profile("MTX", 8).model == MODEL_MTX88
        assert get_profile(MODEL_M2).device_addr == "M001"

    def test_detect_model(self):
        assert detect_model("M001") == MODEL_M2
        assert detect_model("X001") == MODEL_MTX88
        assert detect_model(None) == MODEL_MTX88
