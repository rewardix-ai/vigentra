# Camera profiles

Image statistics are sampled every 0.5 s.
Traffic comes from the independent tracker pass (`tools/anpr_gt_sheets.py`).
Plate sizes are the engine's candidates in the `baseline` run.
The difficulty class is a diagnostic label only.

| camera | res | fps | kbps | luma p10/50/90 | lighting | sharp | block | tracks/min | veh/frame | veh w p50 | motion (appr/rec/steady) | side | occl | plate w p25/50/90 | >=40px | >=60px | skew | class |
|---|---|---:|---:|---|---|---:|---:|---:|---:|---:|---|---|---:|---|---:|---:|---:|---|
| cam06_1080p | 1920x1080 | 23.21 | 1854 | 89.8/92.8/94.9 | day | 593.7 | 1.051 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| delhi_1080p | 1920x1080 | 29.74 | 25660 | 104.4/107.3/110.1 | day | 486.3 | 1.031 | 434.3 | 11.37 | 141 | 83/25/52 | front | 0.394 | 31/64/133 | 96 | 76 | 0.0 | **GOOD** |
| cam01 | 1280x720 | 25.0 | 516 | 89.7/96.2/98.9 | day | 419.0 | 1.158 | 258.7 | 7.88 | 76 | 11/49/75 | rear | 0.185 | 20/24/69 | 5 | 4 | 1.0 | **DIFFICULT** |
| cam02 | 1280x720 | 25.0 | 1089 | 84.0/89.7/94.6 | day | 458.4 | 1.163 | 172.0 | 3.23 | 81 | 22/15/46 | both | 0.047 | 14/22/42 | 4 | 1 | 0.0 | **DIFFICULT** |
| cam04 | 1280x720 | 25.0 | 1209 | 74.3/81.5/88.0 | day | 559.2 | 1.092 | 274.7 | 11.67 | 71 | 31/55/75 | rear | 0.145 | 16/27/95 | 13 | 8 | 0.0 | **DIFFICULT** |
| cam05 | 1280x720 | 25.0 | 824 | 84.4/92.5/98.0 | day | 453.4 | 1.14 | 294.7 | 7.37 | 43 | 33/27/85 | both | 0.051 | 18/20/30 | 0 | 0 | 0.0 | **DIFFICULT** |
| cam06 | 1280x720 | 25.0 | 624 | 87.7/91.1/93.0 | day | 278.8 | 0.953 | 65.3 | 2.7 | 129 | 14/9/12 | front | 0.063 | 35/46/159 | 13 | 3 | 0.0 | **MEDIUM** |
| cam07 | 1280x720 | 25.0 | 499 | 32.0/40.8/47.7 | night/dark | 174.6 | 0.938 | 16.0 | 1.17 | 116 | 1/4/2 | rear | 0.014 | 54/54/54 | 1 | 0 | 3.8 | **DIFFICULT** |
| cam08 | 1280x720 | 25.0 | 1089 | 64.2/79.6/93.4 | dim | 249.9 | 1.022 | 178.7 | 7.25 | 71 | 18/41/48 | rear | 0.128 | 18/31/195 | 6 | 4 | 0.0 | **DIFFICULT** |
| cam09 | 1280x720 | 25.0 | 249 | 1.7/8.3/31.7 | night/dark | 568.9 | 0.957 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| cam10 | 1280x720 | 25.0 | 635 | 68.1/73.8/84.5 | dim | 327.0 | 1.069 | 208.0 | 9.11 | 63 | 15/40/72 | rear | 0.069 | 16/23/64 | 5 | 3 | 0.0 | **DIFFICULT** |
| cam11 | 1280x720 | 25.0 | 685 | 92.8/98.3/104.1 | day | 135.7 | 1.056 | 317.3 | 16.5 | 61 | 25/58/107 | rear | 0.104 | 12/17/51 | 6 | 4 | 0.0 | **VERY_DIFFICULT** |
| cam12 | 1280x720 | 25.0 | 749 | 113.3/116.7/123.7 | day | 2195.6 | 1.006 | 8.5 | 1.1 | 114 | 3/0/2 | front | 0.034 | - | - | - | - | **EXTREME** |
| cam13 | 1280x720 | 25.0 | 689 | 78.1/84.8/90.4 | day | 145.7 | 1.19 | 384.3 | 4.93 | 91 | 9/24/57 | rear | 0.134 | 16/20/41 | 7 | 1 | 0.0 | **DIFFICULT** |
| cam14 | 1280x720 | 25.0 | 505 | 92.5/95.4/102.6 | day | 139.1 | 1.22 | 194.8 | 2.74 | 65 | 7/7/35 | both | 0.14 | 11/14/28 | 1 | 0 | 0.0 | **VERY_DIFFICULT** |
| cam15 | 1280x720 | 25.0 | 399 | 84.5/90.1/97.4 | day | 97.2 | 1.196 | 145.7 | 4.74 | 102 | 6/13/51 | rear | 0.315 | 15/19/34 | 0 | 0 | 0.0 | **VERY_DIFFICULT** |
| cam16 | 1280x720 | 25.0 | 671 | 88.4/93.8/102.3 | day | 133.5 | 1.166 | 433.0 | 8.49 | 41 | 8/40/63 | rear | 0.198 | 14/20/64 | 3 | 2 | 0.0 | **DIFFICULT** |
| tfl_01 | 352x288 | 25.0 | 184 | 94.0/98.8/100.6 | day | 1266.0 | 1.058 | - | - | - | 0/0/0 | - | - | 14/14/14 | 0 | 0 | 0.0 | **VERY_DIFFICULT** |
| tfl_02 | 352x288 | 25.0 | 102 | 92.7/96.4/98.0 | day | 1121.4 | 1.198 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_03 | 352x288 | 25.0 | 85 | 91.5/93.0/93.6 | day | 1195.4 | 0.992 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_04 | 352x288 | 25.0 | 144 | 107.7/109.1/109.5 | day | 1030.6 | 1.047 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_05 | 352x288 | 25.0 | 53 | 97.0/97.9/98.1 | day | 860.5 | 1.041 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_06 | 352x288 | 25.0 | 41 | 89.0/89.8/90.2 | day | 810.6 | 1.145 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_07 | 352x288 | 25.0 | 71 | 107.0/107.5/108.7 | day | 763.2 | 1.045 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_08 | 352x288 | 25.0 | 70 | 100.2/100.4/100.7 | day | 1072.2 | 1.019 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_09 | 352x288 | 25.0 | 137 | 103.8/105.1/106.8 | day | 1085.2 | 1.053 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_10 | 352x288 | 25.0 | 90 | 80.6/80.9/81.2 | day | 1266.6 | 1.075 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_11 | 352x288 | 25.0 | 129 | 114.7/115.2/116.0 | day | 1007.7 | 1.042 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_12 | 352x288 | 25.0 | 88 | 99.9/100.6/101.1 | day | 994.7 | 1.081 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_13 | 352x288 | 25.0 | 118 | 83.3/84.1/89.9 | day | 1055.2 | 1.018 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_14 | 352x288 | 25.0 | 93 | 91.8/92.3/93.1 | day | 936.9 | 1.022 | - | - | - | 0/0/0 | - | - | 11/11/11 | 0 | 0 | 0.0 | **VERY_DIFFICULT** |
| tfl_15 | 352x288 | 25.0 | 67 | 102.4/104.5/105.4 | day | 1032.2 | 1.073 | - | - | - | 0/0/0 | - | - | 9/9/9 | 0 | 0 | 0.0 | **VERY_DIFFICULT** |
| tfl_16 | 352x288 | 25.0 | 55 | 102.0/102.7/103.6 | day | 790.3 | 1.073 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_17 | 352x288 | 25.0 | 58 | 102.9/103.1/103.6 | day | 876.6 | 1.106 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_18 | 352x288 | 25.0 | 49 | 105.6/105.8/105.9 | day | 954.8 | 1.051 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |
| tfl_19 | 352x288 | 25.0 | 113 | 106.8/108.2/109.4 | day | 1392.9 | 1.122 | - | - | - | 0/0/0 | - | - | - | - | - | - | **EXTREME** |

## Why each class

- **cam06_1080p** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **delhi_1080p** (GOOD): no penalty
- **cam01** (DIFFICULT): median widest plate 24px < 40
- **cam02** (DIFFICULT): median widest plate 22px < 40
- **cam04** (DIFFICULT): median widest plate 27px < 40
- **cam05** (DIFFICULT): median widest plate 20px < 40
- **cam06** (MEDIUM): median widest plate 46px < 60
- **cam07** (DIFFICULT): median widest plate 54px < 60; dark (luma 40.8)
- **cam08** (DIFFICULT): median widest plate 31px < 40
- **cam09** (EXTREME): median widest plate 0px < 20; very dark (luma 8.3); the engine banked no plate candidate
- **cam10** (DIFFICULT): median widest plate 23px < 40
- **cam11** (VERY_DIFFICULT): median widest plate 17px < 20
- **cam12** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **cam13** (DIFFICULT): median widest plate 20px < 40
- **cam14** (VERY_DIFFICULT): median widest plate 14px < 20
- **cam15** (VERY_DIFFICULT): median widest plate 19px < 20
- **cam16** (DIFFICULT): median widest plate 20px < 40
- **tfl_01** (VERY_DIFFICULT): median widest plate 14px < 20
- **tfl_02** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_03** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_04** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_05** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_06** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_07** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_08** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_09** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_10** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_11** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_12** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_13** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_14** (VERY_DIFFICULT): median widest plate 11px < 20
- **tfl_15** (VERY_DIFFICULT): median widest plate 9px < 20
- **tfl_16** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_17** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_18** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
- **tfl_19** (EXTREME): median widest plate 0px < 20; the engine banked no plate candidate
