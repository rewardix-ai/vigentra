# ANPR benchmark `x_final`

- **correct** = readable ground-truth plates the engine confirmed (exact string);
- **false** = confirmed or emitted strings that are not a readable ground-truth plate on that clip and do not
  fit any partly legible one (those are counted as unverifiable);
- **read** = any valid-format reading shown (confirmed or not).

| camera | tracks | with plate cand. | plate dets | OCR images | GT readable | correct confirmed | false confirmed/emitted | valid reads correct/false | fps | clip s per s | CPU % | RAM MB | GPU MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cam01 | 129 | 4 | 6 | 82 | 0 | 0 | 0/0 | 0/3 | 7.12 | 0.472 | 52.3 | 3152 | 1195 |
| cam04 | 179 | 7 | 55 | 445 | 0 | 0 | 0/0 | 0/6 | 4.87 | 0.294 | 80.9 | 3192 | 1233 |
| cam06 | 31 | 13 | 153 | 708 | 0 | 0 | 0/0 | 0/9 | 5.63 | 0.318 | 98.3 | 3193 | 1233 |
| cam06_1080p | 43 | 30 | 803 | 3104 | 5 | 3 | 0/0 | 4/10 | 2.67 | 0.117 | 102.7 | 3428 | 1157 |
| cam13 | 202 | 5 | 9 | 66 | 0 | 0 | 0/0 | 0/3 | 6.33 | 0.3 | 52.6 | 3010 | 1242 |
| delhi_1080p | 254 | 74 | 1892 | 8533 | 20 | 13 | 0/0 | 15/23 | 0.92 | 0.031 | 158.4 | 4080 | 1192 |
| **all** | 838 | 133 | 2918 | 12938 | 25 | 16 | 0/0 | 19/54 | | | | | |

## Missed and false plates

- **cam06_1080p**: missed GJ03KS7334, GJ18X6705; false -
- **delhi_1080p**: missed DL11SD3385, DL1LAB9684, DL5SAR5109, DL6SAS6524, DL6SBE6415, UP13AY3893, UP14EC6398; false -

## Tracks with plate candidates by best-candidate width band

| camera | EXTREMELY_TINY | VERY_SMALL | SMALL | MEDIUM | LARGE |
|---|---:|---:|---:|---:|---:|
| cam01 | 0 | 1 | 1 | 1 | 1 |
| cam04 | 0 | 2 | 0 | 2 | 3 |
| cam06 | 0 | 1 | 5 | 5 | 2 |
| cam06_1080p | 0 | 2 | 3 | 23 | 2 |
| cam13 | 0 | 1 | 2 | 1 | 1 |
| delhi_1080p | 0 | 2 | 14 | 36 | 22 |
