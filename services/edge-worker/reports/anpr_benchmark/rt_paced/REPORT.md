# ANPR benchmark `rt_paced`

- **correct** = readable ground-truth plates the engine confirmed (exact string);
- **false** = confirmed or emitted strings that are not a readable ground-truth plate on that clip and do not
  fit any partly legible one (those are counted as unverifiable);
- **read** = any valid-format reading shown (confirmed or not).

| camera | tracks | with plate cand. | plate dets | OCR images | GT readable | correct confirmed | false confirmed/emitted | valid reads correct/false | fps | clip s per s | CPU % | RAM MB | GPU MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cam06 | 24 | 8 | 47 | 284 | 0 | 0 | 0/0 | 0/3 | 6.11 | 0.999 | 107.2 | 3379 | 1200 |
| cam06_1080p | 27 | 17 | 91 | 632 | 5 | 3 | 0/0 | 3/5 | 2.71 | 0.921 | 121.4 | 3789 | 1152 |
| cam06_night | 89 | 17 | 349 | 1016 | 6 | 5 | 0/0 | 6/9 | 8.86 | 1.309 | 128.5 | 3649 | 1192 |
| **all** | 140 | 42 | 487 | 1932 | 11 | 8 | 0/0 | 9/17 | | | | | |

## Missed and false plates

- **cam06_1080p**: missed GJ03KS7334, GJ23H1546; false -
- **cam06_night**: missed GJ18X6705; false -

## Tracks with plate candidates by best-candidate width band

| camera | EXTREMELY_TINY | VERY_SMALL | SMALL | MEDIUM | LARGE |
|---|---:|---:|---:|---:|---:|
| cam06 | 0 | 1 | 3 | 2 | 2 |
| cam06_1080p | 0 | 3 | 4 | 9 | 1 |
| cam06_night | 0 | 8 | 2 | 6 | 1 |
