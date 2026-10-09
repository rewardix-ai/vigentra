# Vigentra models: what runs, why, and how well

Every model in the system, what it does, why it was chosen over the alternatives, and what it scored on
our own footage. Numbers come from measurements in this repository (`docs/submission.md`, the research
repo's `reports/LOOP_LOG.md`, `models/PROVENANCE.json`), not from vendors' claims. State as of 8 Oct 2026.

## 1. The chain at a glance

A grid frame passes through five stages. Only the first four are models; the last is rules.

| # | Stage | Model in live (light) mode | Size | Runs on | Licence |
|---|---|---|---|---|---|
| 1 | Find vehicles | **YOLO11n**, COCO-pretrained, 640 px | 5.6 MB | Mac GPU (MPS) | AGPL-3.0 |
| 2 | Follow each vehicle | **ByteTrack** (an algorithm, no weights) | – | CPU | MIT |
| 3 | Find the plate on the vehicle | **plate_det_grid_clean** (YOLO, fine-tuned on grid footage) | 5.4 MB | Mac GPU | AGPL-3.0 (Ultralytics) |
| 4 | Read the plate | **CRNN v3b** (primary) + **CRNN v6** (fallback) + **Awiros-ANPR-OCR** (PaddleOCR PP-OCRv5) | 7.7 + 7.7 MB + PaddleOCR | CPU (ONNX, Paddle) | own / Apache-2.0 |
| 5 | Decide: confirm, candidate or nothing | Indian plate grammar, multi-frame fusion, voting | – | CPU | own |

Beside the chain: Apple Vision reads each camera's on-screen clock, and the incident detector is rules over
the tracker's boxes (no model).

**Why this shape.** A plate is a few dozen pixels on a 720p frame. Looking for plates in the whole frame
wastes the GPU on road, sky and hoardings, and finds plates too small to read. So vehicles are found
first, cheaply, and the plate is searched only inside a vehicle near enough to carry a readable one
(at least 96 px wide). Each vehicle is followed by the tracker so that its plate is read across many
frames and voted on, rather than trusted from one blurred frame.

## 2. Vehicle detector: YOLO11n

**What it does.** Boxes cars, motorcycles, buses and trucks in every processed frame (four of COCO's 80
classes), at 640 px in light mode.

**Why YOLO11n.** Thirty cameras share one M1 GPU. The nano model is the only size that leaves time for
plate search and reading on thirty streams; YOLO11s (19 MB) is the default for a single-camera edge
worker. A vehicle near enough to carry a readable plate is still well over 30 px wide at 640 px, and
the plate itself is read from the full-resolution frame, so the small input costs little.

**Measured (8 Oct, 480 frames from 10 grid clips, against YOLO11s at 1280 px):**

| | YOLO11n (in use) | YOLO26n (tried) |
|---|---|---|
| Finds plate-sized vehicles (>= 96 px) | **90.8 %** | 89.7 % |
| Finds vehicles >= 48 px | **86.6 %** | 84.2 % |
| Speed on this Mac's GPU | **31 ms/frame** | 34 ms/frame |

YOLO26n was not adopted: it found slightly fewer of the vehicles that matter and ran slower here.

**Known limit.** COCO has no auto-rickshaw or scooter class: autos come out as "truck" or "car",
scooters as "motorcycle". See section 8.

## 3. Tracker: ByteTrack

**What it does.** Gives each vehicle an id that persists from frame to frame (Ultralytics' ByteTrack,
`track_buffer: 45`), so the plate crops of one vehicle are pooled and voted on, and so incidents can use
its motion.

**Why ByteTrack.** It keeps low-confidence boxes in the association step, which holds onto vehicles
through the blur and partial occlusion common on grid footage, and it needs no appearance model, so it
costs almost nothing.

**Known limit, found 8 Oct.** In light mode a quiet camera gets a frame only every few seconds; the
tracker then hands one id from one vehicle to another. Incidents now refuse such tracks (gaps over 1 s
or jumps over 1.5 vehicle heights); see section 7.

## 4. Plate detector: plate_det_grid_clean (live), plate_det_mix_n (default)

**What it does.** Finds the plate box inside a vehicle crop (the crop gets a 5 % margin and is enlarged
to 640 px, the same in training and in use).

| | plate_det_mix_n | plate_det_grid_clean |
|---|---|---|
| Base | YOLO11n, fine-tuned from an HF plate model | the same family, fine-tuned again on grid footage |
| Training data | 6k elevated vehicle-rear crops + 13.5k CCPD (Chinese plates) | 450 train / 129 val crops from grid clips, every box checked by eye (40 epochs, 640 px) |
| Its own validation | mAP50 0.938, recall 0.946 | mAP50 0.995 on the grid val set |
| On 70 live near vehicles it gave no plate box (8 Oct) | 0 plates found | **3 real plates** (two autos, one truck: yellow commercial plates), checked by eye |
| Where used | single-camera edge worker (default) | light mode, since 8 Oct |

**Why the grid model in live mode.** Grid footage is 720p at low bitrate, often at night, and carries
many yellow commercial plates (autos, taxis, trucks). The default model was trained mostly on other
footage and missed those plates outright; the grid model finds them.

**Caution.** The grid validation set was built from boxes the old detector proposed itself, so every
model scores about 0.99 on it and it cannot rank them. That is why the comparison above uses crops the
live system actually missed, and why the next fine-tune uses frames harvested from the live grid with
every box checked by eye (section 9).

## 5. Plate readers: CRNN v3b, CRNN v6, Awiros-ANPR-OCR

Three readers, each with a different job.

| Reader | What it is | Measured | Role |
|---|---|---|---|
| **CRNN v3b** (`reader_crnn.onnx`) | Small CNN + recurrent net with CTC output; greyscale 64x256 input; 36 characters + blank; 7.7 MB | 65.7 % exact, 8.8 % character error on 494 held-out real Indian plates | **Primary.** Its vote alone decides a confirmation. |
| **CRNN v6** (`reader_crnn_v6.onnx`) | Same network, further trained on 2,232 consensus-labelled plates | Replay as fallback: Delhi 4K 11 read / 9 confirmed / 0 wrong; cam06 5/5 read / 3 confirmed / 0 wrong | **Fallback.** Supplies a read only when the primary cannot decide; such a read is shown, never confirmed. |
| **Awiros-ANPR-OCR** | PaddleOCR PP-OCRv5 fine-tuned on 558k Indian plates; reads two-row plates in one pass; Apache-2.0 | Lifts the Delhi clip 11 to 12 of 20 plates and cam06 1080p 2 to 3 of 5, none wrong, at ~1.6x reading time | **Second opinion,** 3 crops per vehicle, weight 2 in the vote; one shared copy for all 30 cameras in light mode. |

**Why three.** No single reader is good enough on grid crops, and they fail differently. A plate is
confirmed only when the evidence agrees, so a second architecture catches the first one's misreads
instead of repeating them. A read from a later CRNN (v8) was rejected because it produced one wrong
confirmation on cam06.

## 6. The decision layer (rules, not a model)

This is what turns readings into a plate the system will stand behind:

- **Indian plate grammar.** A beam search over the reader's character probabilities keeps only strings
  shaped like a registration (state code, district, series, number), with Gujarat as the tie-break state.
- **Multi-frame fusion.** Crops of one vehicle are aligned (ECC), merged by weighted median and enlarged
  3x, giving a cleaner image than any single frame.
- **Voting.** CONFIRMED needs at least 4 crops reading the string exactly, at least 40 % of the vote, a
  runner-up under half the winner's support, and a plate at least 40 px wide. Anything less is a
  CANDIDATE (shown for review) or nothing.
- **Why so strict.** A wrong plate on a traced route sends police after the wrong vehicle. The system
  would rather report fewer plates than invent one.

**Measured on grid clips with by-eye ground truth (8 Oct):** fed every 5th frame, the chain confirms
**15 of 17** readable plates. Asking each vehicle "is this plate P?" (target search) adds only one more,
so the live shortfall is frames per camera, not reading.

## 7. Other components

| Component | What it is | Why |
|---|---|---|
| On-screen clock reader | Apple Vision OCR (`scripts/osd/ocr.swift`), on-device | Each grid camera prints its own date and time; reading it puts every sighting on the footage's timeline. Runs every 30 min per camera. |
| Helmet classifier | Four YOLO11n-cls (1.5 M parameters each) trained on 1,317 of our own grid rider crops labelled by eye (v2, v4, v7 and v8), one of them (v8) on the head band only so the head is ~1.7x larger; scores averaged over each rider's three closest views (`app/helmet.py`, `models/helmet_cls.pt`, `_v4`, `_v7`, `_v8_head`). Judged only on a vehicle the type classifier finds a two-wheeler | Flags bare-headed riders of motorcycles and scooters from 70 px wide as LOW candidates at mean >= 0.8, with the rider enlarged on the snapshot. On 221 held-out riders (50 bare-headed): 21 calls, all right, none on a helmeted rider; the set before it (with v5, v6) 23 calls, 22 right. The two cam06 riders in black helmets it had called bare now average 0.42 and 0.48. Live, checked by eye since 8 Oct 17:23: 46 calls right, 1 unsure, 1 wrong (a pedal cargo tricycle, since excluded). v2 alone scores some helmeted riders above 0.95 (a live miss on cam06, 8 Oct); the trio puts that rider below 0.75. Live audit of the first 14 calls: 10 of 11 judgeable right. A free downloaded helmet model found nothing on our footage. |
| Vehicle-type classifier | YOLO11n-cls trained on 960 grid vehicles labelled by eye (`app/vehicle_type.py`, `models/vtype_cls.pt`): auto-rickshaw, bus, car, motorcycle, scooter, truck | The COCO detector called autos "truck" (45 of 70 clear "trucks" were autos, 6 were trucks) and never separates scooters. A confident call (>= 0.7) replaces the detector's class; held out by camera, types right went from 88 of 146 (detector) to 130 of 146; its confident calls are right 108 of 113. |
| Incident detector | Rules over tracker boxes (`anpr/incidents.py`): wrong way, sudden stop, stopped in lane, collision candidate, person on carriageway, intrusion; plus vehicle without a visible plate on every vehicle type (`app/no_plate.py`, from the plate detector's own misses, only in daylight, when the vehicle faced the camera fully in frame, close enough for its plate to be readable, and the detector proposes nothing even at confidence 0.03) | No incident footage exists to train a model on, so the rules are explicit and every incident carries its evidence and a snapshot. Rebuilt 8 Oct: direction learned per region of the frame, only dense continuous tracks judged; a 6-hour replay went from 416 incidents to 71. |
| Frame quality router | Blur (Laplacian variance) and darkness checks | Skips frames nothing could be read from; brightens dark ones. |
| Cross-camera matching | Confusion-weighted plate distance (`scripts/cross_camera.py`) | The same plate misread on two cameras (O/0, B/8, D/0) still links the route. |

## 8. Tried and not adopted

| Model | What it was for | Result | Decision |
|---|---|---|---|
| YOLO-World v2-s + CLIP ViT-B/32 | Six vehicle types incl. auto rickshaw and scooter, by naming them | On our night crops it named no auto or scooter correctly; autos came out as truck or car | Not adopted; replaced by the vehicle-type classifier trained on our labelled crops (section 7). |
| Helmet v3 (from COCO weights, 320 px) | Helmet classifier | Under-confident: at 0.9 it made no call on held-out riders | Not adopted; v4 (from v2) is used with v2. |
| Helmet v5 paired with v2 only | Helmet classifier | On 203 held-out riders: 25 calls, 22 right, one on a helmet | Not adopted as a pair; used in the trio. |
| YOLO26n | Vehicle detector | 89.7 % vs 90.8 % recall, slower here (section 2) | Not adopted as vehicle detector; will be tried as base for the plate-detector fine-tune. |
| plate_det_grid, plate_det_hf_v1s | Alternative plate detectors | In a raw test on 53 missed crops they boxed 5 and 6 (grid_clean 4, mix_n 1), some of them junk; through the full chain grid_clean was checked best | Kept for labelling (they propose boxes for review). |
| CRNN v8 | Reader | One wrong confirmation on cam06 | Rejected; v6 stays. |
| Gemini Flash-Lite (free tier) | Offline judge of hard crops | 93 % plate/junk correct on 28 crops; when "certain", 10 of 10 right | Offline only (labelling, verification); not in the live path. |
| Claude vision reader | Offline teacher | Built, never measured | No free tier; not used. |
| Target-plate search | "Is this crop plate P?" | Adds 1 of 17 plates on grid clips | Low priority; pursuit mode comes first. |

## 9. What is being trained now

- **Data.** The live reader saves frames with a vehicle near enough to read (one per camera per 10 s,
  8,000 at most), for training only.
- **Labels.** Four plate detectors propose boxes on each vehicle crop; every box is checked by eye.
  The first sheet showed why: all four agreed that GSRTC bus lettering was a plate, so such boxes become
  hard negatives. Any crop showing a benchmark target plate is excluded.
- **Training.** Two candidates overnight: one continuing from plate_det_grid_clean (YOLO11), one from
  YOLO26n.
- **Adoption.** Only if it beats the current model, first on the checked validation set and then live,
  side by side, for an hour each.

## 10. Licences

| Licence | Components | What it means for us |
|---|---|---|
| AGPL-3.0 | Ultralytics YOLO11 / YOLO26 and models fine-tuned with it | Free to use and train. Offering it as a service obliges publishing our source under AGPL; a closed product needs an Ultralytics Enterprise licence. |
| Apache-2.0 | PaddleOCR, Awiros-ANPR-OCR | Free, including commercial use, with attribution. |
| MIT | ByteTrack | Free, with attribution. |
| Own work | CRNN readers, grammar, fusion, voting, incident rules | Ours. |
| Platform | Apple Vision | Part of macOS; runs only on the Mac. |
