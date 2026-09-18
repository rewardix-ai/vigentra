# ANPR benchmark `realtime`

- **correct** = readable ground-truth plates the engine confirmed (exact string);
- **false** = confirmed or emitted strings that are not a readable ground-truth plate on that clip and do not
  fit any partly legible one (those are counted as unverifiable);
- **read** = any valid-format reading shown (confirmed or not).

| camera | tracks | with plate cand. | plate dets | OCR images | GT readable | correct confirmed | false confirmed/emitted | valid reads correct/false | fps | clip s per s | CPU % | RAM MB | GPU MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cam06 | 26 | 9 | 53 | 330 | 0 | 0 | 0/0 | 0/6 | 7.63 | 0.98 | 102.2 | 2074 | 1187 |
| cam06_1080p | 29 | 17 | 111 | 782 | 5 | 2 | 0/0 | 5/6 | 3.39 | 0.91 | 131.9 | 2915 | 1153 |
| cam06_night | 104 | 23 | 391 | 1360 | 6 | 5 | 0/0 | 6/13 | 11.13 | 1.384 | 136.3 | 2082 | 1120 |
| **all** | 159 | 49 | 555 | 2472 | 11 | 7 | 0/0 | 11/25 | | | | | |

## Missed and false plates

- **cam06_1080p**: missed GJ03KS7334, GJ11CK1044, GJ18X6705; false -
- **cam06_night**: missed GJ18X6705; false -

## Tracks with plate candidates by best-candidate width band

| camera | EXTREMELY_TINY | VERY_SMALL | SMALL | MEDIUM | LARGE |
|---|---:|---:|---:|---:|---:|
| cam06 | 0 | 0 | 4 | 3 | 2 |
| cam06_1080p | 0 | 1 | 5 | 9 | 2 |
| cam06_night | 0 | 10 | 3 | 9 | 1 |
