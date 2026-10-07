# Vigentra · From Pixels to Information: storyboard

Generated from `js/data/storyboard.js` by `tools/storyboard.py`; edit the JS, not this file. Every number is in `js/data/project.js` with its source; every picture is real project footage or output (`tools/build_assets.py`, `tools/track_evidence.py`).

## 1. Vigentra
*Vigentra*

- **Story purpose.** Open on the name.
- **Visual.** Four real feeds (CAM06, Delhi, grid cam04 and cam15) glow behind the Vigentra mark; the wordmark wipes in, then the tagline.
- **Speaker narration.** We are Vigentra. Vigilance, intelligence, safer roads. [pause] Today: how we read number plates from the CCTV that is already on our roads.
- **Audience should understand.** Who we are and what this is about.
- **Transition.** After 9 s, one pass of the background clips: cut to one camera. Moves on by itself (press A to hold).
- **Real asset.** brand/*.png (cut from the Vigentra logo); assets/video/cam06_1080p, delhi_raw, reel_cam04, reel_cam15

## 2. A camera sees a vehicle
*The question*

- **Story purpose.** Ask the question with one real car.
- **Visual.** CAM06 on Madhuram Bypass Road. A car comes down the road, the frame freezes and the view pushes into its real plate; then the words: Vigentra can.
- **Speaker narration.** This is a real camera on a road in Gujarat. A camera sees a vehicle. [pause] Can it tell us which one? [beat] Vigentra can. Let me show you how hard that is.
- **Audience should understand.** Seeing a car is not the same as knowing which car it is.
- **Transition.** Clip 4.5 s, zoom 4.5 s, answer 3 s: 13 s in all, then the camera wall. Moves on by itself (press A to hold).
- **Real asset.** assets/video/cam06_1080p.mp4, assets/img/cam06_best_frame.jpg (frame 1299)

## 3. Nobody can watch them all
*The problem*

- **Story purpose.** Why this matters: too much video for people.
- **Visual.** The CAM06 feed fills the screen, then the view pulls back to sixteen real feeds from the grid and Delhi. The count of grid cameras rises to 30.
- **Speaker narration.** These are recordings from our grid cameras. Thirty on the grid alone. [pause] Nobody can watch them all. [pause] So Vigentra turns this video into plates you can search.
- **Audience should understand.** The value is turning video into searchable records.
- **Transition.** The 20-s feeds play once through the scene (18.5 s), then one picture. Moves on by itself (press A to hold).
- **Real asset.** assets/video/wall_cam*.mp4 (grid cam01-cam16), cam06_noon, delhi_raw

## 4. The plate is a tiny part of the picture
*The problem*

- **Story purpose.** Show how small the plate really is.
- **Visual.** The CAM06 frame; a box finds the car, then the plate; the plate's real pixels, enlarged, with its size and share of the picture.
- **Speaker narration.** Somewhere in this picture is a number plate. [beat] Here is the car. Here is the plate. [beat] This is everything the camera gave us: a few thousand pixels, a fraction of one percent of the picture.
- **Audience should understand.** Reading a plate means working with very few pixels.
- **Transition.** Hard cut to harder footage.
- **Real asset.** assets/img/cam06_best_frame.jpg, assets/img/plate_1299.png

## 5. This is the real input
*The problem*

- **Story purpose.** Show the real conditions.
- **Visual.** Fast cuts: night, headlights, a distant view, a low-resolution stream, a hand-held camera, a crowded junction. Then the six fly into one grid.
- **Speaker narration.** And that was a good frame. This is what the cameras really give us: night, headlights, distance, low resolution, movement, crowds. [pause] This is the real input.
- **Audience should understand.** Real footage is hard, and every camera is different.
- **Transition.** Six 1.5-s cuts and the collage (10.5 s), the line (4 s), then the flowchart. Moves on by itself (press A to hold).
- **Real asset.** assets/video/reel_cam07, reel_cam15, reel_cam01, cam06_noon, delhi_raw, reel_cam04

## 6. How Vigentra reads a plate
*How Vigentra reads*

- **Story purpose.** The whole method on one live flowchart, before the details.
- **Visual.** A flowchart of six numbered steps, each card showing the real data at that step, two Yes/No decisions, and outcomes with real examples: Not read (a 10-pixel crop) and No plate saved (a blurred plate). A dot follows one car; the caption beside the title explains each step as the dot reaches it. Red dots take the No arrows.
- **Speaker narration.** Here is the whole journey, on one real car. Camera; track the car; find its plate; combine its best frames. [beat] Enough pixels? If not, it is not read, like this 10-pixel crop. Read the plate. Do the readings agree? If not, no plate is saved: nothing to search, no alert, no wrong record. Only then a confirmed plate: plate, time, camera. Never video.
- **Audience should understand.** The steps, and that Vigentra refuses rather than guesses.
- **Transition.** One full journey of the dot, 23 s, then each step on one car. Moves on by itself (press A to hold).
- **Real asset.** assets/img/plate_1299.png (the packet's crop)

## 7. Find it, follow it, find its plate
*How Vigentra reads*

- **Story purpose.** Detection and tracking, live, on real output.
- **Visual.** CAM06 plays at half speed with the deployed models' real boxes. Each box says what the model thinks it is (car, motorcycle; it calls this small car a truck in some frames), then its tracking ID with a trail, then the plate box.
- **Speaker narration.** First, find every vehicle, and what kind it is. [beat] Then follow each one, so one car stays one car: each gets an ID. [beat] Then, inside that car, find the plate. These boxes are our models' real output, frame by frame.
- **Audience should understand.** Vigentra finds the vehicle first, follows it, then looks for its plate.
- **Transition.** Replay 12.5 s, tracking 8.5 s, plate 8.5 s: 29.5 s, then the plate's frames. Moves on by itself (press A to hold).
- **Real asset.** assets/video/cam06_1080p.mp4; K.TRACK.replay (tools/track_evidence.py, deployed YOLO11s + ByteTrack + plate detector)

## 8. One car, many frames
*How Vigentra reads*

- **Story purpose.** Many frames beat one.
- **Visual.** The same plate in eight real frames, growing as the car comes closer; then a slider between one frame and the combined result.
- **Speaker narration.** Because we follow the car, we see its plate many times, bigger as it comes closer. [beat] Vigentra keeps the best frames and combines them. Drag it: one frame, then twelve combined. Nothing is invented; we only use what the camera captured.
- **Audience should understand.** Following the car gives more evidence, and better evidence.
- **Transition.** Before reading: is it good enough?
- **Real asset.** assets/img/plate_12xx.png (deployed detector crops), journey_best.png, journey_enhanced.png

## 9. Is there enough to read?
*How Vigentra reads*

- **Story purpose.** Refusing is part of the job.
- **Visual.** Three real crops. The CAM06 plate is stamped READ; a 10-pixel Delhi crop TOO SMALL; a blurred Delhi plate, 62 px wide, NOT SAVED: the readings disagreed, so no plate number was saved.
- **Speaker narration.** Before we read, we ask: is there enough here? This one, yes. This one is ten pixels wide: we don't even try. This one is a plate, but blurred: the readings disagree, so no plate number is saved. [pause] If the pixels aren't there, Vigentra doesn't guess.
- **Audience should understand.** A refusal is better than a wrong plate.
- **Transition.** Now read the good one.
- **Real asset.** assets/img/journey_best.png, fail_tiny_plate.png, fail_blurred_plate.png (picked by tools/build_assets.py)

## 10. From pixels to characters
*How Vigentra reads*

- **Story purpose.** OCR, and the plate format.
- **Visual.** The enhanced crop; under it each character flickers through other characters before it settles, with a bar for how sure Vigentra is; then the plate splits into State, RTO, Series, Number.
- **Speaker narration.** Now we read it, character by character, with a confidence for each. [beat] And Indian plates follow a pattern: state, RTO, series, number. A 6 where a letter must be is read as G. The pattern catches mistakes.
- **Audience should understand.** Reading uses both the image and the rules of Indian plates.
- **Transition.** But one reading is not enough.
- **Real asset.** assets/img/journey_enhanced.png; per-character confidence from the evidence pack

## 11. Many readings, one answer
*How Vigentra reads*

- **Story purpose.** Many readings, one confirmed answer.
- **Visual.** A cloud of real readings of this plate, in proportion. The minority readings fall away; the majority gathers into one answer, GJ23H1546, stamped CONFIRMED, with three checks.
- **Speaker narration.** Across all the frames, our readers produced 136 readings of this plate. 73 of them say GJ 23 H 1546. [beat] Only when the readings agree, the format is valid and the crops hold real characters do we confirm.
- **Audience should understand.** Vigentra confirms by agreement, not by a single guess.
- **Transition.** Does it work on real footage?
- **Real asset.** K.EVIDENCE.top_readings (evidence/confirmed/cam06_s0_t132.json)

## 12. CAM06 and Delhi
*Proof*

- **Story purpose.** The test on real footage.
- **Visual.** Vigentra's own output: CAM06 from 0:29 of its result video, where the readings start (24 s), then the Delhi street video from the start (28 s), with the scores counting up.
- **Speaker narration.** We counted every plate a person can read in these clips, frame by frame. CAM06: 16. Vigentra read 15. Wrong: zero. [beat] Delhi, hand-held, busy: 20 legible, 13 read. Wrong: zero.
- **Audience should understand.** On hard real footage it reads most plates and never invents one.
- **Transition.** 24 s + 28 s, then all of it together. Moves on by itself (press A to hold).
- **Real asset.** assets/video/cam06_vigentra.mp4, delhi_vigentra.mp4

## 13. What Vigentra achieved
*Proof*

- **Story purpose.** The headline numbers.
- **Visual.** A ring fills to 36 of 48; then 0 wrong; then 12 missed with nothing wrong in their place; then live speed on one laptop.
- **Speaker narration.** Across our test footage, 48 plates a person could read. Vigentra read 36. [beat] Wrong plates: zero. [beat] The 12 it missed, it said nothing, rather than give a wrong plate. [beat] And it keeps pace with a live 720p camera on one laptop.
- **Audience should understand.** Accurate, honest about what it misses, and fast enough.
- **Transition.** Why it misses.
- **Real asset.** final2 benchmark report; docs/anpr-optimisation.md

## 14. The camera decides
*Proof*

- **Story purpose.** The limit of AI: the camera.
- **Visual.** The real crop, degraded by a slider: fewer pixels, blur, darkness, noise, until nothing is left to read. Labelled as a demonstration.
- **Speaker narration.** Watch what happens as the camera captures less. [beat] AI can infer patterns. It cannot recover what was never captured. Camera placement matters.
- **Audience should understand.** Better cameras and placement give better results.
- **Transition.** What the officer actually uses.
- **Real asset.** assets/img/journey_best.png (degraded live in the browser)

## 15. How the pieces connect
*The system*

- **Story purpose.** How the system fits together, on a live flowchart.
- **Visual.** A flowchart: Cameras (four live feeds) → Vigentra reader (the car with its boxes) → Vigentra central (the plate record) → Console (the trace screen). Under central: On the watchlist? Yes → Alert → Console; No → kept for search. A dashed lane above: live video, only with the owning unit's permission. Dots travel each route; the caption explains each box.
- **Speaker narration.** Video comes in; text goes out. The reader sends only the confirmed plate, time and camera to central, which checks it against the watchlist: yes means an alert in the console, no means it is kept for search. [beat] Live video is a separate lane, shared only with the owning unit's permission.
- **Audience should understand.** Vigentra moves plates as text, and video only with permission.
- **Transition.** All four routes once, 22 s; the line, 5 s; then the console. Moves on by itself (press A to hold).
- **Real asset.** services/edge-worker/app/worker.py (the plate record); central-api access model

## 16. The Vigentra console
*The system*

- **Story purpose.** The system around the reader.
- **Visual.** The Vigentra console tilts up into view, playing the useful parts of the recording: live detection on grid CAM06, then the detection search, tracing a vehicle, the plate report and the audit log. The matching label on the right lights up as each part plays.
- **Speaker narration.** This is what an operator uses. Live detection on a grid feed. Search every detection. Trace one vehicle across cameras. A plate report. And an audit log of what everyone does.
- **Audience should understand.** It is a working system, not just a model.
- **Transition.** 83 s (1:15-1:24 and 1:28-2:42 of the recording; the camera wall and the closing card, which show the London feeds, are skipped), then what's next. Moves on by itself (press A to hold).
- **Real asset.** assets/video/app_demo.mp4 (deliverables/Vigentra_Demo_Short.mp4), 1:15-1:24 and 1:28-2:42

## 17. What still doesn't work
*What's next*

- **Story purpose.** Honest limits and the next steps.
- **Visual.** Three items: night and glare; small, distant plates; GPU servers for every feed.
- **Speaker narration.** What still doesn't work. At night plates are found but rarely legible. Under 22 pixels, we won't read. And one laptop keeps up with one camera; all thirty need GPU servers. That is our next step.
- **Audience should understand.** The team knows its limits and the way forward.
- **Transition.** Back to the road.
- **Real asset.** docs/anpr-cam06-day.md; EW/config/thresholds.yaml

## 18. From pixels to information
*Vigentra*

- **Story purpose.** Close on the idea and the name.
- **Visual.** CAM06 plays with Vigentra's real boxes; 'From pixels to information.' Then the Vigentra mark, wordmark and tagline, and thank you.
- **Speaker narration.** A camera sees a vehicle. Vigentra tells you which one, and only when it is sure. From pixels, to information. [pause] Thank you.
- **Audience should understand.** Vigentra turns CCTV into trustworthy information.
- **Transition.** End. Hold on the logo for questions.
- **Real asset.** assets/video/cam06_1080p.mp4 with K.TRACK.replay; brand/*.png
