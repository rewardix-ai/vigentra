#!/usr/bin/env python3
"""Sentinel Module 1 - synthetic demo footage generator.

Module 1 ships NO real CCTV footage and connects to NO real CCTV portal.
Every clip produced here is drawn from scratch with ffmpeg primitives and is
watermarked as synthetic. The mock VMS services serve these files the same way
a real VMS would hand back an RTSP/HLS stream, so the federation + video-broker
path is exercised end to end without touching anything real.

Usage:
    python scripts/generate_demo_videos.py [--force]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIDTH, HEIGHT, FPS, DURATION = 960, 540, 15, 24

FONT_CANDIDATES = [
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def find_font() -> str:
    for cand in FONT_CANDIDATES:
        probe = cand
        if len(cand) > 2 and cand[1] == ":":  # windows path -> msys-style probe
            probe = "/" + cand[0].lower() + cand[2:]
        if Path(cand).exists() or Path(probe).exists():
            # ffmpeg filtergraphs treat ':' as an option separator.
            return cand.replace(":", "\\:")
    sys.exit("ERROR: no TrueType font found for ffmpeg drawtext.")


FONT = find_font()


def _esc(expr: str) -> str:
    """Escape a filter expression for embedding in a filtergraph."""
    return expr.replace(",", "\\,")


def moving_box(*, speed, offset, y, w, h, color, direction="right", span=1240):
    """A box that loops across the frame; `speed` is px/second."""
    if direction == "right":
        x = "mod(t*{s}+{o},{span})-{off}".format(s=speed, o=offset, span=span, off=w + 40)
    else:
        x = "{start}-mod(t*{s}+{o},{span})".format(start=span - w - 40, s=speed, o=offset, span=span)
    return "drawbox=x='{x}':y={y}:w={w}:h={h}:color={c}:t=fill".format(
        x=_esc(x), y=y, w=w, h=h, c=color
    )


def vehicle(*, speed, offset, y, w, h, body, roof, direction="right"):
    """A crude two-tone vehicle: body slab plus a smaller cabin on top."""
    return [
        moving_box(speed=speed, offset=offset, y=y, w=w, h=h, color=body, direction=direction),
        moving_box(speed=speed, offset=offset, y=y - 17, w=int(w * 0.55), h=19,
                   color=roof, direction=direction),
    ]


def road_scene(direction: str, tint: str) -> list[str]:
    """A carriageway seen from a pole-mounted traffic camera."""
    f = []
    # static skyline so the upper third is not an empty band
    for bx, bw, by in [(0, 150, 96), (155, 90, 62), (250, 120, 108), (375, 175, 74),
                       (555, 105, 118), (665, 140, 58), (810, 150, 92)]:
        f.append("drawbox=x={x}:y={y}:w={w}:h={h}:color=0x272d34:t=fill".format(
            x=bx, y=by, w=bw, h=150 - by))
        for wy in range(by + 12, 148, 22):
            f.append("drawbox=x={x}:y={y}:w={w}:h=9:color=0x3d454e:t=fill".format(
                x=bx + 10, y=wy, w=bw - 20))
    f += [
        # asphalt, kerb, footpath
        "drawbox=x=0:y=232:w=iw:h=ih:color=0x3b4148:t=fill",
        "drawbox=x=0:y=222:w=iw:h=12:color=0x5b636d:t=fill",
        "drawbox=x=0:y=150:w=iw:h=72:color=0x2c333a:t=fill",
        # centre line
        "drawbox=x=0:y=372:w=iw:h=4:color=0xaab2bb@0.7:t=fill",
    ]
    # lane dashes scrolling with traffic
    for k in range(6):
        f.append(moving_box(speed=300, offset=k * 220, y=302, w=110, h=7,
                            color="0xe4e9ef@0.75", direction=direction))
    f += vehicle(speed=210, offset=0, y=396, w=150, h=62, body="0x8d99a6", roof="0x5f6a76", direction=direction)
    f += vehicle(speed=280, offset=520, y=402, w=120, h=54, body="0xb4a58f", roof="0x7d715f", direction=direction)
    f += vehicle(speed=165, offset=980, y=252, w=170, h=58, body="0x6f7c8a", roof="0x4a545e", direction=direction)
    f += vehicle(speed=340, offset=300, y=258, w=96, h=44, body="0x9c8f84", roof="0x6a615a", direction=direction)
    # pedestrians on the footpath
    f.append(moving_box(speed=42, offset=120, y=178, w=16, h=40, color="0x9aa4ae", direction="left"))
    f.append(moving_box(speed=55, offset=700, y=180, w=15, h=38, color="0x87919b", direction="right"))
    f.append("colorbalance=" + tint)
    return f


def plaza_scene(direction: str, tint: str) -> list[str]:
    """A municipal junction / riverfront walkway - slower, people-dominated."""
    f = [
        # river band + railing above the walkway
        "drawbox=x=0:y=44:w=iw:h=76:color=0x2a3640:t=fill",
        "drawbox=x=0:y=104:w=iw:h=5:color=0x4a5866:t=fill",
        "drawbox=x=0:y=250:w=iw:h=ih:color=0x454b52:t=fill",
        "drawbox=x=0:y=250:w=iw:h=10:color=0x69717a:t=fill",
        "drawbox=x=0:y=120:w=iw:h=130:color=0x30373e:t=fill",
        "drawbox=x=0:y=118:w=iw:h=6:color=0x555d66:t=fill",
    ]
    # paving grid
    for k in range(5):
        f.append("drawbox=x=0:y={y}:w=iw:h=2:color=0x616973@0.6:t=fill".format(y=300 + k * 52))
    peds = [
        (58, 0, 430, 18, 46, "0xa8b2bc", "right"),
        (46, 240, 452, 17, 44, "0x8e98a2", "left"),
        (64, 520, 398, 16, 42, "0xb7a99a", "right"),
        (39, 760, 470, 19, 48, "0x99a3ad", "left"),
        (72, 980, 372, 15, 40, "0x7f8993", "right"),
        (51, 1120, 344, 14, 36, "0xa39588", "left"),
    ]
    for speed, offset, y, w, h, color, d in peds:
        f.append(moving_box(speed=speed, offset=offset, y=y, w=w, h=h, color=color, direction=d))
        f.append(moving_box(speed=speed, offset=offset, y=y - 13, w=w - 4, h=13, color="0x5d666f", direction=d))
    # a slow two-wheeler crossing the plaza
    f += vehicle(speed=120, offset=400, y=286, w=64, h=30, body="0x77828d", roof="0x515a63", direction=direction)
    f.append("colorbalance=" + tint)
    return f


def overlays(cam_name: str, cam_id: str, owner: str) -> list[str]:
    def text(body, x, y, size, color, extra=""):
        return ("drawtext=fontfile='{f}':text='{t}':x={x}:y={y}"
                ":fontsize={s}:fontcolor={c}{e}").format(
            f=FONT, t=body, x=x, y=y, s=size, c=color, e=extra)

    blink = ":enable='" + _esc("lt(mod(t,2),1.2)") + "'"
    return [
        # sensor grain + lens fall-off, applied before the HUD so the HUD stays crisp
        "noise=alls=9:allf=t",
        "gblur=sigma=0.45",
        "vignette=PI/6.5",
        "eq=brightness=0.02:contrast=1.06:saturation=0.70",
        "drawbox=x=0:y=0:w=iw:h=44:color=black@0.62:t=fill",
        "drawbox=x=0:y=ih-40:w=iw:h=40:color=black@0.62:t=fill",
        text(cam_name, 18, 12, 22, "white"),
        text("SYNTHETIC DEMO FOOTAGE - NOT REAL CCTV", "(w-tw)/2", 15, 15, "0xFFD166@0.92"),
        text("REC", "w-tw-52", 13, 20, "0xFF5A5A", blink),
        "drawbox=x=iw-34:y=17:w=13:h=13:color=0xFF5A5A@0.9:t=fill" + blink,
        text(owner + "  |  " + cam_id, 18, "h-29", 17, "0xB9C6D6"),
        text("%{pts\\:hms}", "w-tw-18", "h-29", 17, "0xB9C6D6"),
        "format=yuv420p",
    ]


def render(out: Path, scene: list[str], cam_name: str, cam_id: str, owner: str, force: bool) -> None:
    if out.exists() and not force:
        print("  skip (exists): " + out.name)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = ",".join(scene + overlays(cam_name, cam_id, owner))
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i",
        "color=c=0x1b2026:s={w}x{h}:r={r}:d={d}".format(w=WIDTH, h=HEIGHT, r=FPS, d=DURATION),
        "-vf", vf, "-t", str(DURATION),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "28",
        "-g", str(FPS * 2), "-movflags", "+faststart", "-an", str(out),
    ]
    print("  render: " + out.name)
    subprocess.run(cmd, check=True)


CLIPS = [
    ("traffic/traffic_01.mp4", road_scene("right", "rs=-0.04:bs=0.10"),
     "CG Road East", "TRF-001", "GUJARAT TRAFFIC POLICE VMS"),
    ("traffic/traffic_02.mp4", road_scene("left", "rs=0.06:bs=-0.02"),
     "Ashram Road North", "TRF-002", "GUJARAT TRAFFIC POLICE VMS"),
    ("municipal/municipal_01.mp4", plaza_scene("right", "rs=0.02:bs=0.06"),
     "Municipal Junction 1", "SMC-101", "MUNICIPAL CORPORATION VMS"),
    ("municipal/municipal_02.mp4", plaza_scene("left", "gs=0.05:bs=0.08"),
     "Riverfront Walkway", "SMC-102", "MUNICIPAL CORPORATION VMS"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-render clips that already exist")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg not found on PATH.", file=sys.stderr)
        print("Copy your own MP4 files into {p} instead.".format(p=ROOT / "data" / "videos"), file=sys.stderr)
        return 1

    print("Generating synthetic demo footage -> {p}".format(p=ROOT / "data" / "videos"))
    for rel, scene, name, cam_id, owner in CLIPS:
        render(ROOT / "data" / "videos" / rel, scene, name, cam_id, owner, args.force)

    print("\nDone.")
    for rel, *_ in CLIPS:
        p = ROOT / "data" / "videos" / rel
        print("  {r:34s} {k:8.1f} KiB".format(r=rel, k=p.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
