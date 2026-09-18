# Why plates were missed — run `baseline`

Each missed plate is shown with the nearest reading the run produced on that clip, so the
miss can be attributed. `-` in the nearest column means nothing on the clip read within 3
characters of it.

## delhi_1080p

- readable plates: 20, confirmed 7
- tracks: 254, with a plate candidate 144

| missed plate | nearest reading | edits | status | reason | plate size | crops |
|---|---|---:|---|---|---|---:|
| DL11SD3385 | - | | | no reading within 3 characters | | |
| DL1CW0942 | DL1CW0527 | 3 | CANDIDATE | second_reader_only | 65x19 | 386 |
| DL1LAB9684 | DL11AR9684 | 2 | CANDIDATE | second_reader_only | 47x24 | 8 |
| DL1LT1087 | DL14T9087 | 2 | CANDIDATE | low_vote_share | 54x19 | 256 |
| DL1RTA5056 | - | | | no reading within 3 characters | | |
| DL5SAR5109 | DL5ARQ3109 | 3 | CANDIDATE | too_few_agreeing_frames | 101x55 | 68 |
| DL5SBW7737 | DL5SBW7737 | 0 | CANDIDATE | low_vote_share | 78x28 | 81 |
| DL6SAS6524 | DL6SAS6522 | 1 | CANDIDATE | second_reader_only | 93x35 | 37 |
| DL6SBE6415 | DL6SRE6415 | 1 | CANDIDATE | second_reader_only | 82x70 | 58 |
| DL8CAP4175 | - | | | no reading within 3 characters | | |
| UP13AY3893 | UP12AY3193 | 2 | CANDIDATE | second_reader_only | 90x39 | 39 |
| UP14EC6398 | MP11ET6398 | 3 | CANDIDATE | readers_disagree | 78x25 | 32 |
| UP78FH9291 | UP78FH9291 | 0 | CANDIDATE | low_vote_share | 51x26 | 69 |

**Valid-format readings that match no plate (39):** AP02G8177, DL01T7607, DL037777, DL05S843, DL07T1213, DL091861, DL10S3479, DL14T9087, DL1CW0123, DL1CW0527, DL4MH1112, DL7CR3766, GJ027417, GJ02B1999, GJ02C0297, GJ038675, GJ03M179, GJ043119, GJ07A0000, GJ07MH47…

**Why the other tracks settled nothing:** no_plate_detected 110, readers_disagree 61, width_below_gate 19, no_glyph_evidence 10, too_few_agreeing_frames 9, static_scene_text 9, second_reader_only 7, low_confidence 6

## cam07

- readable plates: 1, confirmed 0
- tracks: 2, with a plate candidate 1

| missed plate | nearest reading | edits | status | reason | plate size | crops |
|---|---|---:|---|---|---|---:|
| GJ32AG0416 | - | | | no reading within 3 characters | | |

**Valid-format readings that match no plate (1):** GJ32L7043

**Why the other tracks settled nothing:** no_plate_detected 1, too_few_agreeing_frames 1

