#!/usr/bin/env python3
"""Join annotated segments into one demo video, with title cards, as H.264.

    python tools/assemble_demo_video.py OUT.mp4 \
        --segment delhi.mp4 "1 · Delhi test clip" "Labelled 1080p street footage" \
        --segment cam06.mp4 "2 · Government feed — CAM06" "Live Sentinel grid camera" \
        --outro "Delhi: DL1CQ5334 confirmed" --outro "CAM06: 3 readings"

Cards are drawn here; segments come from tools/annotate_video.py and are not
altered, only re-timed to 30 fps and re-encoded with the ffmpeg that
imageio-ffmpeg ships.
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

LOGO = Path(__file__).resolve().parents[3] / "deliverables" / "presentation" / "logo-on-dark.png"
W, H, FPS = 1920, 1080, 30
NAVY, MUTE, ICE = (14, 30, 51), (174, 189, 209), (143, 180, 230)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(f"/System/Library/Fonts/Supplemental/{'Arial Bold' if bold else 'Arial'}.ttf", size)


def card(path: Path, lines: list[tuple[str, int, bool, tuple]], seconds: float) -> Path:
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    if LOGO.exists():
        logo = Image.open(LOGO).convert("RGBA")
        logo = logo.resize((int(logo.width * 150 / logo.height), 150))
        img.paste(logo, (140, 150), logo)
    y = 400
    for text, size, bold, colour in lines:
        d.text((150, y), text, font=font(size, bold), fill=colour)
        y += int(size * 1.6)
    frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for _ in range(int(seconds * FPS)):
        vw.write(frame)
    vw.release()
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--segment", nargs=3, action="append", metavar=("CLIP", "TITLE", "SUBTITLE"), required=True)
    ap.add_argument("--outro", action="append", default=[])
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp())
    parts = [card(tmp / "intro.mp4", [
        ("Vigentra — ANPR on real footage", 64, True, (255, 255, 255)),
        ("Gujarat Police Innovation Challenge 2026 · CCTV Integration Hackathon", 30, False, MUTE),
        ("Every box and reading shown is the engine's own output — nothing is drawn by hand.", 30, False, ICE),
    ], 4.0)]
    for i, (clip, title, subtitle) in enumerate(a.segment):
        parts.append(card(tmp / f"card{i}.mp4", [(title, 60, True, (255, 255, 255)), (subtitle, 32, False, MUTE)], 2.5))
        parts.append(Path(clip))
    parts.append(card(tmp / "outro.mp4", [("What the engine read", 56, True, (255, 255, 255))]
                      + [(line, 34, False, MUTE) for line in a.outro]
                      + [("Vigentra · github.com/rewardix-ai/vigentra", 28, False, ICE)], 5.0))

    inputs = sum((["-i", str(p)] for p in parts), [])
    chains = "".join(f"[{i}:v]fps={FPS},scale={W}:{H},setsar=1,format=yuv420p[v{i}];" for i in range(len(parts)))
    joined = "".join(f"[v{i}]" for i in range(len(parts)))
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", *inputs, "-filter_complex",
                    f"{chains}{joined}concat=n={len(parts)}:v=1:a=0[out]", "-map", "[out]",
                    "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-movflags", "+faststart", a.out],
                   check=True)
    print(f"{a.out}: {len(parts)} parts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
