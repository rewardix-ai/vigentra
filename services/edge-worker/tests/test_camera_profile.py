"""Per-camera configuration: lookup, merge, and the worker settings derived from it."""
from pathlib import Path

import yaml

from anpr.camera import deep_merge, profile_for, resolve
from app.anpr_engine import router_settings, sampler_settings

CONFIG = Path(__file__).resolve().parent.parent / "config"

DOC = {
    "defaults": {"sampling": {"stride": None}, "frame_quality": {"night_mode": "auto"}},
    "cameras": {
        "cam07": {"aliases": ["GRID-cam07"], "lighting": "dark",
                  "sampling": {"stride": 2, "burst_plate_px": 20},
                  "frame_quality": {"night_mode": "on"},
                  "thresholds": {"detector": {"vehicle_conf": 0.1}}},
    },
}


def test_alias_and_case_resolve_to_the_camera():
    for key in ("cam07", "CAM07", "grid-cam07"):
        p = resolve(DOC, [None, key])
        assert p.key == "cam07"
        assert p.thresholds == {"detector": {"vehicle_conf": 0.1}}
        assert p.info == {"lighting": "dark"}


def test_unknown_camera_gets_the_defaults():
    p = resolve(DOC, ["VIGENTRA-TRAFFIC-AHM-0099", None])
    assert not p.matched
    assert p.thresholds == {}
    assert sampler_settings(p, 20) == {"stride": 20}
    assert router_settings(p) == {}


def test_profile_overrides_merge_over_the_global_thresholds():
    base = {"detector": {"vehicle_conf": 0.15, "plate_conf": 0.2}, "reading": {"readers": ["crnn"]}}
    merged = deep_merge(base, resolve(DOC, ["cam07"]).thresholds)
    assert merged == {"detector": {"vehicle_conf": 0.1, "plate_conf": 0.2}, "reading": {"readers": ["crnn"]}}
    assert base["detector"]["vehicle_conf"] == 0.15      # the global dict is not modified


def test_sampling_and_night_mode_become_worker_arguments():
    p = resolve(DOC, ["cam07"])
    assert sampler_settings(p, 20) == {"stride": 2, "burst_plate_px": 20.0}
    assert router_settings(p) == {"low_light_luma": 256.0}
    off = resolve({"defaults": {"frame_quality": {"night_mode": "off"}}}, ["x"])
    assert router_settings(off) == {"enhance_low_light": False}
    assert sampler_settings(None, 5) == {"stride": 5}


def test_shipped_profiles_parse_and_only_override_known_threshold_sections():
    doc = yaml.safe_load((CONFIG / "camera_profiles.yaml").read_text())
    known = set(yaml.safe_load((CONFIG / "thresholds.yaml").read_text()))
    for name, block in doc["cameras"].items():
        assert set((block or {}).get("thresholds") or {}) <= known, name
    assert profile_for(CONFIG, "GRID-cam06").key == "cam06"
    assert profile_for(CONFIG, "traffic_01").thresholds["reading"]["preferred_state"] == "DL"
