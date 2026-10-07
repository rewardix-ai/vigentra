# Vigentra · From Pixels to Information: storyboard

Generated from `js/data/storyboard.js` by `tools/storyboard.py`; edit the JS, not this file. Every number is in `js/data/project.js` with its source; every picture is real project footage or output (`tools/build_assets.py`, `tools/track_evidence.py`).

## 1. Vigentra control room
*Vigentra*

- **Story purpose.** Open on the name, as a control room powering up.
- **Visual.** Four real feeds glow faintly; the Vigentra mark, wordmark and tagline; a boot log types out what the system is made of: cameras, the vehicle detector, the tracker, the plate detector, the readers, the plate rules.
- **Speaker narration.** This is Vigentra. Vigilance, intelligence, safer roads. [pause] It reads number plates from the CCTV that is already on our roads. Let me show you how.
- **Audience should understand.** Who we are, and that this is a working system.
- **Transition.** 11 s, then the control room's wall. Moves on by itself (press A to hold).
- **Real asset.** brand/*.png; assets/video/cam06_1080p, delhi_raw, reel_cam04, reel_cam15

## 2. Nobody can watch them all
*The problem*

- **Story purpose.** The problem: too much video for people.
- **Visual.** CAM06 full screen; the view pulls back to a wall of sixteen real feeds. Banners: 30 cameras. Nobody can watch them all. Vigentra turns this video into plates you can search.
- **Speaker narration.** These are recordings from our grid cameras. Thirty on the grid alone. [pause] Nobody can watch them all. [pause] So Vigentra turns this video into plates you can search.
- **Audience should understand.** The value is turning video into searchable records.
- **Transition.** 19.5 s: wall 7 · count 4.5 · line 3.5 · line 4.5. Moves on by itself (press A to hold).
- **Real asset.** assets/video/wall_cam*.mp4 (grid cam01-cam16), cam06_noon, delhi_raw

## 3. This is the real input
*The problem*

- **Story purpose.** The real conditions.
- **Visual.** Six monitors power on one by one: night, headlights, far away, low resolution, a moving camera, a crowded junction. Then: This is the real input.
- **Speaker narration.** And this is what the cameras really give us. Night. Headlights. Distance. Low resolution. A moving camera. Crowds. [pause] This is the real input.
- **Audience should understand.** Real footage is hard, and every camera is different.
- **Transition.** 12 s: monitors 8 · line 4. Moves on by itself (press A to hold).
- **Real asset.** assets/video/reel_cam07, reel_cam15, reel_cam01, cam06_noon, delhi_raw, reel_cam04

## 4. A camera sees a vehicle
*One vehicle*

- **Story purpose.** One car, and how small its plate is.
- **Visual.** CAM06 in a viewfinder: a car comes down Madhuram Bypass Road, the frame freezes, the view moves onto the plate. A readout: 122 × 32 pixels, under 0.2% of the picture.
- **Speaker narration.** This is CAM06, a real camera in Gujarat. A camera sees a vehicle. [beat] Can it tell us which one? [beat] Vigentra can, from a plate this small: about a hundred and twenty pixels wide, a fifth of one percent of the picture.
- **Audience should understand.** Reading a plate means working with very few pixels.
- **Transition.** 15 s: clip 5.5 · zoom 4.5 · readout 5. Moves on by itself (press A to hold).
- **Real asset.** assets/video/cam06_1080p.mp4, assets/img/cam06_best_frame.jpg, plate_1299.png

## 5. The signal chain
*How Vigentra reads*

- **Story purpose.** The whole method as one live schematic.
- **Visual.** Six numbered stages, each showing the real data at that step, two YES/NO decisions, and outcomes with real refused plates: Not read, No plate saved. A square follows one car; the event log says what each stage does as it arrives. Red squares take the NO lines.
- **Speaker narration.** Here is the signal chain, on one real car. Camera; track the car; find its plate; combine its best frames. [beat] Enough pixels? If not, it is not read. Read the plate. Do the readings agree? If not, no plate is saved. Only then a confirmed plate: plate, time, camera. Never video.
- **Audience should understand.** The steps, and that Vigentra refuses rather than guesses.
- **Transition.** 23 s, one full trip of the square. Moves on by itself (press A to hold).
- **Real asset.** assets/img/plate_1299.png, journey_fused.png, fail_tiny_plate.png, fail_motion_blur.png

## 6. Find it, follow it, find its plate
*How Vigentra reads*

- **Story purpose.** Detection and tracking, live, on the real output.
- **Visual.** CAM06 on a monitor at half speed with the deployed models' boxes: each says what kind of vehicle it is (car, motorcycle), then its ID with a trail, then the plate box. Beside it an event log prints, frame by frame, what the models saw.
- **Speaker narration.** First, find every vehicle, and what kind it is. [beat] Then follow each one, so one car stays one car: each gets an ID. [beat] Then, inside that car, find the plate. The log on the right is the system telling you what it saw, frame by frame.
- **Audience should understand.** Vigentra finds the vehicle, follows it, then looks for its plate.
- **Transition.** 29.5 s: vehicles 12.5 · tracking 8.5 · plate 8.5. Moves on by itself (press A to hold).
- **Real asset.** assets/video/cam06_1080p.mp4; K.TRACK (tools/track_evidence.py, deployed settings)

## 7. One car, many frames
*How Vigentra reads*

- **Story purpose.** Many frames beat one.
- **Visual.** An evidence buffer: the same plate in nine real frames, growing from 67 to 138 pixels; then a slider between one frame and twelve frames combined and cleaned.
- **Speaker narration.** Because we follow the car, we see its plate many times, bigger as it comes closer. [beat] Vigentra keeps the best frames and combines them. Drag it: one frame, then twelve. Nothing is invented.
- **Audience should understand.** Following the car gives more evidence, and better evidence.
- **Transition.** You move on.
- **Real asset.** assets/img/plate_12xx.png (deployed detector crops), journey_best.png, journey_enhanced.png

## 8. Is there enough to read?
*How Vigentra reads*

- **Story purpose.** Refusing is part of the job.
- **Visual.** Three samples: the CAM06 plate stamped READ; a 10-pixel Delhi crop TOO SMALL; a blurred Delhi plate NOT SAVED, because the readings disagreed.
- **Speaker narration.** Before we read, we ask: is there enough here? This one, yes. This one is ten pixels wide: we don't even try. This one is a plate, but blurred: the readings disagree, so no plate number is saved. [pause] If the pixels aren't there, Vigentra doesn't guess.
- **Audience should understand.** A refusal is better than a wrong plate.
- **Transition.** You move on.
- **Real asset.** assets/img/journey_best.png, fail_tiny_plate.png, fail_blurred_plate.png

## 9. From pixels to characters
*How Vigentra reads*

- **Story purpose.** OCR, and the plate format.
- **Visual.** The combined crop; under it each character flickers before it settles, with a bar for how sure Vigentra is; then the plate splits into State, RTO, Series, Number.
- **Speaker narration.** Now we read it, character by character, each with a confidence. [beat] And Indian plates follow a pattern: state, RTO, series, number. A 6 where a letter must be is read as G.
- **Audience should understand.** Reading uses the image and the rules of Indian plates.
- **Transition.** You move on.
- **Real asset.** assets/img/journey_enhanced.png; per-character confidence from the evidence pack

## 10. Many readings, one answer
*How Vigentra reads*

- **Story purpose.** Many readings, one confirmed answer, one saved record.
- **Visual.** A tally of the 136 real readings: GJ23H1546 leads with 73; the others fade; CONFIRMED. Then the saved record types out: plate, camera, status, readings, frames, format, sent as text only.
- **Speaker narration.** Across all the frames, our readers produced 136 readings of this plate. 73 say GJ 23 H 1546. [beat] Confirmed. [beat] And this is all that is saved: the plate, the camera, the evidence. Text only. No image, no video.
- **Audience should understand.** Vigentra confirms by agreement and stores only text.
- **Transition.** You move on.
- **Real asset.** K.EVIDENCE.top_readings (evidence/confirmed/cam06_s0_t132.json)

## 11. CAM06 and Delhi
*Field test*

- **Story purpose.** The test on real footage.
- **Visual.** Two monitors with Vigentra's own output: CAM06 from 0:29 of its result video, where the readings start, then the Delhi street video; under each the scores count up.
- **Speaker narration.** We counted every plate a person can read in these clips, frame by frame. CAM06: 16. Vigentra read 15. Wrong: zero. [beat] Delhi, hand-held, busy: 20 legible, 13 read. Wrong: zero.
- **Audience should understand.** On hard real footage it reads most plates and never invents one.
- **Transition.** 52 s: CAM06 24 · Delhi 28. Moves on by itself (press A to hold).
- **Real asset.** assets/video/cam06_vigentra.mp4, delhi_vigentra.mp4

## 12. What Vigentra achieved
*Field test*

- **Story purpose.** The headline numbers.
- **Visual.** A ring fills to 36 of 48; then 0 wrong, 12 missed with nothing saved for them, and live speed at 720p on one laptop.
- **Speaker narration.** Across our test footage, 48 plates a person could read. Vigentra read 36. [beat] Wrong plates: zero. [beat] The 12 it missed, it saved nothing for, rather than a wrong plate. [beat] And it keeps pace with a live 720p camera on one laptop.
- **Audience should understand.** Accurate, honest about what it misses, and fast enough.
- **Transition.** You move on.
- **Real asset.** final2 benchmark report; docs/anpr-optimisation.md

## 13. What still doesn't work
*Known limits*

- **Story purpose.** The honest limits.
- **Visual.** A signal-quality control degrades the real crop until nothing is left (a demonstration). The boundary: AI cannot recover what the camera never captured. Then three known limits.
- **Speaker narration.** Watch what happens as the camera captures less. [beat] AI cannot recover what was never captured. [beat] So, what still doesn't work: night and glare; very small, distant plates; and running every feed live needs GPU servers.
- **Audience should understand.** The team knows its limits and the way forward.
- **Transition.** You move on.
- **Real asset.** assets/img/journey_best.png (degraded live in the browser); docs/anpr-cam06-day.md; config/thresholds.yaml

## 14. How the pieces connect
*The system*

- **Story purpose.** How the system fits together.
- **Visual.** A schematic: Cameras (four live feeds) → Vigentra reader → Vigentra central → Console. On the watchlist? YES → Alert; NO → kept for search. A dashed lane: live video, only with the owning unit's permission. Squares travel each route; the event log explains each box.
- **Speaker narration.** Video comes in; text goes out. The reader sends only the confirmed plate, time and camera to central, which checks it against the watchlist: yes means an alert, no means it is kept for search. [beat] Live video is a separate lane, shared only with the owning unit's permission.
- **Audience should understand.** Vigentra moves plates as text, and video only with permission.
- **Transition.** 27 s: schematic 22 · line 5. Moves on by itself (press A to hold).
- **Real asset.** services/edge-worker/app/worker.py (the plate record); central-api access model

## 15. The Vigentra console
*The system*

- **Story purpose.** What an operator uses.
- **Visual.** The Vigentra console on a monitor, playing the useful parts of the recording: live detection on grid CAM06, the detection search, tracing a vehicle, the plate report, the audit log. The matching item on the right lights up as each plays.
- **Speaker narration.** This is what an operator uses. Live detection on a grid feed. Search every detection. Trace one vehicle across cameras. A plate report. And an audit log of what everyone does.
- **Audience should understand.** It is a working system, not just a model.
- **Transition.** 83 s (1:15-1:24 and 1:28-2:42 of the recording; the camera wall and the closing card, which show the London feeds, are skipped). Moves on by itself (press A to hold).
- **Real asset.** assets/video/app_demo.mp4 (deliverables/Vigentra_Demo_Short.mp4)

## 16. From pixels to information
*Vigentra*

- **Story purpose.** Close on the idea and the name.
- **Visual.** CAM06 plays with Vigentra's real boxes; From pixels to information. Then the Vigentra mark, wordmark and tagline, and thank you.
- **Speaker narration.** A camera sees a vehicle. Vigentra tells you which one, and only when it is sure. From pixels, to information. [pause] Thank you.
- **Audience should understand.** Vigentra turns CCTV into trustworthy information.
- **Transition.** End. Hold on the logo for questions.
- **Real asset.** assets/video/cam06_1080p.mp4 with K.TRACK.replay; brand/*.png
