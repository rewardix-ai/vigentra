# ANPR benchmark `pdg_gridclean`

- **correct** = readable ground-truth plates the engine confirmed (exact string);
- **false** = confirmed or emitted strings that are not a readable ground-truth plate on that clip and do not
  fit any partly legible one (those are counted as unverifiable);
- **read** = any valid-format reading shown (confirmed or not).

| camera | tracks | with plate cand. | plate dets | OCR images | GT readable | correct confirmed | false confirmed/emitted | valid reads correct/false | fps | clip s per s | CPU % | RAM MB | GPU MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cam01 | 206 | 10 | 27 | 206 | 0 | 0 | 0/0 | 0/7 | 2.95 | 0.342 | 61.9 | 2858 | 1158 |
| cam02 | 163 | 6 | 33 | 160 | 0 | 0 | 0/0 | 0/3 | 3.4 | 0.247 | 57.6 | 2086 | 1190 |
| cam05 | 500 | 21 | 174 | 756 | 0 | 0 | 0/0 | 0/10 | 2.95 | 0.195 | 69.0 | 3679 | 1198 |
| cam06 | 105 | 24 | 656 | 1824 | 0 | 0 | 4/0 | 0/16 | 5.11 | 0.362 | 124.5 | 1681 | 1210 |
| cam07 | 81 | 25 | 546 | 1756 | 1 | 1 | 7/1 | 1/20 | 3.67 | 0.259 | 111.2 | 1690 | 1234 |
| cam08 | 354 | 9 | 215 | 674 | 0 | 0 | 0/0 | 0/4 | 4.25 | 0.349 | 78.3 | 1555 | 1234 |
| cam09 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0/0 | 2.14 | 0.428 | 46.7 | 2118 | 1239 |
| cam10 | 166 | 2 | 19 | 70 | 0 | 0 | 0/0 | 0/0 | 5.81 | 0.787 | 64.3 | 1680 | 1241 |
| cam11 | 165 | 3 | 106 | 402 | 0 | 0 | 0/0 | 0/1 | 2.95 | 0.327 | 92.5 | 1702 | 1241 |
| cam12 | 4 | 3 | 35 | 171 | 0 | 0 | 0/0 | 0/3 | 3.56 | 0.356 | 72.1 | 1636 | 1241 |
| **all** | 1744 | 103 | 1811 | 6019 | 1 | 1 | 11/1 | 1/64 | | | | | |

## Missed and false plates

- **cam06**: missed -; false GJ03JR1036, GJ03MH7800, GJ11S7924, GJ23H1546
- **cam07**: missed -; false AR01R7050, GJ27TC1876, GJ32AG4949, GJ32AG5088, GJ32B9799, GJ32K0451, GJ32K9870

## Tracks with plate candidates by best-candidate width band

| camera | EXTREMELY_TINY | VERY_SMALL | SMALL | MEDIUM | LARGE |
|---|---:|---:|---:|---:|---:|
| cam01 | 0 | 0 | 2 | 5 | 3 |
| cam02 | 0 | 1 | 2 | 3 | 0 |
| cam05 | 0 | 3 | 5 | 12 | 1 |
| cam06 | 0 | 12 | 6 | 6 | 0 |
| cam07 | 2 | 6 | 16 | 1 | 0 |
| cam08 | 0 | 1 | 6 | 1 | 1 |
| cam09 | 0 | 0 | 0 | 0 | 0 |
| cam10 | 0 | 0 | 2 | 0 | 0 |
| cam11 | 0 | 0 | 1 | 1 | 1 |
| cam12 | 0 | 1 | 2 | 0 | 0 |
