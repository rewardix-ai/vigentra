# ANPR benchmark `pd_v2`

- **correct** = readable ground-truth plates the engine confirmed (exact string);
- **false** = confirmed or emitted strings that are not a readable ground-truth plate on that clip and do not
  fit any partly legible one (those are counted as unverifiable);
- **read** = any valid-format reading shown (confirmed or not).

| camera | tracks | with plate cand. | plate dets | OCR images | GT readable | correct confirmed | false confirmed/emitted | valid reads correct/false | fps | clip s per s | CPU % | RAM MB | GPU MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cam06_1080p | 43 | 34 | 1634 | 4379 | 5 | 4 | 0/0 | 4/12 | 1.57 | 0.069 | 107.6 | 4168 | 1190 |
| cam06_noon | 127 | 56 | 4250 | 5105 | 16 | 14 | 1/0 | 15/26 | 3.3 | 0.15 | 149.8 | 3228 | 1186 |
| delhi_1080p | 254 | 96 | 2594 | 10562 | 20 | 14 | 0/0 | 16/29 | 0.84 | 0.028 | 159.0 | 4020 | 1158 |
| **all** | 424 | 186 | 8478 | 20046 | 41 | 32 | 1/0 | 35/67 | | | | | |

## Missed and false plates

- **cam06_1080p**: missed GJ03KS7334; false -
- **cam06_noon**: missed GJ13AW0739, GJ1KR6061; false GJ11R6061
- **delhi_1080p**: missed DL11SD3385, DL1LAB9684, DL5SAR5109, DL6SAS6524, DL6SBE6415, UP14EC6398; false -

## Tracks with plate candidates by best-candidate width band

| camera | EXTREMELY_TINY | VERY_SMALL | SMALL | MEDIUM | LARGE |
|---|---:|---:|---:|---:|---:|
| cam06_1080p | 0 | 2 | 5 | 21 | 6 |
| cam06_noon | 3 | 26 | 6 | 20 | 1 |
| delhi_1080p | 7 | 14 | 22 | 44 | 9 |
