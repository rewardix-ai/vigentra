"""Per-camera configuration (config/camera_profiles.yaml).

One global pipeline, tuned per camera without code changes. A camera's profile holds:
- `thresholds`: overrides deep-merged over thresholds.yaml when that camera's engine is built
  (e.g. `reading.preferred_state`, `detector.vehicle_imgsz`);
- `sampling`: the worker's AdaptiveSampler settings for that camera;
- `frame_quality`: the FrameQualityRouter settings (`night_mode: auto | on | off`);
- descriptive fields (`site`, `lighting`, `plate_side`, `difficulty`, `notes`), measured by
  tools/anpr_profile_cameras.py and kept for operators. The pipeline does not read them.

A camera is found by its key or by any of its `aliases`, case-insensitively. The live worker offers
the canonical registry id and the grid id (`cam06`); the benchmark offers the clip's camera name.
An unknown camera gets `defaults` alone.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml


@dataclass
class CameraProfile:
    key: str = "default"
    thresholds: dict = field(default_factory=dict)
    sampling: dict = field(default_factory=dict)
    frame_quality: dict = field(default_factory=dict)
    info: dict = field(default_factory=dict)

    @property
    def matched(self) -> bool:
        return self.key != "default"


def deep_merge(base: dict, over: dict) -> dict:
    """A copy of `base` with `over` merged in; nested dicts merge, everything else is replaced."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_profiles(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


_SECTIONS = ("thresholds", "sampling", "frame_quality")


def resolve(doc: dict, keys: Iterable[Optional[str]]) -> CameraProfile:
    """The profile for the first of `keys` that names a camera (by key or alias)."""
    defaults = doc.get("defaults") or {}
    cams: dict[str, Any] = doc.get("cameras") or {}
    index: dict[str, str] = {}
    for name, block in cams.items():
        index[str(name).lower()] = name
        for alias in (block or {}).get("aliases") or []:
            index[str(alias).lower()] = name
    name = next((index[k.lower()] for k in keys if k and k.lower() in index), None)
    block = deep_merge(defaults, cams.get(name) or {}) if name else copy.deepcopy(defaults)
    return CameraProfile(
        key=name or "default",
        **{s: block.get(s) or {} for s in _SECTIONS},
        info={k: v for k, v in block.items() if k not in _SECTIONS and k != "aliases"},
    )


def profile_for(config_dir: str | Path, *keys: Optional[str]) -> CameraProfile:
    return resolve(load_profiles(Path(config_dir) / "camera_profiles.yaml"), keys)
