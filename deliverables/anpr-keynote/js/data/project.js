/* Every number the keynote shows, with where it was measured. Nothing here is projected or
 * invented. Paths are relative to the vigentra repository (docs/, services/edge-worker/ = EW).
 */
window.K = window.K || {};
K.DATA = {
  grid: { cameras: 30, src: "Sentinel grid cam01-cam30 (camera registry; docs/submission.md)" },

  pipeline: {
    vehicle: "YOLO11s (yolo11s.pt), car · motorcycle · bus · truck, confidence ≥ 0.15, frames at native size",
    tracker: "ByteTrack (config/bytetrack.yaml): one identity per vehicle",
    plate: "YOLO11n plate detector (plate_det_mix_n.pt) inside each vehicle box, 640 px, confidence ≥ 0.2",
    crops: "best 12 of up to 64 plate crops per vehicle",
    gate: { width: 22, height: 8, sharpness: 8.0, contrast: 25.0 },
    readers: "CRNN readers, plus the PP-OCRv5 text reader on the Mac readers; ROVER letter-by-letter vote",
    confirm: "a strong fused vote (≥ 0.75 over ≥ 3 frames, runner-up under half the winner) or agreement between different readers (2 crops from each reader type, 6 in all); always a valid Indian format with real character shapes in the crops",
    src: "EW/config/thresholds.yaml, EW/config/bytetrack.yaml, EW/anpr/*",
  },

  // Plate grammar: positional character types; "6" in a letter slot is read as "G"
  grammar: { segments: [["GJ", "State"], ["23", "RTO"], ["H", "Series"], ["1546", "Number"]], src: "EW/anpr/plate_grammar.py (TO_LETTER)" },

  // final2 benchmark. Legible = readable by eye, counted frame by frame before scoring. Only five
  // clips hold legible plates; the rest of the 38 have none a person can read.
  final: {
    read: 36, legible: 48, wrong: 0,
    cam06Noon: [15, 16], delhi: [13, 20],
    src: "services/edge-worker/reports/anpr_benchmark/final2/REPORT.md (cam06_noon 15/16, delhi_1080p 13/20, cam06_1080p 3/5, cam06 18:00 clip 5/6, cam07 0/1; 0 false everywhere)",
  },

  // What sets Vigentra apart: its own design choices, each with where it is shown. Nothing here is
  // a claim about other products.
  distinct: [
    ["Nothing new at the camera", "Federates the cameras and VMS each department already runs, and reads their streams as they are.", "Model 1 + Model 3", "docs/hld.md §2 · EW/app/grid.py"],
    ["A wrong plate is worse than a missed one", "A model that reads more plates but adds one wrong plate is not adopted, however good it looks.", "zero-false rule", "reports/plate_ab_auto_ft_v2.md · bench pd_v2 (9 Oct)"],
    ["Built for Indian roads", "Plate formats down to BH series and temporary plates; CMVR vehicle classes; MV Act sections on challans.", "40 state codes · 39 Gujarat RTOs", "EW/anpr/plate_grammar.py · config/india_codes.yaml"],
    ["Private by design", "No central video store, and plate records expire on a clock that is enforced on every read.", "retention on read", "docs/scalability.md §4"],
    ["Trained on Gujarat's own footage", "Plate boxes, vehicles and riders from our grid cameras, each one labelled by a person.", "4,891 boxes · 2,160 vehicles · 1,317 riders", "reports/review/harvest_a · data/cls/vtype · data/cls/helmet"],
    ["The department stays in control", "Live video crosses to another unit only on a grant the owner can refuse or revoke.", "grant · deny · revoke", "services/video_grants.py"],
  ],

  // What we intend to do next: each starts from a limit the project's own reports record.
  roadmap: [
    ["A reader for borderline plates", "The plate detector already finds more plates than the reader can read safely; retrain the reader on labels checked by eye.", "Next"],
    ["Night and small plates", "Camera placement and IR guidance, and a small-plate detector trained on human-checked hard cases.", "Planned"],
    ["More Indian vehicle classes", "E-rickshaws, goods autos and tractors: too few on our cameras so far to learn them.", "Planned"],
    ["More offences", "Red light, triple riding and no seat belt, each with its evidence and a person to confirm it.", "Planned"],
    ["Real owner lookup and SMS", "Replace the demo owner list and the simulated SMS with VAHAN and a DLT-registered gateway.", "Next"],
    ["Proven at load", "Kafka between ingest and matching, tested well past today's 164 plate reads a second.", "Planned"],
  ],
  roadmapSrc: "plate detector A/B 9 Oct (reports/plate_ab_auto_ft_v2.md) · data/cls/vtype (class counts) · services/challan_service.py (demo owners, simulated SMS) · docs/scalability.md §5 (164 reads/s), §6 (Kafka)",

  // Under the hood: each layer, what it runs on, and why. TECHNICAL.md has the full reasoning.
  // Every feature (deck scenes "features-find", "features-act"): [name, what it does]
  featuresFind: [
    ["Camera registry + GIS", "Every department's cameras on one map, with owner, purpose and health."],
    ["Camera onboarding", "Request, approve and add cameras, one at a time or by CSV."],
    ["Department federation", "Each VMS joins through an adapter; nothing is replaced."],
    ["Live wall", "Every camera at once; detection boxes and people on or off."],
    ["Live ANPR", "Plates read at the edge, settled across frames, sent as text."],
    ["Plate search", "Find any plate, allowing for misreads like 0/O and 8/B."],
    ["Vehicle trace", "A plate's route on the map: place, read time, footage time."],
    ["Live pursuit", "The cameras a vehicle can reach next get the GPU first."],
    ["Watchlist alerts", "A listed plate raises a prioritised alert within seconds."],
    ["Indian vehicle types", "Scooter, motorcycle, auto, car, minibus, bus, light goods, truck."],
    ["Traffic counts", "Vehicles by class, per camera and hour."],
    ["ANPR report", "What was read, where, when and how sure."],
  ],
  featuresAct: [
    ["Helmet detection", "Rider without a helmet, with snapshot, one call per rider."],
    ["Wrong way + incidents", "Wrong side, possible collision, person in traffic, intrusion."],
    ["Incident review", "Snapshot-led cards; a person confirms or dismisses each."],
    ["E-challan", "Plate filled in, owner looked up, MV Act section, SMS sent."],
    ["Camera health alerts", "A camera or system that stops is told within about 40 seconds."],
    ["Footage quality alerts", "Black or broken pictures raise an alert within a minute."],
    ["Gap analysis", "Thin districts, ageing cameras, footage that is unusable."],
    ["Video access requests", "Ask another unit for its video; grant, deny or revoke."],
    ["Role + zone access", "Each user sees only their department, city and zone."],
    ["Audit log", "Every sign-in, trace, lookup and refusal, append-only."],
    ["Alert push", "Alerts sent to another system, signed, when configured."],
    ["Self-healing reader", "Restarts itself after crashes and grid outages; starts again at log-in."],
  ],
  featuresSrc: "services/central-api · services/edge-worker · services/dashboard · scripts/collect_plates.py, scripts/launchd",

  // 80,000 cameras (scenes "scale-possible", "scale-needs"). Figures: docs/scalability.md
  possible: [
    ["Statewide vehicle trace", "A named plate's route across every department's cameras, with the evidence at each point.", "Built on 30"],
    ["Pursuit across districts", "The cameras a vehicle can reach next get the GPU first; PostGIS corridors predict where to look.", "Built on 30"],
    ["Watchlist at every junction", "Stolen and wanted vehicles flagged at the moment of the read, statewide.", "Built on 30"],
    ["E-challan at state scale", "Every confirmed helmet and wrong-side offence becomes a challan, wherever it was seen.", "Built on 30"],
    ["Traffic by vehicle class", "Cars, autos, buses, light and heavy goods per junction and hour; goods vehicles in no-entry hours.", "Next"],
    ["Journey times and flows", "Origin-to-destination flows and travel times between districts, from plate reads alone.", "Next"],
  ],
  needsBig: [["~160 Gbps", "into the data centre if video were sent centrally, ~1.7 PB a day"], ["~0.6–1 Gbps", "with Vigentra: only metadata, 40–250× less"]],
  needs: [
    ["Edge compute", "~3,000 GPU nodes in department sites (1× T4-class GPU, 8 vCPU, 32 GB)", "ANPR only where the optics allow, set at onboarding"],
    ["Regional", "33 districts × 2 nodes (16 vCPU, 64 GB, 2 TB NVMe)", "adapters, an ingest buffer for WAN outages"],
    ["Central", "2 sites, primary + DR; API 6–10 replicas; PostgreSQL 2 × 32 vCPU / 256 GB / 10 TB", "lose at most 5 min of data, back within 1 h"],
    ["Storage", "No video. Metadata hot 30 days, warm 1 year, cold 7 years", "the audit log is kept in full"],
    ["Integrations", "Each department's VMS API and credentials; GSWAN to every site", "the network the sites already share"],
    ["People", "District operators; a camera-health desk; a small labelling team", "a person confirms every alert and challan"],
    ["Cost", "Edge GPUs ₹90–120 crore over the phases; software open source", "the one large line; the rest is ordinary servers"],
  ],
  scaleSrc: "docs/scalability.md §2 hardware, §3 network, §4 storage, §5 AI capacity, §7 HA/DR, §10 cost-benefit (estimates, for planning)",

  stack: [
    ["Capture", "RTSP over TCP · FFmpeg", "Reads the streams cameras already send, timed by the stream's own clock."],
    ["Vehicles", "YOLO11s + ByteTrack (YOLO11n on the live reader)", "One identity per vehicle, so its plate is decided once."],
    ["Plates", "YOLO11n, inside each vehicle", "A small search: small plates found, hoardings never proposed."],
    ["Reading", "2 CRNN readers + PaddleOCR", "Different kinds of reader make different mistakes."],
    ["Deciding", "Votes · plate rules · glyph check", "A plate is right or absent, never a guess."],
    ["Central", "FastAPI · PostgreSQL", "No video and no camera passwords at the centre."],
    ["Console", "Next.js · Leaflet · OpenStreetMap", "The login token stays out of page scripts; no map licence."],
    ["Delivery", "Docker · three image sizes", "A site installs only what it runs: 120 MB to 3.5 GB."],
  ],
  stackSrc: "docs/hld.md §3, §10 · docs/scalability.md §2 · EW/config/thresholds.yaml · TECHNICAL.md",

  // Why this, not that: [decision, what we use, instead of, why it is better]
  choices: [
    ["Where the AI runs", "Next to the video", "All video sent to one centre", "40–250× less network; no central store of footage"],
    ["When a plate is read", "Once per vehicle, from all its frames", "Each frame on its own", "The single-frame reader got 1 plate from 67 vehicles"],
    ["What may be saved", "Only what many frames agree on", "The best single reading", "36 of 48 read, 0 wrong"],
    ["Readers", "Two kinds of reader", "One OCR model", "16 of 26 plates instead of 13, still 0 wrong"],
    ["Tracker", "ByteTrack", "DeepSORT · BoT-SORT", "No appearance network or camera-motion step to pay for on fixed cameras"],
    ["Watchlist match", "Priced by real OCR errors", "Exact match", "A one-character misread still alerts, marked review"],
    ["Video for people", "Brokered, checked every segment", "Camera links in the browser", "Revoked access stops within a segment; no camera password leaves"],
  ],
  choicesSrc: "docs/scalability.md §3 · docs/anpr.md §8 · final2 REPORT.md · docs/anpr-optimisation.md · docs/hld.md §5, §7",

  // Tested and dropped, each measured on the same clips: [what we tried, what happened, what we kept]
  dropped: [
    ["Confirm on 3 crops instead of 4", "A wrong plate confirmed on Delhi", "4 crops"],
    ["The best 20 crops instead of 12", "No more plates, more reading", "12 crops"],
    ["Two CRNNs backing each other", "They share mistakes", "Only another kind of reader backs"],
    ["Frames enlarged to 1920 px", "Half the vehicles, 1.5–9× slower", "Each frame's own size"],
    ["Plate box up to 0.9 of its vehicle", "A hoarding read as a plate", "0.60 of the vehicle"],
    ["One frame at a time (EasyOCR)", "1 plate from 67 vehicles", "Reading whole tracks"],
  ],
  droppedSrc: "docs/anpr-optimisation.md (tested and rejected) · EW/config/thresholds.yaml · docs/anpr.md §8 · docs/submission.md (16 Sep)",

  // Paced like a live camera (frames arriving while busy are dropped), one Apple M1
  speed: {
    machine: "one Apple M1 laptop",
    rows: [{ res: "854 × 480", ms: 58, rt: 1.31 }, { res: "720p", ms: 96, rt: 1.0 }, { res: "1080p", ms: 173, rt: 0.92 }],
    src: "docs/anpr-optimisation.md (anpr_benchmark run --realtime)",
  },
};
