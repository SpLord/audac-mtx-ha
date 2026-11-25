"""XMP44 parsing helpers, checked against the examples of the AUDAC manual."""
from __future__ import annotations

from audac_mtx.models import MODEL_XMP44, detect_model
from audac_mtx.xmp import (
    MOD_DAB,
    MOD_EMPTY,
    MOD_INTERNET,
    MOD_VOICE,
    STATUS_PAUSED,
    STATUS_PLAYING,
    STATUS_RECORDING,
    STATUS_STOPPED,
    Favourite,
    SlotState,
    format_frequency,
    gain_db_to_wire,
    gain_wire_to_db,
    gain_wire_to_volume,
    parse_favourites,
    parse_presets,
    parse_psi,
    parse_pstat,
    parse_tps,
    split_slot,
    volume_to_gain_wire,
)


def test_tps_from_manual() -> None:
    tps = parse_tps("4^1^15^6^IMP40 V 1.0.4^DMP40 ^No Module ^FMP40 V1.4.29")
    assert tps == {
        1: (MOD_INTERNET, "IMP40 V 1.0.4"),
        2: (MOD_DAB, "DMP40"),
        3: (MOD_EMPTY, "No Module"),
        4: (MOD_VOICE, "FMP40 V1.4.29"),
    }


def test_slot_presence() -> None:
    assert SlotState(1, module=MOD_DAB).present
    assert not SlotState(1, module=MOD_EMPTY).present
    assert SlotState(1, module=MOD_DAB).module_name == "DMP40"


def test_gain_mapping_from_manual() -> None:
    assert gain_db_to_wire(8) == 0
    assert gain_db_to_wire(0) == 8
    assert gain_db_to_wire(-20) == 28
    assert gain_wire_to_db(28) == -20
    assert gain_db_to_wire(99) == 0  # clamped to +8 dB


def test_gain_volume_roundtrip() -> None:
    assert gain_wire_to_volume(0) == 1.0
    assert volume_to_gain_wire(1.0) == 0
    for raw in (0, 8, 28, 50):
        assert volume_to_gain_wire(gain_wire_to_volume(raw)) == raw


def test_pstat() -> None:
    assert parse_pstat("0^1^0") == STATUS_PLAYING
    assert parse_pstat("1^0^0") == STATUS_PAUSED
    assert parse_pstat("0^0^0") == STATUS_STOPPED
    assert parse_pstat("0^0^1") == STATUS_RECORDING
    assert parse_pstat("garbage") is None


def test_psi() -> None:
    assert parse_psi("Song^Artist^Album^200^12") == {
        "title": "Song", "artist": "Artist", "album": "Album", "duration": 200, "position": 12,
    }
    assert parse_psi("Only title")["artist"] is None


def test_presets_from_manual() -> None:
    args = "1^10360^103.60 ^1^10000^100.00 ^1^10000^100.00 "
    assert parse_presets(args) == (10360, 10000, 10000)


def test_favourites() -> None:
    assert parse_favourites("0^Studio Brussel^4712^1^Radio 1^4713^2^^") == (
        Favourite(0, "Studio Brussel", "4712"),
        Favourite(1, "Radio 1", "4713"),
    )


def test_split_slot() -> None:
    assert split_slot("OG3", "OG") == 3
    assert split_slot("PSTAT", "PSTAT") is None
    assert split_slot("OG9", "OG") is None
    assert split_slot("PSI1", "PSTAT") is None


def test_detect_model_and_frequency() -> None:
    assert detect_model("D001") == MODEL_XMP44
    assert format_frequency(10410) == "104.10 MHz"
    assert format_frequency(None) is None
