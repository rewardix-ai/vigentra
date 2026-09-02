"""Whether the bundled demo clips are present.

Module 1 ships no footage: the demo clips are drawn from scratch by
`scripts/generate_demo_videos.py` and are deliberately not committed, because a
repository is not the place for eighty megabytes of synthetic video that any
checkout can regenerate.

The consequence is that a handful of tests - the ones that assert real bytes
come back through the media proxy - need an artefact a fresh clone does not
have and cannot make without ffmpeg on PATH. Skipping those with a reason is
honest. Letting them fail teaches the suite's readers to ignore red, which is
the more expensive habit.

Everything else about video access - who may open a session, what a revoked
session may do, which modes a source offers, what the audit trail records - is
tested without a single frame, because none of it depends on one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO_CLIPS = (
    ROOT / "data" / "videos" / "traffic" / "traffic_01.mp4",
    ROOT / "data" / "videos" / "municipal" / "municipal_01.mp4",
)


def demo_clips_present() -> bool:
    return all(clip.is_file() and clip.stat().st_size > 0 for clip in DEMO_CLIPS)


requires_demo_clips = pytest.mark.skipif(
    not demo_clips_present(),
    reason=(
        "bundled demo clips absent - run `python scripts/generate_demo_videos.py` "
        "(needs ffmpeg on PATH)"
    ),
)
