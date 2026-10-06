#!/usr/bin/env python3
"""Render STORYBOARD.md from js/data/storyboard.js (the single source of the speaker notes).

    python3 deliverables/anpr-keynote/tools/storyboard.py

Scene order and titles come from the scene files (js/scenes/0*.js), so the document follows the
deck exactly.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FIELDS = [
    ("purpose", "Story purpose"), ("sees", "Visual"), ("animation", "Animation"), ("interaction", "Interaction"),
    ("say", "Speaker narration"), ("concept", "Technical concept"), ("asset", "Required asset"),
    ("understand", "Audience should understand"), ("transition", "Transition"),
]


def story() -> dict:
    """storyboard.js is a JS object literal with unquoted keys; quote them and read it as JSON."""
    src = (HERE / "js/data/storyboard.js").read_text()
    body = src[src.index("K.STORY = {") + len("K.STORY = "): src.rindex("};") + 1]
    body = re.sub(r"^(\s*)([a-z]+): ", r'\1"\2": ', body, flags=re.M)
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    return json.loads(body)


def scenes() -> list[tuple[str, str, str]]:
    out = []
    for path in sorted((HERE / "js/scenes").glob("0*.js")):
        for m in re.finditer(r'id: "([a-z]+)", act: "([^"]+)", title: "([^"]+)"', path.read_text()):
            out.append(m.groups())
    return out


def main() -> int:
    notes = story()
    lines = ["# From Pixels to Information: storyboard", "",
             "Generated from `js/data/storyboard.js` by `tools/storyboard.py`; edit the JS, not this file. "
             "Every number is in `js/data/project.js` with its source; every picture is real project footage or "
             "output (`tools/build_assets.py`, `tools/track_evidence.py`).", ""]
    for n, (sid, act, title) in enumerate(scenes(), 1):
        lines += [f"## {n}. {title}", f"*{act}*", ""]
        for key, label in FIELDS:
            if notes.get(sid, {}).get(key):
                lines.append(f"- **{label}.** {notes[sid][key]}")
        lines.append("")
    (HERE / "STORYBOARD.md").write_text("\n".join(lines))
    print(f"STORYBOARD.md: {len(scenes())} scenes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
