from app import plates


def test_the_cam05_signboard_is_not_a_plate_but_real_plates_are():
    plates._SCENE_TEXT = None
    for misread in ("TS57SDB3008", "TS57SQB3008", "TS75QB3008", "TS75DB3008", "TS57SQ8300", "TS50B3008"):
        assert plates.scene_text("VIGENTRA-TRAFFIC-AHM-CAM05", misread), misread
        assert plates.scene_text("GRID-cam05", misread), misread
    assert not plates.scene_text("VIGENTRA-TRAFFIC-AHM-CAM06", "TS57SDB3008")   # only on its own camera
    for real in ("GJ01AB3008", "GJ05DB3008", "GJ18BR5253", "GJ39CB0189"):
        assert not plates.scene_text("GRID-cam05", real), real
