# Vigentra · From Pixels to Information

A cinematic, interactive keynote about how Vigentra reads number plates, built from the
project's real footage, real model output and measured results. 18 scenes, about 15 minutes.
Storyboard and speaker notes: [STORYBOARD.md](STORYBOARD.md).

## Run it

Offline, straight from disk: open `index.html` in Chrome or Safari. For the presenter window
(two screens), serve it instead:

```bash
python3 deliverables/anpr-keynote/tools/serve.py
```

then open http://127.0.0.1:8765 and press **S** for the presenter view. Press **F** for fullscreen.

## Controls

| Key | Does |
|---|---|
| → · Space · PageDown · click | next beat (a press during an animation finishes it first) |
| ← · PageUp | previous beat, rebuilt instantly |
| Home · End | first · last scene |
| F | fullscreen |
| N | speaker notes on stage |
| S | presenter window (when served) |
| G | all scenes; click one to jump |
| T | technical layer: models, thresholds, sources |
| B | blackout |

Interactive pieces take clicks without advancing the slide: the camera wall (click a feed to
enlarge it), the two live flowcharts (click a box for its explanation), the one-frame / combined
slider and the degradation slider. For rehearsal, `index.html#7.2` opens scene 7 at its 3rd beat.

## The story

| Part | Scenes |
|---|---|
| Vigentra | the logo over real feeds |
| The question | one CAM06 car; Vigentra reads its plate |
| The problem | the grid's camera wall · how small a plate is · real night, glare, distance, movement |
| How Vigentra reads | a live flowchart of the whole method (numbered steps showing the real data at each, Yes/No decisions, real refused plates on the No outcomes, a caption that explains each step as the dot reaches it), then each step on that one car: the deployed models' boxes replayed over the playing video, its best frames, the quality check, reading, the vote |
| Proof | CAM06 and Delhi · 36 of 48 legible plates, 0 wrong · the camera decides |
| The system | a live flowchart of how the pieces connect · the Vigentra console |
| What's next | three honest limits · the close |

## Media

`assets/` is not committed: it holds government CCTV frames and real registration numbers.
Rebuild it on a machine with the footage:

```bash
/Users/uchit/Downloads/ANPR/.venv/bin/python deliverables/anpr-keynote/tools/build_assets.py
/Users/uchit/Downloads/ANPR/.venv/bin/python deliverables/anpr-keynote/tools/track_evidence.py
```

`build_assets.py` cuts the real clips (H.264, muted): grid cam01–cam16, CAM06 and the Delhi
street clip; no London feeds. It also copies the evidence crops and picks the refused crops by
their own measurements. `track_evidence.py` runs the deployed models (YOLO11s + ByteTrack, the
YOLO11n plate detector) on CAM06 frames 1190–1335 and records every box with its frame's own
timestamp, which the replay draws over the playing video. Both write `js/data/evidence.js`.
`brand/` holds the Vigentra mark, wordmark and tagline cut from the logo, and is committed.

To swap footage, change a `src` in `js/data/assets.js`. A slot without a file shows its label on
stage, for example `[REAL CAM 06 FOOTAGE REQUIRED]`, rather than an invented stand-in.

## Structure

| Path | Holds |
|---|---|
| `js/core/` | engine (scenes, steps, navigation, notes), animation helpers bound to a scene, media slots |
| `js/components/` | plain-character readings, boxes and the live replay, sliders, the CCTV wall, the live flowchart |
| `js/scenes/` | the 18 scenes in story order, one file per part |
| `js/data/` | `project.js` (every number, with its source), `assets.js`, `evidence.js` (generated), `storyboard.js` (notes) |
| `css/` | `theme.css` design tokens, `stage.css` (stage, brand badge, grain), `components.css`, `scenes.css` |
| `tools/` | asset builders, the storyboard renderer, the local server |

## What is real, and what is a demonstration

Every number is in `js/data/project.js` with the file it came from. Footage, frames, crops,
boxes, readings and refusals are the project's own. Two things are illustrations, and say so:
the degradation slider (a demonstration on the real crop) and the dots in the flowcharts
(they show the path a plate takes, not a recorded event). The vote's cloud of readings is drawn
in proportion, one chip for every two real readings.

Claims checked against the reports and left out: the "CAM06 night" clip is stamped 18:00 on a
June evening, so it is not shown as night (the night footage is grid cam07 and cam15 at 21:00);
the sandbox and statewide camera counts, the research detectors' size evaluation and the
training runs are not in the deck.

Design rules from review: no glow effects anywhere, and no drawn (artificial) number plate:
readings are plain characters beside the real crop they came from.
