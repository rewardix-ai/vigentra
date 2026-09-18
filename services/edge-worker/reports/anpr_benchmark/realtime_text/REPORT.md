# ANPR benchmark `realtime_text`

- **correct** = readable ground-truth plates the engine confirmed (exact string);
- **false** = confirmed or emitted strings that are not a readable ground-truth plate on that clip and do not
  fit any partly legible one (those are counted as unverifiable);
- **read** = any valid-format reading shown (confirmed or not).

| camera | tracks | with plate cand. | plate dets | OCR images | GT readable | correct confirmed | false confirmed/emitted | valid reads correct/false | fps | clip s per s | CPU % | RAM MB | GPU MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cam06_1080p | 24 | 16 | 99 | 706 | 5 | 2 | 0/0 | 4/4 | 2.43 | 0.844 | 135.1 | 3823 | 1160 |
| cam06_night | 101 | 23 | 375 | 1324 | 6 | 5 | 0/0 | 6/12 | 7.9 | 1.027 | 134.1 | 3359 | 1129 |
| **all** | 125 | 39 | 474 | 2030 | 11 | 7 | 0/0 | 10/16 | | | | | |

## Missed and false plates

- **cam06_1080p**: missed GJ03KS7334, GJ18X6705, GJ32AG2883; false -
- **cam06_night**: missed GJ18X6705; false -

## Tracks with plate candidates by best-candidate width band

| camera | EXTREMELY_TINY | VERY_SMALL | SMALL | MEDIUM | LARGE |
|---|---:|---:|---:|---:|---:|
| cam06_1080p | 0 | 1 | 2 | 9 | 4 |
| cam06_night | 0 | 10 | 3 | 10 | 0 |
