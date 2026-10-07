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
    ["Works on the cameras you have", "Reads the streams the department already runs; nothing is installed at the camera.", "RTSP · HLS fallback", "EW/app/grid.py"],
    ["It doesn't guess", "A plate is saved only when many frames agree, in a valid Indian format.", "0 wrong · 36 of 48 read", "final2 benchmark"],
    ["Video stays with its owner", "Plates travel as text; another unit sees video only with the owner's permission.", "text only", "worker.py · central-api access model"],
    ["Built for Indian plates", "Standard, Delhi short, BH series, diplomatic, vintage and temporary formats.", "40 state codes · 39 Gujarat RTOs", "EW/anpr/plate_grammar.py · config/india_codes.yaml"],
    ["Every act on record", "Searches, traces, reports and alerts are audited; alerts go out signed.", "audit log · HMAC-signed", "docs/scalability.md · alert_webhook.py"],
    ["Knows when a camera goes dark", "A camera that misses two health checks in a row raises an alert, and clears itself on recovery.", "about 40 s", "services/health_alerts.py"],
  ],

  // What we intend to do next: each starts from a limit the project's own reports record.
  roadmap: [
    ["GPU servers at the edge", "One laptop keeps pace with one camera today; every grid feed live needs GPU hosts.", "Next"],
    ["No record lost offline", "Hold plate records through a network outage and replay them; ingest already ignores duplicates.", "Planned"],
    ["Indian vehicle types", "The current model has no auto-rickshaw or e-rickshaw class; train one on our own footage.", "Planned"],
    ["Night and small plates", "Camera placement and IR guidance, and a small-plate detector trained on human-checked hard cases.", "Planned"],
    ["State-scale monitoring", "Dashboards and alerts for cameras offline, ingest lag and reader speed.", "Planned"],
    ["Recovery targets", "Design goals, not yet tested at scale: lose at most 5 minutes of data, back within an hour.", "Planned"],
  ],
  roadmapSrc: "docs/scalability.md (edge buffering, monitoring, HA/DR targets) · known limits from the benchmark and the CAM06 day study",

  // Under the hood: each layer, what it runs on, and why. TECHNICAL.md has the full reasoning.
  stack: [
    ["Capture", "RTSP over TCP · FFmpeg", "Reads the streams cameras already send, timed by the stream's own clock."],
    ["Vehicles", "YOLO11s + ByteTrack", "One identity per vehicle, so its plate is decided once."],
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
