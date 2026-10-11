# ANPR benchmark `pdg_v2`

- **correct** = readable ground-truth plates the engine confirmed (exact string);
- **false** = confirmed or emitted strings that are not a readable ground-truth plate on that clip and do not
  fit any partly legible one (those are counted as unverifiable);
- **read** = any valid-format reading shown (confirmed or not).

| camera | tracks | with plate cand. | plate dets | OCR images | GT readable | correct confirmed | false confirmed/emitted | valid reads correct/false | fps | clip s per s | CPU % | RAM MB | GPU MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cam01 | 206 | 41 | 396 | 1934 | 0 | 0 | 1/0 | 0/20 | 2.14 | 0.248 | 87.7 | 2234 | 1153 |
| cam02 | 163 | 25 | 231 | 858 | 0 | 0 | 0/0 | 0/9 | 2.81 | 0.205 | 60.2 | 2254 | 1190 |
| cam05 | 500 | 52 | 901 | 3253 | 0 | 0 | 0/0 | 0/24 | 0.8 | 0.053 | 58.0 | 2899 | 1198 |
| cam06 | 105 | 25 | 700 | 1992 | 0 | 0 | 4/0 | 0/17 | 4.12 | 0.293 | 113.5 | 2072 | 1210 |
| cam07 | 81 | 26 | 547 | 1659 | 1 | 1 | 7/1 | 1/19 | 4.76 | 0.336 | 126.8 | 1804 | 1234 |
| cam08 | 354 | 21 | 1112 | 1481 | 0 | 0 | 0/0 | 0/9 | 3.69 | 0.303 | 120.6 | 2396 | 1234 |
| cam09 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0/0 | 2.77 | 0.554 | 54.5 | 2247 | 1233 |
| cam10 | 166 | 7 | 145 | 359 | 0 | 0 | 0/0 | 0/2 | 4.48 | 0.608 | 116.6 | 1598 | 1234 |
| cam11 | 165 | 4 | 152 | 450 | 0 | 0 | 0/0 | 0/3 | 1.99 | 0.22 | 75.9 | 1792 | 1234 |
| cam12 | 4 | 4 | 50 | 249 | 0 | 0 | 0/0 | 0/2 | 1.56 | 0.156 | 53.3 | 1686 | 1235 |
| **all** | 1744 | 205 | 4234 | 12235 | 1 | 1 | 12/1 | 1/105 | | | | | |

## Missed and false plates

- **cam01**: missed -; false GJ04EE8520
- **cam06**: missed -; false GJ03JR1036, GJ03MH7800, GJ11S7924, GJ23H1546
- **cam07**: missed -; false AR01R7050, GJ27TC1876, GJ32AG4949, GJ32AG5088, GJ32B9799, GJ32K0451, GJ32K9870

## Tracks with plate candidates by best-candidate width band

| camera | EXTREMELY_TINY | VERY_SMALL | SMALL | MEDIUM | LARGE |
|---|---:|---:|---:|---:|---:|
| cam01 | 1 | 12 | 8 | 13 | 7 |
| cam02 | 1 | 7 | 10 | 6 | 1 |
| cam05 | 1 | 17 | 19 | 12 | 3 |
| cam06 | 1 | 15 | 3 | 6 | 0 |
| cam07 | 3 | 7 | 14 | 2 | 0 |
| cam08 | 1 | 7 | 6 | 5 | 2 |
| cam09 | 0 | 0 | 0 | 0 | 0 |
| cam10 | 0 | 1 | 5 | 1 | 0 |
| cam11 | 0 | 1 | 1 | 1 | 1 |
| cam12 | 0 | 3 | 1 | 0 | 0 |
