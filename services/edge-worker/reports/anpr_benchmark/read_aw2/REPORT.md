# ANPR benchmark `read_aw2`

- **correct** = readable ground-truth plates the engine confirmed (exact string);
- **false** = confirmed or emitted strings that are not a readable ground-truth plate on that clip and do not
  fit any partly legible one (those are counted as unverifiable);
- **read** = any valid-format reading shown (confirmed or not).

| camera | tracks | with plate cand. | plate dets | OCR images | GT readable | correct confirmed | false confirmed/emitted | valid reads correct/false | fps | clip s per s | CPU % | RAM MB | GPU MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cam01 | 88 | 2 | 7 | 83 | 0 | 0 | 0/0 | 0/2 | 5.15 | 0.427 | 48.6 | 1721 | 1214 |
| cam04 | 143 | 5 | 47 | 335 | 0 | 0 | 0/0 | 0/4 | 4.36 | 0.336 | 65.6 | 1998 | 1209 |
| cam06_1080p | 42 | 30 | 770 | 3005 | 5 | 3 | 0/0 | 4/8 | 3.86 | 0.176 | 104.3 | 4584 | 1158 |
| cam07 | 2 | 1 | 24 | 66 | 1 | 0 | 0/0 | 0/1 | 5.34 | 0.934 | 57.5 | 1824 | 1184 |
| delhi_1080p | 254 | 74 | 1892 | 8598 | 20 | 12 | 0/0 | 15/23 | 1.57 | 0.053 | 171.2 | 5049 | 1214 |
| **all** | 529 | 112 | 2740 | 12087 | 26 | 15 | 0/0 | 19/38 | | | | | |

## Missed and false plates

- **cam06_1080p**: missed GJ03KS7334, GJ18X6705; false -
- **cam07**: missed GJ32AG0416; false -
- **delhi_1080p**: missed DL11SD3385, DL1CW0942, DL1LAB9684, DL5SAR5109, DL6SAS6524, DL6SBE6415, UP13AY3893, UP14EC6398; false -

## Tracks with plate candidates by best-candidate width band

| camera | EXTREMELY_TINY | VERY_SMALL | SMALL | MEDIUM | LARGE |
|---|---:|---:|---:|---:|---:|
| cam01 | 0 | 0 | 0 | 1 | 1 |
| cam04 | 0 | 1 | 0 | 2 | 2 |
| cam06_1080p | 0 | 2 | 4 | 22 | 2 |
| cam07 | 0 | 0 | 1 | 0 | 0 |
| delhi_1080p | 0 | 2 | 14 | 36 | 22 |
