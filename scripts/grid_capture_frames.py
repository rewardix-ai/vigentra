# -*- coding: utf-8 -*-
"""Capture full-resolution frames from the Sentinel grid for location verification.

Grabs several passes per camera. Because each feed is a looping recording the
passes land at different points in the loop, which is how a daylight frame gets
caught on a camera that was dark on the first attempt. Keeps the brightest.

Follows the integrator's guide: paced load, sequential per camera, backoff on
failure, decode warnings non-fatal.
"""
import urllib.request, urllib.parse, subprocess, os, sys, time, json

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
ROOT = "https://live.corp8.cloud"
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
IDS = sys.argv[2:] or [str(i) for i in range(1, 31)]


def get(u, timeout=25):
    p = urllib.parse.urlparse(u)
    q = urllib.parse.parse_qs(p.query)
    q["cookieCheck"] = ["1"]
    u2 = urllib.parse.urlunparse(p._replace(query=urllib.parse.urlencode(q, doseq=True)))
    r = urllib.request.Request(u2, headers={"User-Agent": UA, "Referer": ROOT + "/"})
    return urllib.request.urlopen(r, timeout=timeout).read()


def brightness(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
                        "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=60)
    for line in r.stderr.splitlines() + r.stdout.splitlines():
        if "YAVG" in line:
            try:
                return float(line.split("=")[-1])
            except ValueError:
                pass
    return -1.0


def one_pass(cid, tag):
    base = f"{ROOT}/live/stream/{cid}/"
    master = get(base + "index.m3u8").decode()
    child = next(l.strip() for l in master.splitlines()
                 if l.strip() and not l.startswith("#"))
    media = get(base + child).decode()
    init, segs = None, []
    for line in media.splitlines():
        s = line.strip()
        if s.startswith("#EXT-X-MAP:"):
            init = s.split('URI="', 1)[1].split('"', 1)[0]
        elif s and not s.startswith("#") and "_seg" in s:
            segs.append(s)
    if not segs:
        raise RuntimeError("no segments")
    pick = segs[-3:-1] if len(segs) >= 3 else segs[:1]
    blob = b""
    if init:
        blob += get(base + init)
    for s in pick:
        blob += get(base + s)
    raw = os.path.join(OUT, f"_t{cid}_{tag}.mp4")
    open(raw, "wb").write(blob)
    jpg = os.path.join(OUT, f"_t{cid}_{tag}.jpg")
    # native resolution, best quality - text legibility is the whole point
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", raw,
                    "-frames:v", "1", "-q:v", "1", jpg],
                   capture_output=True, text=True, timeout=90)
    y = brightness(raw)
    os.remove(raw)
    if not os.path.exists(jpg) or os.path.getsize(jpg) < 2000:
        raise RuntimeError("decode produced nothing")
    return jpg, y


results = {}
for cid in IDS:
    best, best_y = None, -999.0
    for tag in range(3):
        try:
            jpg, y = one_pass(cid, tag)
            if y > best_y:
                if best and os.path.exists(best):
                    os.remove(best)
                best, best_y = jpg, y
            else:
                os.remove(jpg)
        except Exception as e:
            time.sleep(1.5 * (tag + 1))          # backoff, never a tight loop
        time.sleep(0.4)
    if best:
        final = os.path.join(OUT, f"cam{int(cid):02d}_hi.jpg")
        if os.path.exists(final):
            os.remove(final)
        os.rename(best, final)
        results[cid] = round(best_y, 1)
        print(f"cam {cid:>2}: YAVG {best_y:6.1f}  {os.path.getsize(final)//1024}KB", flush=True)
    else:
        results[cid] = None
        print(f"cam {cid:>2}: FAILED", flush=True)
    time.sleep(0.6)                               # pace the load between cameras

json.dump(results, open(os.path.join(OUT, "brightness.json"), "w"), indent=1)
