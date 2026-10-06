# From Pixels to Information: storyboard

Generated from `js/data/storyboard.js` by `tools/storyboard.py`; edit the JS, not this file. Every number is in `js/data/project.js` with its source; every picture is real project footage or output (`tools/build_assets.py`, `tools/track_evidence.py`).

## 1. A camera sees a vehicle
*Act 1 · The question*

- **Story purpose.** Open on the question, not the technology.
- **Visual.** Darkness. A real government CCTV feed fades in; a car comes down Madhuram Bypass Road. The frame freezes; the view pushes in to its number plate.
- **Animation.** Fade from black; freeze; slow push-in onto the plate; words appear alone.
- **Interaction.** Next advances each beat.
- **Speaker narration.** This is a real camera on a road in Gujarat. A camera sees a vehicle. [pause] But can it read it? [pause] That is the whole problem. It has a name: ANPR.
- **Technical concept.** None yet. The question.
- **Required asset.** assets/video/cam06_1080p.mp4 (real CAM06), assets/img/cam06_best_frame.jpg (frame 1299)
- **Audience should understand.** A camera can see a car without being able to read it.
- **Transition.** Cut to black, then to many cameras.

## 2. Hours of video, nobody watching
*Act 2 · Why this exists*

- **Story purpose.** Why ANPR exists: footage does not scale, information does.
- **Visual.** A wall of real camera feeds filling up, then the arithmetic of how much video that is.
- **Animation.** Tiles rise in one by one; numbers count up.
- **Interaction.** Click a tile to see it full size.
- **Speaker narration.** The challenge gave us 30 cameras with 12 hours of footage each. That is 360 hours of video, from a sandbox. Gujarat has more than 80,000 cameras. Nobody watches that. So the question becomes: can the cameras tell us what they saw?
- **Technical concept.** Footage is not information. Searchable records are.
- **Required asset.** assets/video/wall_cam*.mp4 (real grid recordings), cam06_noon.mp4, delhi_raw.mp4
- **Audience should understand.** The goal is to turn hours of video into searchable vehicle information.
- **Transition.** From many cameras back to one: where does it all start?

## 3. Before AI, there is a camera
*Act 3 · The camera comes first*

- **Story purpose.** Start with the physical camera, not the neural network.
- **Visual.** A camera drawn on a pole, its angle, its field of view, the road, and finally the real frame it captures.
- **Animation.** Line drawing of pole, camera, view cone; real frame appears inside the cone.
- **Interaction.** None.
- **Speaker narration.** Before there is any AI, there is a camera. Someone chose where to bolt it, how high, at what angle. Everything we do later can only work with what this camera captures.
- **Technical concept.** Installation, angle, field of view.
- **Required asset.** assets/img/cam06_best_frame.jpg
- **Audience should understand.** The AI only gets what the camera captures.
- **Transition.** Same camera, now judged: good placement or bad?

## 4. Placement decides what can be read
*Act 3 · The camera comes first*

- **Story purpose.** Show that camera placement decides what can be read.
- **Visual.** Left: CAM06 at 1080p, plate 123 px wide. Right: what most grid cameras give: plates around 15 px wide.
- **Animation.** Two frames slide in; conditions appear one by one.
- **Interaction.** None.
- **Speaker narration.** Here the plate is about 120 pixels wide. Readable. Across the grid cameras the typical plate is under 15 pixels wide: too high, too far, too steep. Add backlight, headlights, something in the way, and the plate is gone before any AI sees it.
- **Technical concept.** Plate pixels are set by placement.
- **Required asset.** assets/img/plate_1299.png, assets/img/fail_tiny_plate.png, failure crops
- **Audience should understand.** ANPR starts with camera engineering, not AI.
- **Transition.** The camera is up. How does its video reach us?

## 5. From camera to computer
*Act 4 · Connecting the cameras*

- **Story purpose.** Explain the video connection in one picture.
- **Visual.** Frames streaming from the camera across a network into a processing box; then the stream splits into two paths.
- **Animation.** A filmstrip flows along the link; the path forks.
- **Interaction.** Press T for the technical layer.
- **Speaker narration.** A camera doesn't send us photographs. It sends video, continuously, 25 pictures a second. For the AI we take the stream directly, RTSP over a reliable connection. For people watching in a browser, we use HLS. Same camera, two jobs.
- **Technical concept.** RTSP over TCP for analysis; HLS for remote viewing; time from the video's own timestamps.
- **Required asset.** assets/video/cam06_noon.mp4
- **Audience should understand.** The system works on a live video stream, not on photos.
- **Transition.** So what does that video actually look like?

## 6. Looks simple
*Act 5 · What the camera sees*

- **Story purpose.** Set up the illusion that this is easy.
- **Visual.** The cleanest frame we have, then the plate, sharp and obvious.
- **Animation.** Smooth zoom; the words 'Looks simple.'
- **Interaction.** None.
- **Speaker narration.** In a perfect world the camera is close, the light is good, the plate is big. You can read it yourself. [pause] Looks simple.
- **Technical concept.** None.
- **Required asset.** assets/img/cam06_best_frame.jpg, assets/img/journey_best.png
- **Audience should understand.** On a good frame, reading a plate looks trivial.
- **Transition.** Hard cut to reality.

## 7. This is the real input
*Act 5 · What the camera sees*

- **Story purpose.** Show the real input in all its difficulty.
- **Visual.** A fast montage of real footage: night with headlights, a 480p compressed feed, a 352×288 traffic camera, a hand-held street, blurred and dark crops, each labelled.
- **Animation.** Quick cuts, one label per cut.
- **Interaction.** None.
- **Speaker narration.** This is the real input. Darkness. Headlights. Compression. Low resolution. Tiny plates. Blur. All of it from real cameras.
- **Technical concept.** The failure modes, named.
- **Required asset.** cam06_night.mp4, cam06_noon.mp4, tfl_low.mp4, delhi_raw.mp4, fail_*.png
- **Audience should understand.** Real CCTV is nothing like the clean example.
- **Transition.** Why exactly is this so hard? Pixels.

## 8. The plate is a tiny part of the image
*Act 6 · Pixels matter*

- **Story purpose.** The first major insight: the plate is a tiny part of the image.
- **Visual.** The full frame, then the vehicle, then the plate, then the characters as visible pixel blocks, with the pixel count at each level.
- **Animation.** Zoom by zoom, with live pixel counts; final view shows the real pixels.
- **Interaction.** None.
- **Speaker narration.** The whole picture is two million pixels. The car is about two hundred thousand. The plate is about four thousand. Each character gets around four hundred. We are reading one fifth of one percent of the image.
- **Technical concept.** Plate area as a share of the frame.
- **Required asset.** assets/img/cam06_best_frame.jpg, K.TRACK boxes
- **Audience should understand.** The plate occupies a tiny part of the frame; that is the core difficulty.
- **Transition.** Two different questions hide in 'read the plate'.

## 9. Where is it? What does it say?
*Act 7 · Detection is not recognition*

- **Story purpose.** Separate detection from recognition.
- **Visual.** Left: 'Where is the plate?' with a box on the frame. Right: 'What does it say?' with characters.
- **Animation.** Box draws; crop lifts out; characters type in.
- **Interaction.** None.
- **Speaker narration.** Two different questions. Detection: where is the plate? Recognition: what does it say? Different tools answer them, and both have to be right.
- **Technical concept.** Detection vs recognition.
- **Required asset.** frame_1299.jpg, plate_1299.png
- **Audience should understand.** Finding the plate and reading it are separate steps.
- **Transition.** First question first: where are the vehicles?

## 10. Object detection
*Act 8 · Finding the vehicle*

- **Story purpose.** Introduce object detection through the vehicle.
- **Visual.** The raw frame; boxes lock onto the car and the motorcycle; then the words Object Detection, then YOLO.
- **Animation.** Boxes draw themselves; labels fade in; the name arrives last.
- **Interaction.** Press T: model, classes, threshold.
- **Speaker narration.** The first job is simple to say: find the vehicles. This is called object detection. The model we use is YOLO. It looks at the whole frame once and says: a vehicle here, a motorcycle there.
- **Technical concept.** Object detection, YOLO11s.
- **Required asset.** frame_1299.jpg, K.TRACK.others_at_best
- **Audience should understand.** The system first finds vehicles in the frame.
- **Transition.** Inside the vehicle, a much smaller object.

## 11. A tiny object inside a large one
*Act 9 · Finding the plate*

- **Story purpose.** Plate detection as a small object inside a large one.
- **Visual.** Zoom into the detected car; a second detector finds the plate inside it; the crop lifts out.
- **Animation.** Push-in; amber box draws; crop separates.
- **Interaction.** Press T for model details.
- **Speaker narration.** Now a harder problem. We look for a very small object inside an object we already found. A second, smaller model searches only inside each vehicle, at higher resolution.
- **Technical concept.** Two-stage detection; small-object detection.
- **Required asset.** frame_1299.jpg, plate_1299.png
- **Audience should understand.** Searching inside the vehicle makes the tiny plate findable.
- **Transition.** How small is too small?

## 12. Large to extremely tiny
*Act 10 · Plate scale*

- **Story purpose.** Make plate size physical, then show how many plates are small.
- **Visual.** One real plate shrinking through five size bands until the characters dissolve into blocks; then how many real plates fall in each band.
- **Animation.** The plate steps down in size, rendered with hard pixels; bars grow.
- **Interaction.** None.
- **Speaker narration.** Watch what happens as the same plate gets fewer pixels. At 48 pixels wide you can read it. At 16 it's a guess. At 6 it is a few grey blocks. And here is the problem: on these cameras, seven out of ten plates are narrower than 22 pixels.
- **Technical concept.** Size bands by plate width in pixels.
- **Required asset.** journey_best.png (shrunk for demonstration), project.js sizeBands
- **Audience should understand.** Most plates on real CCTV are tiny, and tiny plates lose their characters.
- **Transition.** But a video gives us something a photo doesn't.

## 13. A video is not one image
*Act 11 · Tracking*

- **Story purpose.** Introduce time: the same vehicle across frames.
- **Visual.** Three real frames with the same car carrying the same ID; a scrubber through its plate from 67 to 150 pixels wide.
- **Animation.** Frames step forward; the ID badge stays on the car.
- **Interaction.** Drag the scrubber through the frames.
- **Speaker narration.** A video gives us something a photograph doesn't: time. The tracker gives this car an identity, number 1, and follows it frame after frame. Now we don't have one picture of its plate. We have dozens.
- **Technical concept.** Multi-object tracking (ByteTrack).
- **Required asset.** frame_1244/1299/1314.jpg, plate_*.png, K.TRACK
- **Audience should understand.** Tracking turns one vehicle into many observations.
- **Transition.** Are all those observations equally good?

## 14. One bad frame isn't the end
*Act 12 · Evidence over time*

- **Story purpose.** Many frames give different pieces of evidence; be honest about limits.
- **Visual.** The plate in every frame, with its size and the detector's confidence; the last two frames are mistakes, flagged.
- **Animation.** Crops line up; bad ones dim in red.
- **Interaction.** Click a crop for its numbers.
- **Speaker narration.** Some frames are small, some are sharp, some are wrong: here the car has left the picture and the detector boxed the dashboard. One bad frame isn't the end. We keep the best crops of the whole track and let them vote. Multiple frames don't invent detail; they let us choose and combine the evidence that is there.
- **Technical concept.** Crop bank, temporal evidence.
- **Required asset.** plate_*.png, K.TRACK, K.EVIDENCE.frames
- **Audience should understand.** Different frames give different evidence; time helps, but cannot invent.
- **Transition.** Before reading anything: is a crop even worth reading?

## 15. Should we even try?
*Act 13 · Quality*

- **Story purpose.** The quality gate: decide whether to try at all.
- **Visual.** A crop on a test bench; its size, sharpness and contrast measured against the thresholds; a verdict.
- **Animation.** Meters fill toward the threshold lines.
- **Interaction.** Tabs: choose a crop.
- **Speaker narration.** Before we try to read, we ask: is this image good enough? Wide enough, sharp enough, enough contrast. This one passes easily. This one is ten pixels wide: we don't read it at all. A guess on a crop like that is worse than no answer.
- **Technical concept.** Legibility gate: width ≥ 22 px, height ≥ 8 px, sharpness ≥ 8, contrast ≥ 25.
- **Required asset.** journey_best.png, fail_tiny_plate.png, fail_low_contrast.png
- **Audience should understand.** The system refuses to read what can't be read.
- **Transition.** For crops that pass, can we make them better?

## 16. Can we make it better?
*Act 14 · Enhancement*

- **Story purpose.** Enhancement, and its hard limit.
- **Visual.** The real best crop, then the same plate fused from 12 frames, then enhanced; a slider compares raw with enhanced.
- **Animation.** Steps tick off; before/after slider.
- **Interaction.** Drag the slider.
- **Speaker narration.** For crops that pass, we clean them up: straighten, align twelve frames on top of each other, combine them, reduce glare and noise, sharpen where there is blur. But remember this: enhancement cannot create information the camera never captured.
- **Technical concept.** Rectify, register, multi-frame fusion, glare, denoise, deblur, deskew.
- **Required asset.** journey_best.png, journey_fused.png, journey_enhanced.png
- **Audience should understand.** Enhancement helps the reader; it does not invent detail.
- **Transition.** Now, finally, read it.

## 17. From image to characters
*Act 15 · Reading*

- **Story purpose.** Recognition: from image to characters.
- **Visual.** The enhanced plate; characters appear one by one with their confidence.
- **Animation.** Characters type in; confidence bars rise under each.
- **Interaction.** Press T for the readers.
- **Speaker narration.** Now optical character recognition turns pixels into characters. The detector told us where the plate is. OCR tells us what it says. Three readers read every crop, and their answers are combined letter by letter.
- **Technical concept.** OCR (CRNN ×2, PP-OCRv5), ROVER fusion.
- **Required asset.** journey_enhanced.png, journey_charconf.png, K.EVIDENCE.per_char_conf
- **Audience should understand.** OCR answers 'what does it say', character by character.
- **Transition.** But should we believe it?

## 18. OCR can be wrong
*Act 16 · Validation*

- **Story purpose.** OCR can be wrong; validation decides what to believe.
- **Visual.** The readings the system actually produced for this plate, aligned so the disagreements show; then the checks it passed.
- **Animation.** Readings stack; agreement highlights; checks tick.
- **Interaction.** None.
- **Speaker narration.** OCR is not truth. For this one car the readers produced 136 readings. Most say GJ23H1546; some say 1545. Reading a plate is not enough. We decide whether we believe it: enough frames agree, the format is a real Indian plate, the crop really contains characters. Only then is it confirmed. If not, we stay silent.
- **Technical concept.** Voting, confirm rules, glyph check, refusing to guess.
- **Required asset.** K.EVIDENCE.top_readings, verify_sheet.png
- **Audience should understand.** The system confirms only what enough evidence supports.
- **Transition.** One of those checks is knowledge about Indian plates.

## 19. Plates are not random text
*Act 17 · Indian plates*

- **Story purpose.** Domain knowledge as validation.
- **Visual.** GJ · 23 · H · 1546 split into state, district, series, number; then a misread digit corrected by the slot it sits in.
- **Animation.** Segments separate and label; one character flips.
- **Interaction.** None.
- **Speaker narration.** Indian plates are not random text. Two letters for the state, two digits for the district office, a series, a number. If a reader says six-J, the first slot must be a letter, and six is a common misread of G. The format helps us check, and fix, the reading.
- **Technical concept.** Plate grammar: standard, Delhi, Bharat series, diplomatic, temporary.
- **Required asset.** project.js grammar
- **Audience should understand.** Knowing the format catches impossible readings.
- **Transition.** Now let's break it.

## 20. Now break it.
*Act 18 · The hardest cases*

- **Story purpose.** The hardest cases, fast and dramatic.
- **Visual.** Real failures in quick succession: 10-pixel plate, darkness, glare, blur, low contrast, a 352×288 camera, night video.
- **Animation.** Rapid cuts; red measured reasons.
- **Interaction.** None.
- **Speaker narration.** Now break it. Ten pixels wide. Dark. Glare. Blur. No contrast. Every one of these is real, and every one of these was refused rather than guessed. [pause] This is where the real engineering begins.
- **Technical concept.** Measured failure causes.
- **Required asset.** fail_*.png, cam06_night.mp4, tfl_low.mp4
- **Audience should understand.** Hard cases are the norm, and the system says no instead of guessing.
- **Transition.** So can it handle a real, difficult clip?

## 21. CAM06 and Delhi
*Act 19 · Real stress test*

- **Story purpose.** Real stress test on CAM06 and the Delhi street.
- **Visual.** Two real clips. The question: a person can read 16 plates here, and 20 there. How many does the system read, and how many does it get wrong?
- **Animation.** Videos play; counters run; zero wrong lands last.
- **Interaction.** None.
- **Speaker narration.** CAM06, a government camera at noon: a person, going frame by frame, can read 16 plates. The system read 15. Wrong: zero. A busy Delhi street, filmed by hand: 20 plates are readable. The system read 13. Wrong: zero.
- **Technical concept.** Ground truth counted by eye.
- **Required asset.** cam06_vigentra.mp4, delhi_vigentra.mp4
- **Audience should understand.** On real, hard footage it reads most legible plates and never invents one.
- **Transition.** How did it get here? By collecting its failures.

## 22. When the model fails, we collect evidence
*Act 20 · Dataset*

- **Story purpose.** Failures become a dataset.
- **Visual.** Real labelling sheets of plate crops; then the dataset that came from them, by difficulty.
- **Animation.** Sheet zooms; category bars grow.
- **Interaction.** None.
- **Speaker narration.** We don't improve a model by hoping. We collect the cases where it struggles: tiny plates, low contrast, darkness, blur, glare. 933 images, 634 plates, most of them hard. The median plate in this set is under 15 pixels wide.
- **Technical concept.** Hard-case mining, annotation.
- **Required asset.** gt_sheet.jpg, project.js dataset
- **Audience should understand.** Real failures are turned into training and test data.
- **Transition.** That becomes a loop.

## 23. Train, test, fail, learn, improve
*Act 21 · The engineering loop*

- **Story purpose.** The engineering loop, honestly.
- **Visual.** Deploy → observe failures → collect → annotate → train → evaluate → deploy again, going round.
- **Animation.** A highlight travels round the loop.
- **Interaction.** None.
- **Speaker narration.** This is the real story. Not 'we trained a model and finished'. The real world shows us where it fails, we collect those cases, we fix, we measure, and we go round again. Five training runs on the hard set so far, and none in production yet: the loop also tells you when not to ship.
- **Technical concept.** Continuous improvement driven by failures.
- **Required asset.** project.js training, improvements
- **Audience should understand.** The system improves because the real world keeps teaching it where it fails.
- **Transition.** How do we know a change helped?

## 24. How do we know it improved?
*Act 22 · Evaluation*

- **Story purpose.** Metrics, explained visually, with real per-size results.
- **Visual.** Recall and precision as pictures; then a plate detector's results by plate size.
- **Animation.** Dots sort themselves; bars grow band by band.
- **Interaction.** Press T for all runs.
- **Speaker narration.** Recall: of all the plates that were there, how many did we find? Precision: of everything we called a plate, how much really was one? AP combines the two. Look at it by size: large plates, nearly perfect. Extremely tiny: almost nothing.
- **Technical concept.** Recall, precision, AP; size bands.
- **Required asset.** project.js eval (reports/eval_by_size)
- **Audience should understand.** Large plates are easy, tiny plates are hard, and the numbers show it.
- **Transition.** Now that you have seen every part, here is the whole.

## 25. Everything comes together
*Act 23 · The complete pipeline*

- **Story purpose.** Reveal the whole system only now.
- **Visual.** The complete chain, with data pulses flowing through it.
- **Animation.** Stages appear in order; pulses travel.
- **Interaction.** Click any stage.
- **Speaker narration.** Camera. Stream. Vehicles. Tracking. Plates. The quality gate. Enhancement. Readers. The vote. Validation. A result, or silence. Every piece is there because of a problem you have now seen.
- **Technical concept.** End-to-end pipeline.
- **Required asset.** none
- **Audience should understand.** Each stage answers a specific problem.
- **Transition.** And here it is running.

## 26. The system comes alive
*Act 24 · The control room*

- **Story purpose.** The finished system in a control room.
- **Visual.** A wall of real feeds with live detections, and the operators' dashboard.
- **Animation.** Wall fills; one feed expands.
- **Interaction.** Click any feed.
- **Speaker narration.** This is what the finished system looks like. Cameras from different departments on one wall, vehicles detected, plates read, and when a plate is on a watchlist, an alert within seconds.
- **Technical concept.** Control room, alerts.
- **Required asset.** cam06_vigentra.mp4, delhi_vigentra.mp4, wall_cam*.mp4, app_demo.mp4
- **Audience should understand.** The pipeline runs live behind a usable console.
- **Transition.** Let's follow one car all the way through.

## 27. Follow one vehicle
*Act 25 · One vehicle's journey*

- **Story purpose.** The strongest technical demonstration: one vehicle, every stage, real data.
- **Visual.** GJ23H1546's journey stage by stage, each with its real output.
- **Animation.** Each stage opens with its real artefact.
- **Interaction.** Click stages, or press Next.
- **Speaker narration.** One car. Frame 1299. Found as a vehicle. Tracked as ID 1 for 46 frames. Plate found at 79% confidence. Best crop: quality 0.97, sharp and high-contrast. Twelve frames fused. 136 readings, 39 frames agreeing. Valid format. Confirmed: GJ23H1546. And it goes into the database as a sighting, checked against the watchlist.
- **Technical concept.** Everything, applied to one vehicle.
- **Required asset.** K.EVIDENCE, K.TRACK, journey_*.png, frame_1299.jpg
- **Audience should understand.** Each stage contributes something measurable to one final answer.
- **Transition.** How much did all this improve things?

## 28. Evolution
*Act 26 · Before and after*

- **Story purpose.** Before vs after, with measured numbers.
- **Visual.** The first benchmark, the same clips after the changes, and the final run.
- **Animation.** Numbers transform from old to new.
- **Interaction.** Press T for the list of changes.
- **Speaker narration.** First benchmark: 7 of 21 readable plates. On the same 36 clips, the changes took us from 9 to 16 correct, cut junk plate candidates from 22 thousand to 3 and a half thousand, and made it faster. The final run: 36 of 48 readable plates. Wrong: zero, every time.
- **Technical concept.** Measured improvement.
- **Required asset.** project.js baseline, sameClips, final
- **Audience should understand.** The improvements are measured, and false reads stayed at zero.
- **Transition.** And what still fails?

## 29. Don't hide the failures
*Act 27 · Failure analysis*

- **Story purpose.** Show the failures, classified.
- **Visual.** Where the 1,760 unresolved tracks went, by cause; real examples of each.
- **Animation.** Bars by cause; a failure explorer.
- **Interaction.** Tabs: real failure crops.
- **Speaker narration.** Most vehicles never give us a plate at all: 1,576 tracks where no plate was ever found. That is the camera and the plate size. Then the readers disagree, or only one kind of reader agrees, or the image is too soft. These are not hidden. They're our to-do list.
- **Technical concept.** Failure analysis.
- **Required asset.** project.js failures, fail_*.png
- **Audience should understand.** Most failures start at the camera; the rest are measured and named.
- **Transition.** Some of these can't be fixed by AI at all.

## 30. The boundary
*Act 28 · What AI cannot do*

- **Story purpose.** What AI cannot do.
- **Visual.** A real readable plate; a slider removes pixels, adds blur and darkness until nothing is left.
- **Animation.** The plate degrades live.
- **Interaction.** Drag the slider (a demonstration on a real crop).
- **Speaker narration.** Watch. As the camera captures less, the characters disappear. At some point there is nothing left to recover. AI can infer patterns. It cannot recover information that was never captured.
- **Technical concept.** Information loss.
- **Required asset.** journey_best.png
- **Audience should understand.** No model can read what the pixels do not contain.
- **Transition.** So which lever matters more?

## 31. The real lesson
*Act 29 · Camera vs AI*

- **Story purpose.** Close the loop back to the camera.
- **Visual.** Two levers: better AI and better camera data. The same system on a 120-pixel plate and on a 10-pixel plate.
- **Animation.** Two levers; the camera lever wins.
- **Interaction.** None.
- **Speaker narration.** Two levers. Better AI, and better data from the camera. The same software reads this plate every time, and can never read this one. The best model cannot compensate forever for a bad camera.
- **Technical concept.** Camera vs model.
- **Required asset.** plate_1299.png, fail_tiny_plate.png
- **Audience should understand.** Camera placement and resolution are the biggest lever.
- **Transition.** From a demo to a deployed system.

## 32. From demo to deployment
*Act 30 · Deployment*

- **Story purpose.** A model is not a system.
- **Visual.** Everything a real deployment needs, assembled around the model.
- **Animation.** Components orbit in around the model.
- **Interaction.** None.
- **Speaker narration.** A model in a notebook is not a system. You need the cameras, the network, the streams, hardware at the edge, storage, monitoring that tells you when a camera dies, alerts, and people who verify before anyone acts.
- **Technical concept.** Deployment architecture.
- **Required asset.** none
- **Audience should understand.** Reliability comes from the whole system, not the model alone.
- **Transition.** And it has to be fast.

## 33. Real-time means more than accuracy
*Act 31 · Performance*

- **Story purpose.** Real-time means more than accuracy.
- **Visual.** Measured time per frame and speed against real time, by resolution, on one Apple M1.
- **Animation.** Bars race a real-time marker.
- **Interaction.** Press T for memory and sizing estimates.
- **Speaker narration.** A model that's accurate but slower than the camera isn't real-time. On one laptop chip, a 720p camera is read at real time; 1080p a little under; 480p faster. Ingest handles 164 plate reads a second.
- **Technical concept.** Latency, throughput, hardware.
- **Required asset.** project.js speed
- **Audience should understand.** Speed is part of correctness for live cameras.
- **Transition.** So what did we actually achieve?

## 34. What did we actually achieve?
*Act 32 · Results*

- **Story purpose.** The results, after the audience knows what they mean.
- **Visual.** A handful of large, real numbers.
- **Animation.** Numbers count up one at a time.
- **Interaction.** None.
- **Speaker narration.** 36 of 48 readable plates across 38 real clips. Zero wrong. CAM06: 15 of 16. Delhi: 13 of 20. 84 vehicles counted once each, from 141 tracker IDs. A full day of CAM06: 59 plates, zero wrong.
- **Technical concept.** Results.
- **Required asset.** project.js results
- **Audience should understand.** It reads most legible plates and never invents one.
- **Transition.** And what still doesn't work.

## 35. What still doesn't work
*Act 33 · Limitations*

- **Story purpose.** Limitations, stated plainly.
- **Visual.** What still fails, one line each.
- **Animation.** Lines appear; the title flips from 'failures' to 'next problems'.
- **Interaction.** None.
- **Speaker narration.** Plates under twenty pixels. Night. Glare. Heavy occlusion. Badly placed cameras. Live reading needs a GPU. Our plate detector still needs a proper evaluation by size, on human-checked labels. These are not just failures. They are the next engineering problems.
- **Technical concept.** Limitations.
- **Required asset.** none
- **Audience should understand.** The team knows exactly where the system stops.
- **Transition.** Where next?

## 36. Where do we go next?
*Act 34 · Where next*

- **Story purpose.** A realistic roadmap.
- **Visual.** Five steps, from cameras to deployment.
- **Animation.** Steps light in sequence.
- **Interaction.** None.
- **Speaker narration.** Better cameras and placement first, because that is the biggest lever. Then plate-specific small-object models on human-checked data. Better use of many frames. Better reading and confidence. And a deployment that scales: GPUs at the edge, many cameras, monitored, with alerts.
- **Technical concept.** Roadmap.
- **Required asset.** none
- **Audience should understand.** The next steps follow from the measured failures.
- **Transition.** Back to where we started.

## 37. From pixels to information
*Act 35 · The answer*

- **Story purpose.** Return to the opening, now understood.
- **Visual.** The same car on the same road; the pipeline's words pass over it; then FROM PIXELS TO INFORMATION.
- **Animation.** Words drift over the footage; final type.
- **Interaction.** None.
- **Speaker narration.** Same camera. Same car. But now you know what happens behind the picture. Camera. Pixels. Vehicle. Plate. Tracking. Enhancement. OCR. Validation. Result. [pause] From pixels [pause] to information. [pause] That's ANPR.
- **Technical concept.** The whole journey.
- **Required asset.** cam06_1080p.mp4
- **Audience should understand.** ANPR is the journey from pixels to trustworthy information.
- **Transition.** End.
