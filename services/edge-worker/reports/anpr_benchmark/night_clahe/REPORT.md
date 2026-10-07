# ANPR benchmark `night_clahe`

- **correct** = readable ground-truth plates the engine confirmed (exact string);
- **false** = confirmed or emitted strings that are not a readable ground-truth plate on that clip and do not
  fit any partly legible one (those are counted as unverifiable);
- **read** = any valid-format reading shown (confirmed or not).

| camera | tracks | with plate cand. | plate dets | OCR images | GT readable | correct confirmed | false confirmed/emitted | valid reads correct/false | fps | clip s per s | CPU % | RAM MB | GPU MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cam06_night | 105 | 22 | 413 | 1416 | 6 | 5 | 0/0 | 5/10 | 6.95 | 0.493 | 115.3 | 2333 | 1152 |
| cam07 | 10 | 3 | 63 | 209 | 1 | 0 | 0/0 | 0/3 | 6.9 | 0.505 | 66.5 | 2053 | 1119 |
| cam13 | 202 | 5 | 9 | 66 | 0 | 0 | 0/0 | 0/3 | 4.52 | 0.215 | 73.3 | 2058 | 1169 |
| cam15 | 103 | 5 | 50 | 260 | 0 | 0 | 0/0 | 0/1 | 4.12 | 0.165 | 54.1 | 1881 | 1155 |
| **all** | 420 | 35 | 535 | 1951 | 7 | 5 | 0/0 | 5/17 | | | | | |

## Missed and false plates

- **cam06_night**: missed GJ18X6705; false -
- **cam07**: missed GJ32AG0416; false -

## Tracks with plate candidates by best-candidate width band

| camera | EXTREMELY_TINY | VERY_SMALL | SMALL | MEDIUM | LARGE |
|---|---:|---:|---:|---:|---:|
| cam06_night | 0 | 12 | 2 | 5 | 3 |
| cam07 | 0 | 1 | 0 | 2 | 0 |
| cam13 | 0 | 1 | 2 | 1 | 1 |
| cam15 | 0 | 4 | 1 | 0 | 0 |
