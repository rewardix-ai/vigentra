"""Detection pipeline tests (spec items 21-29).

Covers the detector abstraction, threshold filtering, ingestion validation,
idempotency, provenance, the health endpoint and the frame-quality router.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def payload_for(detectors, camera_id: str, **overrides) -> list[dict]:
    detector = detectors.MockDetector(confidence_threshold=0.45)
    stamp = iso(datetime.now(timezone.utc))
    return [
        detection.to_payload(camera_id, stamp, index, source_mode="mock", **overrides)
        for index, detection in enumerate(detector.detect(None))
    ]


# 21. Mock detector returns valid detections
async def test_mock_detector_returns_valid_detections(detectors):
    detector = detectors.MockDetector(confidence_threshold=0.45)
    results = detector.detect(None)

    assert results, "mock detector must produce detections"
    for detection in results:
        assert detection.class_name in detectors.DETECTION_CLASSES
        assert 0.0 <= detection.confidence <= 1.0
        x1, y1, x2, y2 = detection.bbox_xyxy
        assert x2 > x1 and y2 > y1
        assert all(value >= 0 for value in detection.bbox_xyxy)
        assert detection.model_name and detection.model_version

    # Deterministic for a given frame index, so tests can assert on it.
    assert [d.class_name for d in detectors.MockDetector().detect(None)] == [
        d.class_name for d in detectors.MockDetector().detect(None)
    ]


# 22. Confidence threshold filters low-confidence detections
async def test_confidence_threshold_filters(detectors):
    permissive = detectors.MockDetector(confidence_threshold=0.10).detect(None)
    default = detectors.MockDetector(confidence_threshold=0.45).detect(None)
    strict = detectors.MockDetector(confidence_threshold=0.90).detect(None)

    assert len(permissive) > len(default) > len(strict)
    assert all(d.confidence >= 0.45 for d in default)
    assert all(d.confidence >= 0.90 for d in strict)


# 23. Invalid bounding boxes are rejected
@pytest.mark.parametrize(
    "bbox,reason",
    [
        ([500, 400, 100, 100], "x2 <= x1"),
        ([10, 400, 200, 100], "y2 <= y1"),
        ([-5, 10, 200, 300], "negative coordinate"),
        ([10, 10, 10, 300], "zero width"),
    ],
)
async def test_invalid_bboxes_are_rejected(api, login, traffic_camera, detectors, bbox, reason):
    headers = await login("traffic.ai")
    detection = payload_for(detectors, traffic_camera["camera_id"])[0]
    detection["bbox_xyxy"] = bbox
    detection["detection_id"] = f"det_bad_{abs(hash(reason))}"

    response = await api.post(
        "/api/v1/detections/ingest", headers=headers, json={"detections": [detection]}
    )
    assert response.status_code == 422, f"{reason} should be refused"


async def test_out_of_range_confidence_is_rejected(api, login, traffic_camera, detectors):
    headers = await login("traffic.ai")
    detection = payload_for(detectors, traffic_camera["camera_id"])[0]
    detection["confidence"] = 1.5

    response = await api.post(
        "/api/v1/detections/ingest", headers=headers, json={"detections": [detection]}
    )
    assert response.status_code == 422


async def test_out_of_scope_class_is_rejected(api, login, traffic_camera, detectors):
    """Phase scope is enforced at the schema, not by convention."""
    headers = await login("traffic.ai")
    detection = payload_for(detectors, traffic_camera["camera_id"])[0]
    detection["class_name"] = "license_plate"

    response = await api.post(
        "/api/v1/detections/ingest", headers=headers, json={"detections": [detection]}
    )
    assert response.status_code == 422


# 24. Unknown camera detections are rejected
async def test_unknown_camera_detections_are_rejected(api, login, detectors):
    headers = await login("traffic.ai")
    detections = payload_for(detectors, "VIGENTRA-NOPE-XXX-9999")

    body = (
        await api.post(
            "/api/v1/detections/ingest", headers=headers, json={"detections": detections}
        )
    ).json()

    assert body["accepted"] == 0
    assert body["rejected"] == len(detections)
    assert body["errors"][0]["code"] == "UNKNOWN_CAMERA"


async def test_partial_batch_accepts_the_good_rows(api, login, traffic_camera, detectors):
    """One bad row must not discard the rest of the batch."""
    headers = await login("traffic.ai")
    good = payload_for(detectors, traffic_camera["camera_id"])
    bad = dict(good[0])
    bad["detection_id"] = "det_unknown_camera_row"
    bad["camera_id"] = "VIGENTRA-NOPE-XXX-0001"

    body = (
        await api.post(
            "/api/v1/detections/ingest",
            headers=headers,
            json={"detections": good + [bad]},
        )
    ).json()

    assert body["accepted"] == len(good)
    assert body["rejected"] == 1


# 25. Missing weights return a useful error
async def test_missing_weights_raises_actionable_error(detectors, tmp_path):
    detector = detectors.UltralyticsYoloDetector(
        model_name="definitely-not-here.pt",
        weights_dir=str(tmp_path),
        allow_download=False,
    )
    with pytest.raises(detectors.DetectorError) as excinfo:
        detector.load()

    message = str(excinfo.value)
    # Either ultralytics is absent, or the weights are - both must explain the fix.
    assert "YOLO_ENABLE=false" in message or "pip install" in message
    assert "docs/yolo-setup.md" in message or "requirements" in message


async def test_detector_factory_falls_back_to_mock(detectors):
    detector = detectors.build_detector(enabled=False)
    assert isinstance(detector, detectors.MockDetector)
    assert detector.detect(None)


# 26. Detector health endpoint works
async def test_detector_health_endpoint(api, login):
    headers = await login("traffic.ai")
    body = (await api.get("/api/v1/detector/health", headers=headers)).json()

    assert set(body["classes"]) >= {"person", "car", "motorcycle", "bus", "truck"}
    assert 0.0 <= body["confidence_threshold"] <= 1.0
    assert body["frame_sample_interval"] >= 1
    # The probabilistic disclaimer ships in the payload, not only in docs.
    assert "probabilistic" in body["accuracy_disclaimer"].lower()


# 27. Model provenance is stored
async def test_model_provenance_is_stored(api, login, traffic_camera, detectors):
    headers = await login("traffic.ai")
    detections = payload_for(detectors, traffic_camera["camera_id"])

    result = (
        await api.post(
            "/api/v1/detections/ingest", headers=headers, json={"detections": detections}
        )
    ).json()
    assert result["accepted"] == len(detections)
    assert result["model_name"] == "mock_detector"

    stored = (await api.get("/api/v1/detections", headers=headers)).json()
    assert stored
    record = stored[0]
    assert record["model_name"] == "mock_detector"
    assert record["model_version"] == "1.0.0"
    assert record["source_mode"] == "mock"
    assert record["is_demo_data"] is True
    assert record["provenance"]["submitted_by"] == "traffic.ai"
    assert record["provenance"]["source_system"] == traffic_camera["source_system"]

    # The health endpoint now reports the build that reported in.
    health = (await api.get("/api/v1/detector/health", headers=headers)).json()
    assert health["weights_available"] is True
    assert "mock_detector" in health["detail"]


# 28. Detection ingestion is idempotent
async def test_detection_ingestion_is_idempotent(api, login, traffic_camera, detectors):
    headers = await login("traffic.ai")
    detections = payload_for(detectors, traffic_camera["camera_id"])

    first = (
        await api.post(
            "/api/v1/detections/ingest", headers=headers, json={"detections": detections}
        )
    ).json()
    second = (
        await api.post(
            "/api/v1/detections/ingest", headers=headers, json={"detections": detections}
        )
    ).json()

    assert first["accepted"] == len(detections) and first["duplicates"] == 0
    assert second["accepted"] == 0 and second["duplicates"] == len(detections)

    stored = (await api.get("/api/v1/detections", headers=headers)).json()
    assert len(stored) == len(detections), "replay must not inflate the count"


async def test_detection_ingest_requires_permission(api, login, traffic_camera, detectors):
    detections = payload_for(detectors, traffic_camera["camera_id"])
    for username in ("health.monitor", "registry.viewer", "auditor"):
        headers = await login(username)
        response = await api.post(
            "/api/v1/detections/ingest", headers=headers, json={"detections": detections}
        )
        assert response.status_code == 403, f"{username} must not be able to ingest"


async def test_detections_are_scoped_by_department(api, login, traffic_camera, detectors):
    ai_headers = await login("traffic.ai")
    detections = payload_for(detectors, traffic_camera["camera_id"])
    await api.post(
        "/api/v1/detections/ingest", headers=ai_headers, json={"detections": detections}
    )

    # The municipal operator may not read Traffic detections.
    municipal = await login("municipal.operator")
    visible = (await api.get("/api/v1/detections", headers=municipal)).json()
    assert all(d["camera_id"] != traffic_camera["camera_id"] for d in visible)

    per_camera = await api.get(
        f"/api/v1/cameras/{traffic_camera['camera_id']}/detections", headers=municipal
    )
    assert per_camera.status_code == 403


# 29. Frame-quality router classifies basic cases
async def test_frame_quality_router_classifies(frame_quality):
    numpy = pytest.importorskip("numpy")
    router = frame_quality.FrameQualityRouter()

    def textured(brightness: int):
        frame = numpy.full((240, 320, 3), brightness, dtype="uint8")
        frame[40:90, 40:160] = min(255, brightness + 60)
        frame[120:180, 180:280] = max(0, brightness - 50)
        return frame

    _, normal = router.route(textured(128))
    assert normal.quality is frame_quality.FrameQuality.NORMAL
    assert normal.inference_skipped is False

    _, dark = router.route(textured(25))
    assert dark.quality is frame_quality.FrameQuality.LOW_LIGHT

    _, bright = router.route(numpy.full((240, 320, 3), 252, dtype="uint8"))
    assert bright.quality is frame_quality.FrameQuality.OVEREXPOSED

    _, flat = router.route(numpy.full((240, 320, 3), 128, dtype="uint8"))
    assert flat.quality is frame_quality.FrameQuality.BLURRED
    assert flat.inference_skipped is True


async def test_exposure_is_classified_before_blur(frame_quality):
    """Darkness and clipping both depress edge variance.

    Checking blur first would mislabel every dark and every glared frame as
    "blurred" and route them away from the enhancement that might have helped.
    This asserts the ordering that fixes it.
    """
    numpy = pytest.importorskip("numpy")
    router = frame_quality.FrameQualityRouter()

    # Low edge energy AND dark -> low_light, not blurred.
    dark = numpy.full((240, 320, 3), 20, dtype="uint8")
    dark[100:140, 100:220] = 45
    _, assessment = router.route(dark)
    assert assessment.quality is frame_quality.FrameQuality.LOW_LIGHT
    assert assessment.laplacian_variance < router.blur_variance

    # Zero edge energy AND clipped -> overexposed, not blurred.
    _, glare = router.route(numpy.full((240, 320, 3), 255, dtype="uint8"))
    assert glare.quality is frame_quality.FrameQuality.OVEREXPOSED


async def test_overexposed_frames_are_not_enhanced(frame_quality):
    """Clipped highlights carry no recoverable signal.

    Enhancing them would invent plausible texture, which is worse than useless
    as evidence - so detection runs on the original and the frame is flagged.
    """
    numpy = pytest.importorskip("numpy")
    router = frame_quality.FrameQualityRouter()
    original = numpy.full((240, 320, 3), 252, dtype="uint8")

    routed, assessment = router.route(original)
    assert assessment.quality is frame_quality.FrameQuality.OVEREXPOSED
    assert assessment.enhancement_applied is None
    assert numpy.array_equal(routed, original), "must detect on the original frame"


async def test_frame_quality_events_can_be_recorded(api, login, traffic_camera, frame_quality):
    headers = await login("traffic.ai")
    response = await api.post(
        "/api/v1/detections/frame-quality",
        headers=headers,
        json={
            "camera_id": traffic_camera["camera_id"],
            "quality": "low_light",
            "mean_luma": 27.7,
            "laplacian_variance": 38.1,
            "enhancement_applied": "clahe_lab_l",
            "inference_skipped": False,
        },
    )
    assert response.status_code == 202
    assert response.json()["recorded"] is True


# ---------------------------------------------------------------------------
# The supervisor: many cameras, continuously
# ---------------------------------------------------------------------------

def _load_worker():
    """Load the edge worker without putting its `app` package on sys.path.

    The worker and the central API both use the package name `app`, so a plain
    import would resolve to whichever won the path race. The worker also uses
    relative imports, so a bare file load is not enough either - it needs a
    real parent package. Synthesising one with `__path__` gives it both.
    """
    import importlib.util
    import sys
    import types
    from pathlib import Path

    package = "vigentra_edge"
    app_dir = Path(__file__).resolve().parents[1] / "services" / "edge-worker" / "app"

    if package not in sys.modules:
        parent = types.ModuleType(package)
        parent.__path__ = [str(app_dir)]
        sys.modules[package] = parent

    name = f"{package}.worker"
    if name in sys.modules:
        return sys.modules[name]

    spec = importlib.util.spec_from_file_location(name, app_dir / "worker.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _FakeClient:
    """Stands in for the registry so discovery can be tested without a server."""

    def __init__(self, cameras):
        self._cameras = cameras

    def list_cameras(self):
        return self._cameras


async def test_explicit_cameras_are_deduplicated():
    worker = _load_worker()
    resolved = worker.resolve_cameras(["CAM-1", "CAM-2", "CAM-1"], False, _FakeClient([]))
    # (canonical, external) pairs: the external ID is what tells the worker
    # whether a camera is on the live grid, so it travels with the target.
    assert [camera_id for camera_id, _ in resolved] == ["CAM-1", "CAM-2"], (
        "a repeated --camera must not be processed twice"
    )


async def test_discovery_only_returns_cameras_the_worker_may_watch():
    """`--all-cameras` is convenience, not escalation.

    Discovery reads the registry, which is federation-wide, so it must filter
    on the per-camera video decision. Otherwise a worker would open sessions
    against every camera in the state and collect a wall of audited denials.
    """
    worker = _load_worker()
    client = _FakeClient([
        {"camera_id": "CAM-LIVE", "video_access": "live_and_playback"},
        {"camera_id": "CAM-LIVE-ONLY", "video_access": "live_only"},
        {"camera_id": "CAM-PLAYBACK", "video_access": "playback_only"},
        {"camera_id": "CAM-ASK", "video_access": "needs_unit_approval"},
        {"camera_id": "CAM-OFF", "video_access": "not_enabled_by_owner"},
        {"camera_id": "CAM-DEAD", "video_access": "camera_unavailable"},
    ])
    resolved = worker.resolve_cameras(None, True, client)
    ids = [camera_id for camera_id, _ in resolved]

    assert ids == ["CAM-LIVE", "CAM-LIVE-ONLY"]
    # Playback-only is excluded deliberately: the worker samples a live feed,
    # and asking for playback would need a time window it has no basis to pick.
    assert "CAM-PLAYBACK" not in ids
    assert "CAM-ASK" not in ids


async def test_no_cameras_requested_is_an_error_not_a_silent_pass():
    worker = _load_worker()
    assert worker.resolve_cameras(None, False, _FakeClient([])) == []


async def test_live_runs_refuse_to_fall_back_to_synthetic_frames():
    """The live path must decode the session it opened.

    It used to open a real video session and then quietly feed itself
    generated test patterns, so every "live" detection was inference on a
    gradient. Now a run with no clip, no session and no --synthetic fails
    loudly instead of inventing frames.
    """
    worker = _load_worker()
    with pytest.raises(Exception) as excinfo:
        worker.run(
            camera_id="VIGENTRA-TRAFFIC-AHM-0001",
            clip=None,
            max_frames=1,
            sample_interval=1,
            dry_run=True,      # no client, so no session can be opened
            synthetic=False,
            source_mode="demo_local",
        )
    assert "No frame source" in str(excinfo.value)


# ---------------------------------------------------------------------------
# ANPR: number-plate reading
# ---------------------------------------------------------------------------

def _load_plates():
    import importlib.util
    import sys
    import types
    from pathlib import Path

    package = "vigentra_edge"
    app_dir = Path(__file__).resolve().parents[1] / "services" / "edge-worker" / "app"
    if package not in sys.modules:
        parent = types.ModuleType(package)
        parent.__path__ = [str(app_dir)]
        sys.modules[package] = parent
    name = f"{package}.plates"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, app_dir / "plates.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("GJ01AB1234", "GJ01AB1234"),
        ("gj 01 ab 1234", "GJ01AB1234"),
        ("GJ-05-CD-4321", "GJ05CD4321"),
        ("MH12DE5678", "MH12DE5678"),
        ("DL8CAF5031", "DL8CAF5031"),
        # OCR read the number's leading zero as a letter O. Fixable by position.
        ("GJ01AB1O34", "GJ01AB1034"),
    ],
)
async def test_plausible_plates_are_normalised(raw, expected):
    assert _load_plates().normalise_plate(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "HELLO",              # ordinary text on a vehicle
        "12",                 # too short
        "GJ01AB",             # no number
        "GJ01AB12345678",     # too long
        "0J01AB1234",         # "OJ" is not a state code
        "XX01AB1234",         # nor is XX
        "",
    ],
)
async def test_implausible_reads_are_discarded(raw):
    """Half-read text is worse than none: it looks like evidence and is not."""
    assert _load_plates().normalise_plate(raw) is None


async def test_only_vehicles_are_examined():
    """A person is never cropped for text."""
    plates = _load_plates()
    assert "person" not in plates.PLATE_BEARING_CLASSES
    assert "bicycle" not in plates.PLATE_BEARING_CLASSES
    assert {"car", "motorcycle", "bus", "truck"} <= plates.PLATE_BEARING_CLASSES


async def test_anpr_is_off_unless_explicitly_enabled(monkeypatch):
    plates = _load_plates()
    monkeypatch.delenv("ANPR_ENABLE", raising=False)
    assert isinstance(plates.build_plate_reader(), plates.DisabledPlateReader)
    monkeypatch.setenv("ANPR_ENABLE", "true")
    assert isinstance(plates.build_plate_reader(), plates.EasyOcrPlateReader)


async def test_a_crop_too_small_to_hold_a_plate_is_skipped():
    """Upscaling a 12-pixel strip manufactures confident nonsense."""
    numpy = pytest.importorskip("numpy")
    plates = _load_plates()
    frame = numpy.zeros((200, 200, 3), dtype=numpy.uint8)
    assert plates.plate_region(frame, [10.0, 10.0, 18.0, 18.0]) is None

    region = plates.plate_region(frame, [10.0, 10.0, 150.0, 120.0])
    assert region is not None
    crop, (offset_x, offset_y) = region
    # The crop is the lower part of the vehicle, offset reported in frame space.
    assert offset_x == 10
    assert offset_y > 10
    assert crop.shape[0] > 0 and crop.shape[1] > 0


async def test_plate_is_withheld_without_the_permission(api, login, traffic_camera):
    """A plate is personal data, so it has its own permission.

    The row is still returned - an operator counting vehicles has a legitimate
    need for the detection and none for the registration number. Withholding
    the field is the right granularity, not refusing the row.
    """
    from datetime import datetime, timezone

    ingest = await login("traffic.ai")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "detections": [
            {
                "detection_id": "det_plate_rbac_probe",
                "camera_id": traffic_camera["camera_id"],
                "timestamp_utc": stamp,
                "class_name": "car",
                "class_id": 2,
                "confidence": 0.91,
                "bbox_xyxy": [10.0, 10.0, 200.0, 160.0],
                "model_name": "ultralytics-yolo",
                "model_version": "yolo11n.pt/ultralytics-8.4.123",
                "source_mode": "demo_local",
                "plate_text": "GJ01AB1234",
                "plate_confidence": 0.72,
                "plate_bbox_xyxy": [40.0, 120.0, 150.0, 155.0],
                "plate_reader": "easyocr-1.7.2",
            }
        ]
    }
    accepted = await api.post("/api/v1/detections/ingest", headers=ingest, json=payload)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["accepted"] == 1

    def find(rows):
        return next((r for r in rows if r["detection_id"] == "det_plate_rbac_probe"), None)

    # ai_operator ingests plates but has no business reading them back.
    without = find((await api.get("/api/v1/detections?limit=500", headers=ingest)).json())
    assert without is not None, "the detection itself must still be visible"
    assert without["plate_text"] is None
    assert without["plate_withheld"] is True

    # A traffic operator investigating an incident may.
    with_perm = await login("traffic.operator")
    got = find((await api.get("/api/v1/detections?limit=500", headers=with_perm)).json())
    assert got is not None
    assert got["plate_text"] == "GJ01AB1234"
    assert got["plate_withheld"] is False
    assert got["plate_confidence"] == 0.72


async def test_reading_a_plate_is_audited(api, login, traffic_camera):
    """Learning which vehicle it was is a separate act from seeing one pass.

    Ingests its own row: the database is rebuilt per test, so leaning on data
    another test happened to leave behind would pass or fail depending on
    execution order.
    """
    from datetime import datetime, timezone

    ingest = await login("traffic.ai")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await api.post(
        "/api/v1/detections/ingest",
        headers=ingest,
        json={
            "detections": [
                {
                    "detection_id": "det_plate_audit_probe",
                    "camera_id": traffic_camera["camera_id"],
                    "timestamp_utc": stamp,
                    "class_name": "car",
                    "class_id": 2,
                    "confidence": 0.9,
                    "bbox_xyxy": [1.0, 1.0, 60.0, 60.0],
                    "model_name": "ultralytics-yolo",
                    "model_version": "yolo11n.pt/ultralytics-8.4.123",
                    "plate_text": "GJ05CD4321",
                    "plate_confidence": 0.8,
                    "plate_reader": "easyocr-1.7.2",
                }
            ]
        },
    )

    headers = await login("traffic.operator")
    seen = (await api.get("/api/v1/detections?limit=500", headers=headers)).json()
    assert any(row.get("plate_text") == "GJ05CD4321" for row in seen)

    entries = (await api.get("/api/v1/audit?limit=200", headers=await login("auditor"))).json()
    disclosures = [
        entry for entry in entries
        if entry["action"] == "plate_data_viewed" and entry["username"] == "traffic.operator"
    ]
    assert disclosures, "plate disclosure must be recorded"
    assert disclosures[0]["details"]["plates_disclosed"] >= 1


async def test_listing_without_plates_records_no_disclosure(api, login, traffic_camera):
    """The audit line means something, so it must not fire on every listing."""
    ingest = await login("traffic.ai")
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await api.post(
        "/api/v1/detections/ingest",
        headers=ingest,
        json={
            "detections": [
                {
                    "detection_id": "det_no_plate_probe",
                    "camera_id": traffic_camera["camera_id"],
                    "timestamp_utc": stamp,
                    "class_name": "person",
                    "class_id": 0,
                    "confidence": 0.9,
                    "bbox_xyxy": [1.0, 1.0, 40.0, 90.0],
                    "model_name": "ultralytics-yolo",
                    "model_version": "yolo11n.pt/ultralytics-8.4.123",
                }
            ]
        },
    )

    headers = await login("traffic.operator")
    await api.get("/api/v1/detections?limit=500", headers=headers)

    entries = (await api.get("/api/v1/audit?limit=200", headers=await login("auditor"))).json()
    assert not [e for e in entries if e["action"] == "plate_data_viewed"], (
        "no plates were disclosed, so nothing should claim they were"
    )


async def test_a_plate_split_across_ocr_boxes_is_reassembled():
    """Observed on real footage, and it silently cost every plate.

    EasyOCR returned one Delhi plate as two boxes, 'DL' and '1LCE5987'. Judged
    separately neither is a registration number, so the reader accepted nothing
    at all while the engine was plainly reading plates.
    """
    plates = _load_plates()
    fragments = [
        ([[10, 5], [40, 5], [40, 25], [10, 25]], "DL", 0.80),
        ([[45, 6], [160, 6], [160, 26], [45, 26]], "1LCE5987", 0.77),
    ]
    lines = plates.assemble_lines(fragments)
    assert len(lines) == 1, "fragments on one text line must join"
    text, confidence, _ = lines[0]
    assert text == "DL1LCE5987"
    # A line is only as trustworthy as its worst-read fragment.
    assert confidence == pytest.approx(0.77)
    assert plates.normalise_plate(text) == "DL1LCE5987"


async def test_fragments_on_different_lines_are_not_joined():
    """Two plates in one crop must not be concatenated into a third."""
    plates = _load_plates()
    fragments = [
        ([[10, 5], [120, 5], [120, 25], [10, 25]], "GJ01AB1234", 0.9),
        ([[10, 80], [120, 80], [120, 100], [10, 100]], "MH12DE5678", 0.9),
    ]
    lines = plates.assemble_lines(fragments)
    assert {text for text, _, _ in lines} == {"GJ01AB1234", "MH12DE5678"}


async def test_segmentation_prefers_the_reading_needing_fewest_corrections():
    """Where a segment boundary falls is genuinely ambiguous in a bare string.

    `GJ01AB1234` wants a two-digit district; `DL1LCE5987` wants one. Reading
    either greedily corrupts the other - greedy district turns the series
    letter L into a digit and yields DL11CE5987, a different vehicle.
    """
    normalise = _load_plates().normalise_plate
    assert normalise("GJ01AB1234") == "GJ01AB1234"
    assert normalise("DL1LCE5987") == "DL1LCE5987"


async def test_the_state_code_must_be_a_real_state():
    """What stops a plate-shaped misread being stored as a registration."""
    normalise = _load_plates().normalise_plate
    assert normalise("GJ01AB1234") is not None
    for fake in ("XX01AB1234", "0J01AB1234", "QZ01AB1234"):
        assert normalise(fake) is None, f"{fake} is not a registration"


async def test_single_detection_and_per_camera_routes_answer(api, login, traffic_camera, detectors):
    """Both routes referenced an undefined name and answered 500 on every call."""
    edge = await login("traffic.ai")
    detection = payload_for(detectors, traffic_camera["camera_id"])[0]
    ingested = await api.post(
        "/api/v1/detections/ingest", headers=edge, json={"detections": [detection]}
    )
    assert ingested.status_code in (200, 201), ingested.text

    reader = await login("traffic.state")
    listed = await api.get(
        "/api/v1/detections", headers=reader, params={"camera_id": traffic_camera["camera_id"]}
    )
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert rows, "the ingested detection should be listed"

    one = await api.get(f"/api/v1/detections/{rows[0]['detection_id']}", headers=reader)
    assert one.status_code == 200, one.text

    per_camera = await api.get(
        f"/api/v1/cameras/{traffic_camera['camera_id']}/detections", headers=reader
    )
    assert per_camera.status_code == 200, per_camera.text
    assert per_camera.json()
