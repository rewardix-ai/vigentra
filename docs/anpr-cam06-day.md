# What grid cam06 actually carries, hour by hour

Measured 18 Sep 2026 by sampling the camera's own recording: a 2-minute clip every 2 hours across its
whole span, each run through the pipeline (`tools/anpr_benchmark.py`, the configuration merged in
`5b2eea0`, with the whole-crop text reader loaded).

## The stream is a recording, not live

Two captures taken 16 minutes apart are frame-for-frame identical, both stamped `17-06-2026 18:00:55`
at the same offset. The playlist says why:

```
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-ENDLIST
14 690 segments, 88 138 s
```

cam06 is a **24.5-hour recording that starts at 17 June 2026, 18:00**, and every session starts it from
the beginning. Holding one connection open plays it forward normally (at 405 s in, the overlay reads
18:06:27), and seeking reaches any point (`ANPR research repo: tools/hls_seek_clip.py`).

**What this means for the platform.** Each reader pass opens a new session, so every pass sees the same
opening minutes and the same handful of vehicles, however long the service runs. The plate counts on
this camera are therefore a property of the first minutes of the recording, not of the day's traffic. If
the grid keeps serving VOD, the worker should carry an offset per camera — pass *n* starting where pass
*n-1* stopped — so the passes walk through the recording instead of replaying its opening.

## The day, at 2-hour intervals

Each row is one 2-minute clip. "Plate px" is the median width of the plate candidates in that clip.
"Vehicles" counts tracker ids, which over-count vehicles: the tracker gives one vehicle several ids
(an id switch, a double box, a parked car re-acquired). With the fragments linked
(`anpr/track/vehicle_count.py`, checked by eye on the 12:00 clip) its 141 ids are **84 vehicles**.

| Clock (recording time) | Vehicles | With a plate | Plate px | Brightness | Plates read | Wrong |
|---|---:|---:|---:|---:|---:|---:|
| 17 Jun 18:00 | 60 | 11 | 47 | 113 | 4 | 0 |
| 17 Jun 20:00 | 93 | 18 | 35 | 65 | 0 | 0 |
| 17 Jun 22:00 | 47 | 6 | 46 | 60 | 0 | 0 |
| 18 Jun 00:00 | 9 | 4 | 55 | 67 | 0 | 0 |
| 18 Jun 02:00 | 0 | 0 | – | – | 0 | 0 |
| 18 Jun 04:00 | 2 | 2 | 48 | 53 | 0 | 0 |
| 18 Jun 06:00 | 2 | 2 | 28 | 56 | 0 | 0 |
| 18 Jun 08:00 | 28 | 8 | 37 | 70 | 1 | 0 |
| 18 Jun 10:00 | 91 | 31 | 45 | 69 | 13 | 0 |
| 18 Jun 12:00 | 128 | 44 | 37 | 32 | 14 (15 after change 8 of `docs/anpr-optimisation.md`) | 0 |
| 18 Jun 14:00 | 58 | 24 | 45 | 65 | 9 | 0 |
| 18 Jun 16:00 | 47 | 18 | 37 | 73 | 8 | 0 |
| 18 Jun 18:00 | 94 | 31 | 38 | 90 | 10 | 0 |

**59 plates from 26 minutes of footage.** The 15 readings of the 12:00 clip were checked against their
crops by eye: every one is correct, including the yellow and orange commercial plates
(RJ24TA3407, GJ37T9052, GJ13AW0739) whose vehicles the detector classes as buses.

## What the day says

- **Daylight is where the plates are.** 10:00-18:00 gives 8-14 plates per 2-minute clip; 20:00-06:00
  gives none at all, though the camera still sees traffic (93 vehicles at 20:00). Street lighting leaves
  plates bright enough to detect — the median candidate is 35-55 px at every hour — but not legible.
- **The engine reads nothing it cannot see.** Not one false plate in any of the 13 clips, including the
  night hours where it banks candidates and confirms none.
- **Traffic follows the clock**, not the camera: 128 vehicle tracks at noon, 0 between 02:00 and 03:00.
- **Cost follows the traffic.** The same 2 minutes cost 71 s at 02:00 and 389 s at 12:00 on an M1.
  Sizing has to assume the busy hour.

## Rerunning this

```
python tools/hls_seek_clip.py --cam cam06 --offset 64800 --seconds 120 --out noon.mp4     # research repo
ANPR_BENCH_VIDEOS=<dir> python tools/anpr_benchmark.py run --tag noon --device mps --clips cam06
```

Recording signs in to the grid gateway. The account holds one session, so the Docker stack must be
stopped while sampling, or central-api loses its grid session.
