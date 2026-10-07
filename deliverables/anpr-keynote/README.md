# Vigentra · From Pixels to Information

A keynote about how Vigentra reads number plates, built as a Vigentra control room: monitors of
real footage, the deployed models' real boxes, event logs that narrate what the system does, and
live schematics. Everything shown is the project's own footage, model output and measured
results. 21 scenes, about 16 minutes. Storyboard and speaker notes: [STORYBOARD.md](STORYBOARD.md).
Every technical choice, why it was made and what it beats, for the questions: [TECHNICAL.md](TECHNICAL.md), also as a
standalone styled page, `TECHNICAL.html` (rebuild it with `tools/technical_html.py` after editing the Markdown).

A second deck, the **story edition** (`lens.html`), tells the same story in an editorial,
cinematic look after datalense.app: dark chapters of real footage under a night-indigo tint with
big white headlines and lavender time stamps, and light paper sections of rounded cards. It
shares the engine, data, parts and both schematics; only its scenes (`js/lens/`) and stylesheet
(`css/lens.css`) are its own.

## Run it

Offline, straight from disk: open `index.html` (control room) or `lens.html` (story edition) in Chrome or Safari. For the presenter window
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
| A | auto-advance on / off (on by default; remembered in this browser) |

Interactive pieces take clicks without advancing the slide: the camera wall (click a feed to
enlarge it), the two schematics (click a stage for its line in the event log), the one-frame /
combined slider and the signal-quality control. For rehearsal, `index.html#6.2` opens scene 6 at
its 3rd beat.

## Videos: time limits and auto-advance

Each scene that plays video moves on by itself when its video has done its job; a thin line
above the progress bar fills while it counts down. Press → to move on sooner, or **A** to turn
auto-advance off (for questions). Scenes without video wait for you.

| Scene | Video | Time on screen, then moves on |
|---|---|---|
| 1 Boot | four feeds behind the logo | 11 s |
| 2 Camera wall | 16 feeds, 20-s clips | 19.5 s (wall 7 · count 4.5 · line 3.5 · line 4.5) |
| 3 Real input | six monitors | 12 s (monitors 8 · line 4) |
| 4 One vehicle | CAM06 clip, 4.5 s | 15 s (clip 5.5 · zoom 4.5 · readout 5) |
| 5 Signal chain | CAM06 clip in the first stage | 23 s (one full trip of the square) |
| 6 Find, follow, find its plate | CAM06 replay at half speed | 29.5 s (vehicles 12.5 · tracking 8.5 · plate 8.5) |
| 11 Field test | Vigentra's result videos | 52 s (CAM06 from 0:29, 24 · Delhi 0:00–0:28, 28) |
| 14 The system | four feeds in the first stage | 27 s (schematic 22 · line 5) |
| 15 The console | screen recording | 82 s (1:16–1:24 and 1:28–2:42; the camera wall and closing card, which show the London feeds, are skipped) |
| 21 Close | CAM06 replay behind the logo | stays: the end |

Story edition (`lens.html`):

| Scene | Video | Time on screen, then moves on |
|---|---|---|
| 1 Hero | CAM06, the car arriving | 10 s |
| 2 A day on the grid | CAM06 11:46, CAM06 18:00, grid cam15 21:00 | 21 s (7 · 7 · 7) |
| 3 Nobody can watch them all | 16 feeds | 12 s (wall 7 · line 5) |
| 4 How it works | CAM06 clip in the first stage | 23 s |
| 5 Find, follow, find its plate | CAM06 replay at half speed | 29.5 s |
| 10 Proof | Vigentra's result videos | 52 s (CAM06 24 · Delhi 28) |
| 13 The system | four feeds in the first stage | 27 s |
| 14 The console | screen recording | 82 s |
| 19 Close | CAM06 replay behind the logo | stays: the end |

## The story

| Part | Scenes |
|---|---|
| Vigentra | the control room boots: logo, then a log of what the system is made of |
| The problem | the camera wall · six monitors of real conditions |
| One vehicle | CAM06: a car, its plate, 122 × 32 pixels |
| How Vigentra reads | the signal chain (a live schematic with an event log) · the deployed models' replay with a frame-by-frame event log · the evidence buffer · the quality check · reading · the vote and the saved record |
| Field test | CAM06 and Delhi · 36 of 48 legible plates, 0 wrong |
| Known limits | the camera decides (a demonstration) · three limits |
| The system | a live schematic of how the pieces connect · the Vigentra console |
| Under the hood | what it is built with · why this, not that · what we tested and dropped |
| Why Vigentra | six design choices, each with its evidence |
| Next | the deployment plan, each item starting from a limit the project measured |

## Media

`assets/` is not committed: it holds government CCTV frames and real registration numbers.
Rebuild it on a machine with the footage:

```bash
/Users/uchit/Downloads/ANPR/.venv/bin/python deliverables/anpr-keynote/tools/build_assets.py
/Users/uchit/Downloads/ANPR/.venv/bin/python deliverables/anpr-keynote/tools/track_evidence.py
```

`build_assets.py` cuts the real clips (H.264, muted): grid cam01–cam16, CAM06 and the Delhi
street clip; no London feeds. It also copies the evidence crops and picks the refused crops by
their own measurements (`--images-only` skips the video cuts). `track_evidence.py` runs the
deployed models with their deployed settings (YOLO11s with class-agnostic NMS + ByteTrack, the
YOLO11n plate detector) on CAM06 frames 1190–1335 and records every box with its frame's own
timestamp; the replay draws them over the playing video and the event log reads them. Both write
`js/data/evidence.js`. `brand/` holds the Vigentra mark, wordmark and tagline cut from the logo.

To swap footage, change a `src` in `js/data/assets.js`. A slot without a file shows its label on
stage, for example `[REAL CAM 06 FOOTAGE REQUIRED]`, rather than an invented stand-in.

## Structure

| Path | Holds |
|---|---|
| `js/core/` | engine (scenes, steps, navigation, notes, auto-advance, the control-room bars), animation helpers bound to a scene, media slots |
| `js/components/` | plain-character readings, boxes and the live replay, sliders, the camera wall, the live schematic |
| `js/scenes/` | the control-room deck's 21 scenes, one file per part; `helpers.js` (panels, monitors, banners, event logs) and `charts.js` (both schematics) are shared |
| `js/lens/` | the story edition's 19 scenes |
| `js/data/` | `project.js` (every number, with its source), `assets.js`, `evidence.js` (generated), `storyboard.js` (notes) |
| `css/` | `theme.css` design tokens, `stage.css` (stage, bars, HUD), `components.css`, `scenes.css`; `lens.css` for the story edition |
| `tools/` | asset builders, the storyboard and TECHNICAL.html renderers, the local server |

## What is real, and what is a demonstration

Every number is in `js/data/project.js` with the file it came from. Footage, frames, crops,
boxes, event-log lines, readings and refusals are the project's own. Three things are
illustrations: the signal-quality control (a demonstration on the real crop), the squares in the
schematics (they show the path a plate takes, not a recorded event) and the flicker of characters
before each settles in the reading scene (the characters it settles on are the real reading). The
clock in the top bar is the presenting computer's own time.

Vehicle labels in the replay give one type per vehicle: the class of its most confident detection
over its track. The model's class flips frame to frame (it has no Indian vehicle types); for the
evidence car, a small hatchback, its most confident detection says car (0.91), so it is labelled car.

Claims checked against the reports and left out: the "CAM06 night" clip is stamped 18:00 on a
June evening, so it is not shown as night (the night footage is grid cam07 and cam15 at 21:00);
the sandbox and statewide camera counts, the research detectors' size evaluation and the training
runs are not in the deck.

Design rules from review: no glow effects anywhere, and no drawn (artificial) number plate:
readings are plain characters beside the real crop they came from.
