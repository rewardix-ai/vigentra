# From Pixels to Information

A cinematic, interactive keynote about Vigentra's number-plate reading, built from the project's
real footage, real model output and measured results. 37 scenes, 35 acts, about 25 minutes.
Storyboard: [STORYBOARD.md](STORYBOARD.md).

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

Interactive pieces (CCTV walls, sliders, tabs, the pipeline, the journey) take clicks without
advancing the slide. For rehearsal, `index.html#12.3` opens scene 12 at its 4th beat.

## Media

`assets/` is not committed: it holds government CCTV frames and real registration numbers.
Rebuild it on a machine with the footage:

```bash
/Users/uchit/Downloads/ANPR/.venv/bin/python deliverables/anpr-keynote/tools/build_assets.py
/Users/uchit/Downloads/ANPR/.venv/bin/python deliverables/anpr-keynote/tools/track_evidence.py
```

`build_assets.py` cuts the real clips (H.264, muted), copies the evidence crops, sheets and
screenshots, and picks one failure crop per cause by its own measurements. `track_evidence.py`
runs the deployed models (YOLO11s + ByteTrack, the YOLO11n plate detector) on CAM06 frames
1236–1330 to get the real boxes, track and per-frame plate crops. Both write
`js/data/evidence.js`.

To swap footage, change a `src` in `js/data/assets.js`. A slot without a file shows its label on
stage, for example `[REAL CAM 06 FOOTAGE REQUIRED]`, rather than an invented stand-in.

## Structure

| Path | Holds |
|---|---|
| `js/core/` | engine (scenes, steps, navigation, notes), animation helpers bound to a scene, media slots |
| `js/components/` | plate, detection boxes, sliders and tabs, charts and the loop, pipeline and CCTV wall |
| `js/scenes/` | the 37 scenes in story order, one file per part |
| `js/data/` | `project.js` (every number, with its source), `assets.js`, `evidence.js` (generated), `storyboard.js` (notes) |
| `css/` | `theme.css` design tokens, `stage.css`, `components.css`, `scenes.css` |
| `tools/` | asset builders, the storyboard renderer, the local server |

## What is real, and what is a demonstration

Every number is in `js/data/project.js` with the file it came from. Footage, frames, crops,
boxes, readings and failure examples are the project's own. Four things are demonstrations, and
each says so on screen: the plate shrunk to each size band (scene 12), the recall/precision dots
(scene 24), the degradation slider (scene 30) and the `6J…` → `GJ…` grammar example (scene 19).
The size-band evaluation is of the research detectors; the deployed plate detector has not yet
been evaluated by size, and the scene states that.
