# Vigentra · From Pixels to Information: storyboard

Generated from `js/data/storyboard.js` by `tools/storyboard.py`; edit the JS, not this file. Every number is in `js/data/project.js` with its source; every picture is real project footage or output (`tools/build_assets.py`, `tools/track_evidence.py`).

## 1. Vigentra control room
*Vigentra*

- **Story purpose.** Open on the name, as a control room powering up.
- **Visual.** Four real feeds glow faintly; the Vigentra mark, wordmark and tagline; a boot log types out what the system is made of: the hackathon model (Model 1 + Model 3), cameras, the vehicle detector (YOLO11s, YOLO11n on the live reader), the tracker, the plate detector, the readers, the plate rules.
- **Speaker narration.** Good morning. This is Vigentra: vigilance, intelligence, safer roads. [pause] Gujarat already has thousands of cameras, owned by many departments, each with its own system. Our answer to this challenge is Model 1 plus Model 3. Model 1: one statewide registry, so every camera, whoever owns it, is on one map with its owner and its health. Model 3: we federate each department's video system through an adapter, instead of asking anyone to replace theirs. On top sits one console. [beat] And because a registry alone does not catch a vehicle, we read number plates, at the edge, from the CCTV that is already on the road. Over the next minutes I will show you how it reads, what it has achieved on real footage, how the whole system fits together, and what it takes to run it on eighty thousand cameras.
- **Audience should understand.** Who we are, and that this is a working system.
- **Transition.** You move on (→) when you are ready.
- **Real asset.** brand/*.png; assets/video/cam06_1080p, delhi_raw, reel_cam04, reel_cam15

## 2. Nobody can watch them all
*The problem*

- **Story purpose.** The problem: too much video for people.
- **Visual.** CAM06 full screen; the view pulls back to a wall of sixteen real feeds. Banners: 30 cameras. Nobody can watch them all. Vigentra turns this video into plates you can search.
- **Speaker narration.** These are recordings from the Sentinel grid: real Gujarat junctions, replayed to us as live streams. Thirty cameras on the grid alone, and the event adds about fifty. [pause] No control room can watch them all. A person watching one feed misses the other twenty-nine, and a vehicle that matters passes in seconds. [pause] So the question is not how to show more video. It is how to turn video into something you can ask: was this plate seen, where, and when? That is what Vigentra does: it turns this video into plates you can search.
- **Audience should understand.** The value is turning video into searchable records.
- **Transition.** You move on (→) when you are ready.
- **Real asset.** assets/video/wall_cam*.mp4 (grid cam01-cam16), cam06_noon, delhi_raw

## 3. This is the real input
*The problem*

- **Story purpose.** The real conditions.
- **Visual.** Six monitors power on one by one: night, headlights, far away, low resolution, a moving camera, a crowded junction. Then: This is the real input.
- **Speaker narration.** Before the technology, the honest picture. This is what the cameras really give us. [beat] Night, when most of the frame is dark. Headlights that wash out the plate. Vehicles far away, a few pixels wide. Low-resolution streams, many at 720p or below. A camera that moves. A junction crowded with two-wheelers and autos. [pause] Any system shown on clean, close, daylight footage would look good. We built and measured ours on this. This is the real input.
- **Audience should understand.** Real footage is hard, and every camera is different.
- **Transition.** You move on (→) when you are ready.
- **Real asset.** assets/video/reel_cam07, reel_cam15, reel_cam01, cam06_noon, delhi_raw, reel_cam04

## 4. A camera sees a vehicle
*One vehicle*

- **Story purpose.** One car, and how small its plate is.
- **Visual.** CAM06 in a viewfinder: a car comes down Madhuram Bypass Road, the frame freezes, the view moves onto the plate. A readout: 122 × 32 pixels, under 0.2% of the picture.
- **Speaker narration.** Let me take one vehicle through the system. This is CAM06, on Madhuram Bypass Road in Bhavnagar, a real grid camera. A car comes down the road. [beat] The camera sees a vehicle. But can it tell us which vehicle? [beat] Look at the plate: about a hundred and twenty pixels wide and thirty-two high. That is less than a fifth of one percent of the picture. Everything you see next is about getting a reliable registration number out of that small a patch, and refusing when it cannot.
- **Audience should understand.** Reading a plate means working with very few pixels.
- **Transition.** You move on (→) when you are ready.
- **Real asset.** assets/video/cam06_1080p.mp4, assets/img/cam06_best_frame.jpg, plate_1299.png

## 5. The signal chain
*How Vigentra reads*

- **Story purpose.** The whole method as one live schematic.
- **Visual.** Six numbered stages, each showing the real data at that step, two YES/NO decisions, and outcomes with real refused plates: Not read, No plate saved. A square follows one car; the event log says what each stage does as it arrives. Red squares take the NO lines.
- **Speaker narration.** Here is the whole method on one screen, following that real car. [beat] The camera gives us video. We find the car and track it, so it keeps one identity. Inside the car, and only inside it, we look for the plate. We keep its best frames and combine them. [beat] Then the first decision: are there enough pixels? If not, it is not read at all. If yes, we read the plate. [beat] Then the second decision: do the readings agree, and is it a valid Indian format? If not, no plate is saved. [pause] Only when both answers are yes do we have a confirmed plate: the registration, the time and the camera. Never the video. The red squares you see are plates that took a NO line: that is the system declining to guess.
- **Audience should understand.** The steps, and that Vigentra refuses rather than guesses.
- **Transition.** You move on (→) when you are ready.
- **Real asset.** assets/img/plate_1299.png, journey_fused.png, fail_tiny_plate.png, fail_motion_blur.png

## 6. Find it, follow it, find its plate
*How Vigentra reads*

- **Story purpose.** Detection and tracking, live, on the real output.
- **Visual.** CAM06 on a monitor at half speed with the deployed models' boxes: each says what kind of vehicle it is (car, motorcycle), then its ID with a trail, then the plate box. Beside it an event log prints, frame by frame, what the models saw.
- **Speaker narration.** Now the first stages, live, with the models we deploy. [beat] First: find every vehicle and say what it is: car, motorcycle, auto, bus. [beat] Second: follow each one. Every vehicle gets an ID and a trail, so one car stays one car across frames, and we decide its plate once, not again in every frame. [beat] Third: inside that vehicle only, find the plate box. Searching inside the vehicle is what keeps shop signs and banners out. The log on the right is the system narrating what it saw, frame by frame.
- **Audience should understand.** Vigentra finds the vehicle, follows it, then looks for its plate.
- **Transition.** You move on (→) when you are ready.
- **Real asset.** assets/video/cam06_1080p.mp4; K.TRACK (tools/track_evidence.py, deployed settings)

## 7. One car, many frames
*How Vigentra reads*

- **Story purpose.** Many frames beat one.
- **Visual.** An evidence buffer: the same plate in nine real frames, growing from 67 to 138 pixels; then a slider between one frame and twelve frames combined and cleaned.
- **Speaker narration.** Here is why tracking matters. Because we follow the car, we see its plate many times: in these nine real frames it grows from sixty-seven to a hundred and thirty-eight pixels as the car comes closer. [beat] Vigentra keeps the best frames and combines them into one clearer image. Let me drag this: one frame on the left, twelve frames combined on the right. [pause] Nothing is painted in or invented. The extra detail is real: it comes from the other frames of the same plate.
- **Audience should understand.** Following the car gives more evidence, and better evidence.
- **Transition.** You move on.
- **Real asset.** assets/img/plate_12xx.png (deployed detector crops), journey_best.png, journey_enhanced.png

## 8. Is there enough to read?
*How Vigentra reads*

- **Story purpose.** Refusing is part of the job.
- **Visual.** Three samples: the CAM06 plate stamped READ; a 10-pixel Delhi crop TOO SMALL; a blurred Delhi plate NOT SAVED, because the readings disagreed.
- **Speaker narration.** Before reading, we ask an honest question: is there enough here to read? [beat] This CAM06 plate: yes, read it. [beat] This one, from the Delhi clip, is ten pixels wide. We do not even try: no reader in the world can recover characters from ten pixels. [beat] This one is a plate, but blurred. We tried, the readings disagreed, so no plate number is saved. [pause] This gate is why Vigentra makes few mistakes: when the pixels are not there, it does not guess.
- **Audience should understand.** A refusal is better than a wrong plate.
- **Transition.** You move on.
- **Real asset.** assets/img/journey_best.png, fail_tiny_plate.png, fail_blurred_plate.png

## 9. From pixels to characters
*How Vigentra reads*

- **Story purpose.** OCR, and the plate format.
- **Visual.** The combined crop; under it each character flickers before it settles, with a bar for how sure Vigentra is; then the plate splits into State, RTO, Series, Number.
- **Speaker narration.** Now the reading itself, character by character, each with a bar showing how sure the system is. [beat] Then Indian plate rules. A Gujarat plate has a pattern: two letters for the state, digits for the RTO, a series, and a number. So if a reader sees a six where only a letter can stand, the rules read it as a G. The plate splits into state, RTO, series and number, and each part must make sense.
- **Audience should understand.** Reading uses the image and the rules of Indian plates.
- **Transition.** You move on.
- **Real asset.** assets/img/journey_enhanced.png; per-character confidence from the evidence pack

## 10. Many readings, one answer
*How Vigentra reads*

- **Story purpose.** Many readings, one confirmed answer, one saved record.
- **Visual.** A tally of the 136 real readings: GJ23H1546 leads with 73; the others fade; CONFIRMED. Then the saved record types out: plate, camera, status, readings, frames, format, sent as text with one proof photo.
- **Speaker narration.** One car gave us many frames, and our readers produced a hundred and thirty-six readings of this plate. [beat] Seventy-three of them say G J 23 H 1546; the rest scatter. [beat] Only when enough frames agree is the plate confirmed. [pause] And this is everything that is saved: the plate, the camera, the time, how many frames agreed, the format, and one small photo of the vehicle in its best frame, so anyone checking the report can see the proof. Never video.
- **Audience should understand.** Vigentra confirms by agreement and keeps the proof, never the video.
- **Transition.** You move on.
- **Real asset.** K.EVIDENCE.top_readings (evidence/confirmed/cam06_s0_t132.json)

## 11. CAM06 and Delhi
*Field test*

- **Story purpose.** The test on real footage.
- **Visual.** Two monitors with Vigentra's own output: CAM06 from 0:29 of its result video, where the readings start, then the Delhi street video; under each the scores count up.
- **Speaker narration.** Then we measured it like an examiner would. A person went through these clips frame by frame and wrote down every plate a human can actually read. [beat] CAM06: sixteen readable plates. Vigentra read fifteen. Wrong: zero. [beat] The Delhi street clip, hand-held and busy: twenty readable, thirteen read. Wrong: zero. On screen is Vigentra's own output, and the counts are against that human check, not against our own guesses.
- **Audience should understand.** On hard real footage it reads most plates and never invents one.
- **Transition.** You move on (→) when you are ready.
- **Real asset.** assets/video/cam06_vigentra.mp4, delhi_vigentra.mp4

## 12. What Vigentra achieved
*Field test*

- **Story purpose.** The headline numbers.
- **Visual.** A ring fills to 36 of 48; then 0 wrong, 12 missed with nothing saved for them, live speed at 720p on one laptop, and the live grid: 203 different plates confirmed, 10 cameras with 4 or more reads, 1,116 frames a minute (12 Oct).
- **Speaker narration.** Across all our test footage there were forty-eight plates a person could read. Vigentra read thirty-six of them. [beat] Wrong plates: zero. [beat] The twelve it missed, it saved nothing for. It did not put a wrong number into the database to look better. [beat] It keeps pace with a live 720p camera, in real time, on one laptop. [beat] And on the live Sentinel grid, as of this morning: two hundred and three different plates confirmed, ten cameras with four or more confirmed reads, all thirty cameras read at over a thousand frames a minute on one Mac GPU.
- **Audience should understand.** Accurate, honest about what it misses, and fast enough.
- **Transition.** You move on.
- **Real asset.** final2 benchmark report; docs/anpr-optimisation.md

## 13. What still doesn't work
*Known limits*

- **Story purpose.** The honest limits.
- **Visual.** A signal-quality control degrades the real crop until nothing is left (a demonstration). The boundary: AI cannot recover what the camera never captured. Then three known limits.
- **Speaker narration.** Now the honest part: what still does not work. Watch this real crop as the camera captures less and less. [beat] At some point there is simply nothing left. AI cannot recover what the camera never captured. [pause] So: night and headlight glare remain hard. Very small, distant plates are not readable. And reading every feed live, all the time, needs GPU servers at the edge, not one laptop. We say this up front because a system that claims to read everything is a system that guesses.
- **Audience should understand.** The team knows its limits and the way forward.
- **Transition.** You move on.
- **Real asset.** assets/img/journey_best.png (degraded live in the browser); docs/anpr-cam06-day.md; config/thresholds.yaml

## 14. How the pieces connect
*The system*

- **Story purpose.** How the system fits together.
- **Visual.** A schematic: Cameras (four live feeds) → Vigentra reader → Vigentra central → Console. On the watchlist? YES → Alert; NO → kept for search. A dashed lane: live video, only with the owning unit's permission. Squares travel each route; the event log explains each box.
- **Speaker narration.** Now step back from the single car to the system. [beat] Video comes in from the department's cameras; text goes out. The reader sends the plate, its time, its camera and one proof photo of the vehicle to Vigentra central; never the video. Central checks every plate against the watchlist as it arrives: on the list means an alert, prioritised for an operator; not on the list means it is kept for search and tracing. [beat] Live video is a separate lane. Another unit sees it only when the owning department grants it.
- **Audience should understand.** Vigentra moves plates as text with a proof photo, and video only with permission.
- **Transition.** You move on (→) when you are ready.
- **Real asset.** services/edge-worker/app/worker.py (the plate record); central-api access model

## 15. The high-level design
*The system*

- **Story purpose.** The high-level design in four tiers.
- **Visual.** A live schematic: cameras, edge in the site, regional per district, central for the state, the console; a red dashed line between edge and regional marks where only metadata crosses. Squares carry video into the edge and text beyond it.
- **Speaker narration.** Now zoom out to the state. The design has four tiers. [beat] Cameras stay with their departments, on their own video systems. The edge, inside the department's site, reads the video where it is recorded: plates, vehicle types, helmets and footage quality. Each district has a regional tier that speaks the local systems' dialects and holds records if the network to the centre drops. Central keeps the records and decides who may see what. [pause] The red line is the point of the design: only metadata crosses it, never video. And each tier grows on its own: more GPUs, more districts or more central replicas, without touching the others.
- **Audience should understand.** Why it scales: video stays local, text travels.
- **Transition.** You move on (→) when you are ready.
- **Real asset.** K.CHARTS.hld()

## 16. The Vigentra console
*The system*

- **Story purpose.** What an operator uses.
- **Visual.** The Vigentra console on a monitor, playing the useful parts of the recording: live detection on grid CAM06, the detection search, tracing a vehicle, the plate report, the audit log. The matching item on the right lights up as each plays.
- **Speaker narration.** This is what an operator actually uses, recorded from the running system. Live detection on a grid feed, with boxes on every vehicle. A search across every detection. A vehicle traced across cameras. The plate report. And the audit log, where every search and every trace is written down against the person who made it.
- **Audience should understand.** It is a working system, not just a model.
- **Transition.** You move on (→) when you are ready.
- **Real asset.** assets/video/app_demo.mp4 (deliverables/Vigentra_Demo_Short.mp4)

## 17. The console today
*The system*

- **Story purpose.** The console as it is today, page by page.
- **Visual.** A monitor steps through seven screenshots of the running console: operations overview, registry map, live wall, a trace, incident review, alerts, footage quality. The page list on the right lights up, and a line under the monitor says what is visible.
- **Speaker narration.** And this is the console as it is today, in screenshots from the running system. [beat] The operations overview: seventy cameras registered, fifty in service, three department systems connected, with onboarding and camera health on one page. [beat] The registry map: every camera with coordinates, on the map of Gujarat, coloured by department and by health. [beat] The live wall: the grid cameras this account may watch, with detection boxes, and the plates just read running along the top. [beat] A trace: every read of one plate, with time, place, department, and how many frames agreed. [beat] Incident review: each candidate led by its snapshot, its confidence and the fine if confirmed. [beat] Alerts: here, cameras whose picture is breaking up, raised by the footage check within a minute. [beat] And footage quality, camera by camera, so a camera that is online but useless is not invisible.
- **Audience should understand.** The features are real and running, in today's console.
- **Transition.** You move on: one press per screen, seven screens.
- **Real asset.** assets/img/app_v2_*.jpg (deliverables/tools/snap_app.py)

## 18. See every camera. Find any vehicle.
*The system*

- **Story purpose.** Every feature, part 1: seeing and finding.
- **Visual.** Twelve cards appear row by row: camera registry and GIS, onboarding, department federation, live wall, live ANPR, plate search, vehicle trace, live pursuit, watchlist alerts, Indian vehicle types, traffic counts, ANPR report.
- **Speaker narration.** Everything Vigentra does fits on two screens. This first one is about seeing and finding. [beat] Model 1: a camera registry with a GIS map, onboarding of new cameras one by one or by CSV, and federation, where each department's system joins through an adapter. [beat] A live wall of every camera. Live number-plate reading at the edge, plate search that tolerates the usual misreads like zero and O, a vehicle trace on the map, and a live pursuit that gives priority to the cameras a vehicle can reach next. [beat] A watchlist that raises a prioritised alert within seconds, every vehicle typed in Indian classes, traffic counts per camera and hour, and the ANPR report.
- **Audience should understand.** The breadth of the working system.
- **Transition.** You move on, row by row.
- **Real asset.** js/data/project.js featuresFind

## 19. Act on it, with a person in charge.
*The system*

- **Story purpose.** Every feature, part 2: acting and governing.
- **Visual.** Twelve cards: helmet detection, wrong way and incidents, incident review, e-challan, camera health alerts, footage quality alerts, gap analysis, video access requests, role and zone access, audit log, alert push, self-healing reader.
- **Speaker narration.** The second screen is about acting, with a person in charge. [beat] Riders without helmets, with a snapshot and one call per rider. Wrong-way vehicles, possible collisions, people in traffic and intrusions. Each one is a candidate, reviewed as a card, confirmed or dismissed by a person. A confirmed offence opens the e-challan with the plate filled in, the owner looked up, and the MV Act section and fine. [beat] Camera health alerts within about forty seconds, and footage alerts within a minute when a picture goes black or breaks up. Gap analysis of thin districts, ageing cameras and unusable footage. [beat] And governance: video access requests between units, access by role, city and zone, an append-only audit log, signed alert push to other systems, and a reader that restarts itself after crashes and grid outages.
- **Audience should understand.** Every action has evidence and a person behind it.
- **Transition.** You move on, row by row.
- **Real asset.** js/data/project.js featuresAct

## 20. What Vigentra is built with
*Under the hood*

- **Story purpose.** What Vigentra is built on, with one line of reason per layer.
- **Visual.** Eight panels: capture (RTSP over TCP), vehicles (YOLO11s + ByteTrack), plates (YOLO11n inside each vehicle), reading (two CRNN readers + PaddleOCR), deciding (votes, plate rules, glyph check), central (FastAPI, PostgreSQL), console (Next.js, Leaflet, OpenStreetMap), delivery (Docker).
- **Speaker narration.** Under the hood, every part is open source: no licence fee, no vendor account. [beat] Video comes in over RTSP, the protocol the cameras already speak. YOLO11 finds the vehicles, the larger model for the recorded results and the lighter one on the live reader, and ByteTrack gives each vehicle an identity, so its plate is decided once. The plate detector looks only inside the vehicle. Two kinds of reader read it, CRNN models and PaddleOCR, because different readers make different mistakes. [beat] Then the vote, the Indian plate rules and a check that real character shapes are present decide. The centre is FastAPI and PostgreSQL, holding no video and no camera passwords. The console is Next.js on OpenStreetMap, and it all ships in Docker.
- **Audience should understand.** Every part is a deliberate, open choice.
- **Transition.** You move on.
- **Real asset.** js/data/project.js (stack) · TECHNICAL.md

## 21. Why this, not that
*Under the hood*

- **Story purpose.** Why each big choice beats the obvious alternative, with its evidence.
- **Visual.** A table of seven decisions: what Vigentra uses, instead of what, and why it is better.
- **Speaker narration.** Every choice was made against an alternative, and here is why ours wins. The AI runs next to the video, not in one central data centre: that is forty to two hundred and fifty times less network. A plate is read once per vehicle, from all its frames: the single-frame reader we started with found one plate in sixty-seven vehicles. Only what many frames agree on is saved, which is how we keep wrong plates at zero. Two kinds of reader together read sixteen plates where one read thirteen. ByteTrack, because fixed cameras need no camera-motion correction. Watchlist matching weighted by real OCR errors, so one wrong character still raises the alert. And video is brokered, so revoking someone's access stops their stream within one segment.
- **Audience should understand.** Each choice is measured or reasoned, not fashion.
- **Transition.** You move on.
- **Real asset.** js/data/project.js (choices) · TECHNICAL.md

## 22. What we tested and dropped
*Under the hood*

- **Story purpose.** Engineering discipline: what we tried, measured and dropped.
- **Visual.** Six rows: confirming on 3 crops (a wrong plate on Delhi), 20 crops (no gain), two CRNNs backing each other (shared mistakes), frames enlarged to 1920 px (half the vehicles, slower), a plate box up to 0.9 of its vehicle (a hoarding read), one frame at a time (1 plate from 67 vehicles).
- **Speaker narration.** Just as important is what we tried and threw away, because each was measured on the same clips before we decided. Confirming a plate on three crops instead of four let a wrong plate through on Delhi, so it stays at four. Twenty crops instead of twelve found nothing more. Our two CRNN readers share mistakes, so they are not allowed to vouch for each other. Enlarging every frame to nineteen-twenty pixels halved the vehicles found and slowed everything down. A loose plate box let a hoarding be read as a plate. And reading one frame at a time found one plate in sixty-seven vehicles. Nothing went in on a hunch.
- **Audience should understand.** Every threshold has a measured reason.
- **Transition.** You move on.
- **Real asset.** js/data/project.js (dropped) · docs/anpr-optimisation.md

## 23. What we will do next
*Next*

- **Story purpose.** What we will do next, from today's limits.
- **Visual.** A deployment plan: a reader for borderline plates (next); night and small plates; more Indian vehicle classes; more offences; real owner lookup (next); proven at load.
- **Speaker narration.** What we do next, each item starting from a limit we measured ourselves. [beat] First, a better reader for borderline plates: our new plate detector already finds more plates than the reader can read safely, so the reader is the next thing to train, on labels people have checked. Second, night and small plates: camera placement and infrared guidance, and a small-plate detector trained on hard cases. Third, more Indian vehicle classes: e-rickshaws, goods autos and tractors, which are still too rare on our cameras to learn. Fourth, more offences: red light, triple riding and no seat belt, each with its evidence and a person to confirm. Fifth, the real owner lookup, from the state VAHAN registry, in place of our demo list. And sixth, proof at load, well past today's hundred and sixty-four plate reads a second.
- **Audience should understand.** A concrete plan, grounded in what the project measured.
- **Transition.** You move on.
- **Real asset.** docs/scalability.md; js/data/project.js (roadmap)

## 24. What becomes possible
*80,000 cameras*

- **Story purpose.** What 80,000 cameras make possible.
- **Visual.** Six cards: statewide vehicle trace, pursuit across districts, watchlist at every junction, e-challan at state scale, traffic by vehicle class, journey times and flows; each tagged Built on 30 or Next.
- **Speaker narration.** Now imagine this on eighty thousand cameras. One query instead of twenty-six footage requests to twenty-six departments. [beat] A named vehicle traced across every department's cameras, with the evidence at each point. Pursued live across districts, with the next cameras on its route given priority. Flagged at any junction in the state the moment it is read. Challans for helmet and wrong-side offences wherever they are seen. Traffic counted by Indian vehicle class at every junction, so you know when goods vehicles enter no-entry hours. And journey times and origin-to-destination flows between districts, from plate reads alone. Four of these run today on our thirty cameras; the rest extend the same pipeline.
- **Audience should understand.** The value grows with every camera added.
- **Transition.** You move on.
- **Real asset.** js/data/project.js possible

## 25. What it needs
*80,000 cameras*

- **Story purpose.** What it takes, honestly sized.
- **Visual.** Two big numbers: about 160 Gbps if video went central, about 1 Gbps with Vigentra; then the requirements table, tier by tier: edge GPUs, regional, central, storage, integrations, people, cost.
- **Speaker narration.** And here is what it takes, sized honestly. If every camera's video were sent to one data centre, you would need about a hundred and sixty gigabits a second of network, and storage for about one point seven petabytes of video every day. Vigentra sends only metadata: about one gigabit a second for the whole state. [beat] What it does need: about three thousand GPU nodes at the edge, in the departments' own sites, with plate reading only on cameras whose view allows it. Two nodes per district. Two central sites, one a disaster-recovery copy. Metadata kept hot for thirty days, warm for a year, cold for seven, with the audit log kept in full. Access to each department's video system and the state network. People in every district to confirm alerts, and a small team labelling footage. The edge GPUs are the one large cost, ninety to a hundred and twenty crore over the phases; the software has no licence fees.
- **Audience should understand.** It is feasible, and the cost is in one known place.
- **Transition.** You move on.
- **Real asset.** js/data/project.js needsBig, needs · docs/scalability.md

## 26. What makes Vigentra different
*Why Vigentra*

- **Story purpose.** What sets Vigentra apart, as design choices with evidence.
- **Visual.** Six panels: nothing new at the camera (Model 1 + Model 3); a wrong plate is worse than a missed one (the zero-false rule); built for Indian roads (40 state codes, 39 Gujarat RTOs); private by design (retention enforced on read); trained on Gujarat's own footage (4,891 plate boxes, 2,160 vehicles, 1,317 riders labelled by people); the department stays in control (grant, deny, revoke).
- **Speaker narration.** So, to close: what makes Vigentra different? [beat] Nothing new at the camera: it federates what each department already runs. [beat] A wrong plate counts for more than a missed one: we rejected a model that read two more plates because it added one wrong plate. [beat] It is built for Indian roads: forty state codes, thirty-nine Gujarat RTOs, Indian vehicle classes and MV Act sections. [beat] It is private by design: no central video, and plate records expire on a clock checked on every read. [beat] It is trained on Gujarat's own footage, labelled by people: almost five thousand plate boxes, over two thousand vehicles and over thirteen hundred riders. [beat] And every department stays in control of its own video.
- **Audience should understand.** Vigentra's choices: existing cameras, no guessing, federated video, Indian plates, accountability, health.
- **Transition.** You move on.
- **Real asset.** js/data/project.js (distinct, with sources)

## 27. From pixels to information
*Vigentra*

- **Story purpose.** Close on the idea and the name.
- **Visual.** CAM06 plays with Vigentra's real boxes; From pixels to information. Then the Vigentra mark, wordmark and tagline, and thank you.
- **Speaker narration.** From pixels to information. [pause] That is Vigentra: the cameras Gujarat already has, turned into answers an officer can act on, with a person in charge and a record of every step. [pause] Thank you. We are happy to show you anything live.
- **Audience should understand.** Vigentra turns CCTV into trustworthy information.
- **Transition.** End. Hold on the logo for questions.
- **Real asset.** assets/video/cam06_1080p.mp4 with K.TRACK.replay; brand/*.png
