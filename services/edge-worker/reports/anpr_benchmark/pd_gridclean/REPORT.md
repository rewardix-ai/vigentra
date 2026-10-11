# ANPR benchmark `pd_gridclean`

- **correct** = readable ground-truth plates the engine confirmed (exact string);
- **false** = confirmed or emitted strings that are not a readable ground-truth plate on that clip and do not
  fit any partly legible one (those are counted as unverifiable);
- **read** = any valid-format reading shown (confirmed or not).

| camera | tracks | with plate cand. | plate dets | OCR images | GT readable | correct confirmed | false confirmed/emitted | valid reads correct/false | fps | clip s per s | CPU % | RAM MB | GPU MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cam06_1080p | 43 | 32 | 1471 | 3964 | 5 | 4 | 0/0 | 4/9 | 1.93 | 0.085 | 112.9 | 2700 | 1198 |
| cam06_noon | 127 | 51 | 4159 | 4618 | 16 | 14 | 0/0 | 15/24 | 5.57 | 0.253 | 147.2 | 2816 | 1194 |
| delhi_1080p | 254 | 88 | 2540 | 10730 | 20 | 12 | 0/0 | 16/25 | 0.82 | 0.028 | 156.2 | 3631 | 1158 |
| **all** | 424 | 171 | 8170 | 19312 | 41 | 30 | 0/0 | 35/58 | | | | | |

## Missed and false plates

- **cam06_1080p**: missed GJ03KS7334; false -
- **cam06_noon**: missed GJ13AW0739, GJ1KR6061; false -
- **delhi_1080p**: missed DL11SD3385, DL1LAB9684, DL5SAR5109, DL5SBW7737, DL6SAS6524, DL6SBE6415, UP13AY3893, UP14EC6398; false -

## Tracks with plate candidates by best-candidate width band

| camera | EXTREMELY_TINY | VERY_SMALL | SMALL | MEDIUM | LARGE |
|---|---:|---:|---:|---:|---:|
| cam06_1080p | 0 | 2 | 6 | 21 | 3 |
| cam06_noon | 2 | 25 | 1 | 20 | 3 |
| delhi_1080p | 1 | 13 | 18 | 48 | 8 |
