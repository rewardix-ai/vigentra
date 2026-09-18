# ANPR benchmark `read_kinds`

- **correct** = readable ground-truth plates the engine confirmed (exact string);
- **false** = confirmed or emitted strings that are not a readable ground-truth plate on that clip and do not
  fit any partly legible one (those are counted as unverifiable);
- **read** = any valid-format reading shown (confirmed or not).

| camera | tracks | with plate cand. | plate dets | OCR images | GT readable | correct confirmed | false confirmed/emitted | valid reads correct/false | fps | clip s per s | CPU % | RAM MB | GPU MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cam01 | 129 | 4 | 6 | 82 | 0 | 0 | 0/0 | 0/3 | 7.47 | 0.495 | 53.9 | 3173 | 1187 |
| cam06_1080p | 43 | 30 | 803 | 3104 | 5 | 3 | 0/0 | 4/10 | 2.81 | 0.123 | 101.8 | 3530 | 1158 |
| cam07 | 10 | 3 | 63 | 206 | 1 | 0 | 0/0 | 0/3 | 7.53 | 0.552 | 71.7 | 3099 | 1225 |
| cam13 | 202 | 5 | 9 | 66 | 0 | 0 | 0/0 | 0/3 | 6.3 | 0.299 | 53.4 | 3067 | 1236 |
| delhi_1080p | 254 | 74 | 1892 | 8533 | 20 | 12 | 0/0 | 15/23 | 0.98 | 0.033 | 157.2 | 3883 | 1190 |
| **all** | 638 | 116 | 2773 | 11991 | 26 | 15 | 0/0 | 19/42 | | | | | |

## Missed and false plates

- **cam06_1080p**: missed GJ03KS7334, GJ18X6705; false -
- **cam07**: missed GJ32AG0416; false -
- **delhi_1080p**: missed DL11SD3385, DL1CW0942, DL1LAB9684, DL5SAR5109, DL6SAS6524, DL6SBE6415, UP13AY3893, UP14EC6398; false -

## Tracks with plate candidates by best-candidate width band

| camera | EXTREMELY_TINY | VERY_SMALL | SMALL | MEDIUM | LARGE |
|---|---:|---:|---:|---:|---:|
| cam01 | 0 | 1 | 1 | 1 | 1 |
| cam06_1080p | 0 | 2 | 3 | 23 | 2 |
| cam07 | 0 | 1 | 0 | 2 | 0 |
| cam13 | 0 | 1 | 2 | 1 | 1 |
| delhi_1080p | 0 | 2 | 14 | 36 | 22 |
