"""Mixer-card helpers: VU mapping, dB display value, channel layout."""
from __future__ import annotations

from audac_mtx.mixer import VU_MAX, build_layout, volume_db, vu_by_zone
from audac_mtx.models import MODEL_M2, MODEL_MTX88, PROFILES
from audac_mtx.protocol import parse_vu

M2 = PROFILES[MODEL_M2]
MTX = PROFILES[MODEL_MTX88]

# Captured live from the Rosenbar M2: outputs 5–7 muted/off read 00/00
LIVE_FRAME = "151519192323181700000000000016170000040"


def test_vu_pairs_map_to_outputs():
    vu = vu_by_zone(M2, parse_vu(LIVE_FRAME), 8)
    assert vu[1] == [15, 15]
    assert vu[2] == [19, 19]
    assert vu[4] == [18, 17]
    assert vu[5] == vu[6] == vu[7] == [0, 0]
    assert vu[8] == [16, 17]
    assert set(vu) == set(range(1, 9))


def test_vu_short_frame_maps_only_complete_pairs():
    assert vu_by_zone(M2, [1, 2, 3], 8) == {1: [1, 2]}


def test_vu_not_mapped_for_mtx():
    assert vu_by_zone(MTX, [3, 0, 5], 8) == {}


def test_volume_db():
    assert volume_db(M2, 39) == -39
    assert volume_db(M2, 0) == 0
    assert volume_db(M2, 100) is None
    assert volume_db(M2, 150) is None  # M2 "output off"
    assert volume_db(MTX, 70) is None
    assert volume_db(MTX, 20) == -20


def test_build_layout_resolves_entities_by_unique_id():
    known = {
        ("media_player", "e1_zone_1"): "media_player.audac_m2_output_1",
        ("number", "e1_zone_1_bass"): "number.audac_m2_output_1_bass",
        ("number", "e1_zone_1_treble"): "number.audac_m2_output_1_treble",
        ("media_player", "e1_zone_2"): "media_player.audac_m2_raum2",
    }
    layout = build_layout(
        "e1", M2, 2, lambda z: {1: "Output 1", 2: "Raum2"}[z], lambda p, u: known.get((p, u))
    )
    assert layout["model"] == "M2"
    assert layout["vol_max"] == 100
    assert layout["vu"] is True
    assert layout["vu_max"] == VU_MAX
    assert layout["channels"][0] == {
        "zone": 1,
        "name": "Output 1",
        "media_player": "media_player.audac_m2_output_1",
        "bass": "number.audac_m2_output_1_bass",
        "treble": "number.audac_m2_output_1_treble",
    }
    assert layout["channels"][1]["name"] == "Raum2"
    assert layout["channels"][1]["bass"] is None


def test_build_layout_mtx_has_no_meters():
    layout = build_layout("e2", MTX, 8, lambda z: f"Zone {z}", lambda p, u: None)
    assert layout["vu"] is False
    assert layout["vol_max"] == 70
    assert len(layout["channels"]) == 8
