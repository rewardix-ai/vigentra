"""Cut a record_demo.py recording into the submission video: the scenes in the order they were
recorded, every wait (the operator's password, the alert arriving) shortened to its first 2.5 s
and last 6 s, and a 3 s title card. Warns when the cut runs over the brief's three minutes.

    <ANPR venv python> edit_demo.py <recdir> <out.mp4> "Own-feed demonstration"

Needs Pillow and the ffmpeg that ships with imageio-ffmpeg (both in the ANPR research venv).
"""
import json, os, subprocess, sys
from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg

REC, OUT = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
TITLE = sys.argv[3] if len(sys.argv) > 3 else "Demonstration"
FF = imageio_ffmpeg.get_ffmpeg_exe()
LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "presentation", "logo-on-dark.png")
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
REGULAR = "/System/Library/Fonts/Supplemental/Arial.ttf"
W, H, FPS = 1920, 1080, 30
BG = (11, 18, 32)

frames = sorted((json.loads(line) for line in open(os.path.join(REC, "frames.jsonl"))), key=lambda f: f["t"])
order, spans, waits = [], {}, []
for line in open(os.path.join(REC, "marks.jsonl")):
    m = json.loads(line)
    if m["scene"] == "wait":
        if m["what"] == "start":
            waits.append([m["t"], None])
        elif waits and waits[-1][1] is None:
            waits[-1][1] = m["t"]
    elif m["what"] == "start":
        if m["scene"] in order:
            order.remove(m["scene"])          # a later take replaces an earlier one
        order.append(m["scene"])
        spans[m["scene"]] = {"start": m["t"]}
    else:
        spans.setdefault(m["scene"], {})[m["what"]] = m["t"]


def card(name, title, lines, foot):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    logo = Image.open(LOGO).convert("RGBA")
    logo.thumbnail((560, 180))
    img.paste(logo, ((W - logo.width) // 2, 150), logo)
    font_title, font_line, font_foot = (ImageFont.truetype(BOLD, 64), ImageFont.truetype(REGULAR, 36),
                                        ImageFont.truetype(REGULAR, 26))
    y = 390
    draw.text(((W - draw.textlength(title, font=font_title)) / 2, y), title, font=font_title, fill=(255, 255, 255))
    y += 120
    for line in lines:
        draw.text(((W - draw.textlength(line, font=font_line)) / 2, y), line, font=font_line, fill=(203, 213, 225))
        y += 60
    draw.text(((W - draw.textlength(foot, font=font_foot)) / 2, H - 100), foot, font=font_foot, fill=(148, 163, 184))
    path = os.path.join(REC, name + ".jpg")
    img.save(path, quality=95)
    return path



def sample(name):
    """(frame path, seconds) runs for one scene, resampled at FPS, with the waits cut short."""
    span = spans.get(name, {})
    if "start" not in span or "end" not in span:
        print("scene incomplete:", name)
        return []
    keep = [(span["start"], span["end"])]
    for a, b in waits:
        if b and b - a > 10:
            cut = (a + 2.5, b - 6.0)
            keep = [piece for lo, hi in keep for piece in
                    ([(lo, hi)] if cut[1] <= lo or cut[0] >= hi else [(lo, max(lo, cut[0])), (min(hi, cut[1]), hi)])]
    runs, j = [], 0
    for lo, hi in keep:
        t = lo
        while t < hi:
            while j + 1 < len(frames) and frames[j + 1]["t"] <= t:
                j += 1
            path = os.path.join(REC, "frames", frames[j]["f"])
            if runs and runs[-1][0] == path:
                runs[-1][1] += 1
            else:
                runs.append([path, 1])
            t += 1 / FPS
    return [(path, n / FPS) for path, n in runs]


intro = card("intro", "Vigentra", [TITLE, "Recorded in the running platform. Nothing is mocked or animated."],
             "Gujarat Police Innovation Challenge 2026")
entries = [(intro, 3.0)]
for scene in order:
    part = sample(scene)
    print(f"{scene:<11} {sum(d for _, d in part):6.1f} s")
    entries += part
total = sum(d for _, d in entries)
print(f"total {total:.1f} s" + ("   ** over the brief's 3:00: re-record fewer scenes: record_demo.py <out> own onboard watch alert route **" if total > 180 else ""))

listing = os.path.join(REC, "edit.ffconcat")
with open(listing, "w") as handle:
    handle.write("ffconcat version 1.0\n")
    for path, seconds in entries:
        handle.write(f"file '{path}'\nduration {seconds:.4f}\n")
    handle.write(f"file '{entries[-1][0]}'\n")
subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing,
                "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0b1220,"
                       f"fps={FPS},format=yuv420p",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-movflags", "+faststart", OUT], check=True)
print("wrote", OUT)
