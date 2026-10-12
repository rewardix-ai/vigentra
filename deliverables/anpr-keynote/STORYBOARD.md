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

## 6. What Vigentra achieved
*Field test*

- **Story purpose.** The headline numbers.
- **Visual.** A ring fills to 36 of 48; then 0 wrong, 12 missed with nothing saved for them, live speed at 720p on one laptop, and the live grid: 203 different plates confirmed, 10 cameras with 4 or more reads, 1,116 frames a minute (12 Oct).
- **Speaker narration.** Across all our test footage there were forty-eight plates a person could read. Vigentra read thirty-six of them. [beat] Wrong plates: zero. [beat] The twelve it missed, it saved nothing for. It did not put a wrong number into the database to look better. [beat] It keeps pace with a live 720p camera, in real time, on one laptop. [beat] And on the live Sentinel grid, as of this morning: two hundred and three different plates confirmed, ten cameras with four or more confirmed reads, all thirty cameras read at over a thousand frames a minute on one Mac GPU.
- **Audience should understand.** Accurate, honest about what it misses, and fast enough.
- **Transition.** You move on.
- **Real asset.** final2 benchmark report; docs/anpr-optimisation.md

## 7. What still doesn't work
*Known limits*

- **Story purpose.** The honest limits.
- **Visual.** A signal-quality control degrades the real crop until nothing is left (a demonstration). The boundary: AI cannot recover what the camera never captured. Then three known limits.
- **Speaker narration.** Now the honest part: what still does not work. Watch this real crop as the camera captures less and less. [beat] At some point there is simply nothing left. AI cannot recover what the camera never captured. [pause] So: night and headlight glare remain hard. Very small, distant plates are not readable. And reading every feed live, all the time, needs GPU servers at the edge, not one laptop. We say this up front because a system that claims to read everything is a system that guesses.
- **Audience should understand.** The team knows its limits and the way forward.
- **Transition.** You move on.
- **Real asset.** assets/img/journey_best.png (degraded live in the browser); docs/anpr-cam06-day.md; config/thresholds.yaml

## 8. The high-level design
*The system*

- **Story purpose.** The high-level design in four tiers.
- **Visual.** A live schematic: cameras, edge in the site, regional per district, central for the state, the console; a red dashed line between edge and regional marks where only metadata crosses. Squares carry video into the edge and text beyond it.
- **Speaker narration.** Now zoom out to the state. The design has four tiers. [beat] Cameras stay with their departments, on their own video systems. The edge, inside the department's site, reads the video where it is recorded: plates, vehicle types, helmets and footage quality. Each district has a regional tier that speaks the local systems' dialects and holds records if the network to the centre drops. Central keeps the records and decides who may see what. [pause] The red line is the point of the design: only metadata crosses it, never video. And each tier grows on its own: more GPUs, more districts or more central replicas, without touching the others.
- **Audience should understand.** Why it scales: video stays local, text travels.
- **Transition.** You move on (→) when you are ready.
- **Real asset.** K.CHARTS.hld()

## 9. The console today
*The system*

- **Story purpose.** The console as it is today, page by page.
- **Visual.** A monitor steps through seven screenshots of the running console: operations overview, registry map, live wall, a trace, incident review, alerts, footage quality. The page list on the right lights up, and a line under the monitor says what is visible.
- **Speaker narration.** And this is the console as it is today, in screenshots from the running system. [beat] The operations overview: seventy cameras registered, fifty in service, three department systems connected, with onboarding and camera health on one page. [beat] The registry map: every camera with coordinates, on the map of Gujarat, coloured by department and by health. [beat] The live wall: the grid cameras this account may watch, with detection boxes, and the plates just read running along the top. [beat] A trace: every read of one plate, with time, place, department, and how many frames agreed. [beat] Incident review: each candidate led by its snapshot, its confidence and the fine if confirmed. [beat] Alerts: here, cameras whose picture is breaking up, raised by the footage check within a minute. [beat] And footage quality, camera by camera, so a camera that is online but useless is not invisible.
- **Audience should understand.** The features are real and running, in today's console.
- **Transition.** You move on: one press per screen, seven screens.
- **Real asset.** assets/img/app_v2_*.jpg (deliverables/tools/snap_app.py)

## 10. What becomes possible
*80,000 cameras*

- **Story purpose.** What 80,000 cameras make possible.
- **Visual.** Six cards: statewide vehicle trace, pursuit across districts, watchlist at every junction, e-challan at state scale, traffic by vehicle class, journey times and flows; each tagged Built on 30 or Next.
- **Speaker narration.** Now imagine this on eighty thousand cameras. One query instead of twenty-six footage requests to twenty-six departments. [beat] A named vehicle traced across every department's cameras, with the evidence at each point. Pursued live across districts, with the next cameras on its route given priority. Flagged at any junction in the state the moment it is read. Challans for helmet and wrong-side offences wherever they are seen. Traffic counted by Indian vehicle class at every junction, so you know when goods vehicles enter no-entry hours. And journey times and origin-to-destination flows between districts, from plate reads alone. Four of these run today on our thirty cameras; the rest extend the same pipeline.
- **Audience should understand.** The value grows with every camera added.
- **Transition.** You move on.
- **Real asset.** js/data/project.js possible

## 11. What it needs
*80,000 cameras*

- **Story purpose.** What it takes, honestly sized.
- **Visual.** Two big numbers: about 160 Gbps if video went central, about 1 Gbps with Vigentra; then the requirements table, tier by tier: edge GPUs, regional, central, storage, integrations, people, cost.
- **Speaker narration.** And here is what it takes, sized honestly. If every camera's video were sent to one data centre, you would need about a hundred and sixty gigabits a second of network, and storage for about one point seven petabytes of video every day. Vigentra sends only metadata: about one gigabit a second for the whole state. [beat] What it does need: about three thousand GPU nodes at the edge, in the departments' own sites, with plate reading only on cameras whose view allows it. Two nodes per district. Two central sites, one a disaster-recovery copy. Metadata kept hot for thirty days, warm for a year, cold for seven, with the audit log kept in full. Access to each department's video system and the state network. People in every district to confirm alerts, and a small team labelling footage. The edge GPUs are the one large cost, ninety to a hundred and twenty crore over the phases; the software has no licence fees.
- **Audience should understand.** It is feasible, and the cost is in one known place.
- **Transition.** You move on.
- **Real asset.** js/data/project.js needsBig, needs · docs/scalability.md

## 12. What makes Vigentra different
*Why Vigentra*

- **Story purpose.** What sets Vigentra apart, as design choices with evidence.
- **Visual.** Six panels: nothing new at the camera (Model 1 + Model 3); a wrong plate is worse than a missed one (the zero-false rule); built for Indian roads (40 state codes, 39 Gujarat RTOs); private by design (retention enforced on read); trained on Gujarat's own footage (4,891 plate boxes, 2,160 vehicles, 1,317 riders labelled by people); the department stays in control (grant, deny, revoke).
- **Speaker narration.** So, to close: what makes Vigentra different? [beat] Nothing new at the camera: it federates what each department already runs. [beat] A wrong plate counts for more than a missed one: we rejected a model that read two more plates because it added one wrong plate. [beat] It is built for Indian roads: forty state codes, thirty-nine Gujarat RTOs, Indian vehicle classes and MV Act sections. [beat] It is private by design: no central video, and plate records expire on a clock checked on every read. [beat] It is trained on Gujarat's own footage, labelled by people: almost five thousand plate boxes, over two thousand vehicles and over thirteen hundred riders. [beat] And every department stays in control of its own video.
- **Audience should understand.** Vigentra's choices: existing cameras, no guessing, federated video, Indian plates, accountability, health.
- **Transition.** You move on.
- **Real asset.** js/data/project.js (distinct, with sources)

## 13. From pixels to information
*Vigentra*

- **Story purpose.** Close on the idea and the name.
- **Visual.** CAM06 plays with Vigentra's real boxes; From pixels to information. Then the Vigentra mark, wordmark and tagline, and thank you.
- **Speaker narration.** From pixels to information. [pause] That is Vigentra: the cameras Gujarat already has, turned into answers an officer can act on, with a person in charge and a record of every step. [pause] Thank you. We are happy to show you anything live.
- **Audience should understand.** Vigentra turns CCTV into trustworthy information.
- **Transition.** End. Hold on the logo for questions.
- **Real asset.** assets/video/cam06_1080p.mp4 with K.TRACK.replay; brand/*.png
