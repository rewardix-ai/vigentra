# ANPR benchmark `night_base`

- **correct** = readable ground-truth plates the engine confirmed (exact string);
- **false** = confirmed or emitted strings that are not a readable ground-truth plate on that clip and do not
  fit any partly legible one (those are counted as unverifiable);
- **read** = any valid-format reading shown (confirmed or not).

| camera | tracks | with plate cand. | plate dets | OCR images | GT readable | correct confirmed | false confirmed/emitted | valid reads correct/false | fps | clip s per s | CPU % | RAM MB | GPU MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cam06_night | 105 | 22 | 410 | 1391 | 6 | 5 | 0/0 | 5/10 | 5.56 | 0.395 | 109.7 | 1782 | 1154 |
| cam07 | 10 | 3 | 63 | 206 | 1 | 0 | 0/0 | 0/3 | 5.26 | 0.385 | 80.2 | 1594 | 1119 |
| cam13 | 202 | 5 | 9 | 66 | 0 | 0 | 0/0 | 0/3 | 4.55 | 0.216 | 50.7 | 2376 | 1168 |
| cam15 | 103 | 5 | 50 | 260 | 0 | 0 | 0/0 | 0/1 | 7.04 | 0.282 | 71.2 | 2663 | 1155 |
| **all** | 420 | 35 | 532 | 1923 | 7 | 5 | 0/0 | 5/17 | | | | | |

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
