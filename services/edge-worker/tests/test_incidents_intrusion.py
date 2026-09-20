"""A person inside a camera's restricted zone is an incident; one outside it, or one who only clips
it, is not. The zone comes from the camera's profile (config/camera_profiles.yaml, `intrusion`)."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anpr.incidents import IncidentDetector  # noqa: E402

ZONE = {"zone": [[0.5, 0.5], [1.0, 0.5], [1.0, 1.0], [0.5, 1.0]], "dwell_seconds": 2}
FRAME = (1280, 720)


@dataclass
class T:
    track_id: int
    box: tuple
    label: str


def _stand(det, box, label="person", seconds=4.0, step=0.25):
    out, t = [], 1000.0
    while t <= 1000.0 + seconds:
        out += det.update([T(1, box, label)], t, frame_size=FRAME)
        t += step
    return out


def test_a_person_standing_inside_the_zone_raises_one_intrusion():
    hits = _stand(IncidentDetector("cam", intrusion=ZONE), (900, 400, 960, 600))
    assert [i.kind for i in hits] == ["INTRUSION"]
    assert hits[0].severity == "HIGH" and hits[0].evidence["dwell_s"] >= 2


def test_a_person_outside_the_zone_raises_nothing():
    assert _stand(IncidentDetector("cam", intrusion=ZONE), (100, 100, 160, 300)) == []


def test_the_foot_point_decides_not_the_head():
    # the box's top is outside the zone and its feet inside: they are standing in it
    assert _stand(IncidentDetector("cam", intrusion=ZONE), (900, 200, 960, 500))


def test_a_vehicle_is_ignored_unless_the_profile_lists_it():
    assert _stand(IncidentDetector("cam", intrusion=ZONE), (900, 400, 1100, 600), label="car") == []
    both = dict(ZONE, classes=["person", "car"])
    assert _stand(IncidentDetector("cam", intrusion=both), (900, 400, 1100, 600), label="car")


def test_a_camera_without_a_zone_has_no_intrusion_rule():
    assert _stand(IncidentDetector("cam"), (900, 400, 960, 600)) == []
