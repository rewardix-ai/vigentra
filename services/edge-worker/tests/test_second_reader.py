"""The second reader named in thresholds.yaml must actually reach the engine.

`reading.extra_crnn_weights` names a fallback CRNN that fills in a read on
tracks the first reader cannot decide (vendor replay: cam06 3/5 -> 5/5 read,
0 wrong confirms). The adapter passes `reader_weights` explicitly, which skips
the engine's own lookup of that key - so while it passed only the primary
reader, the second one was configured, shipped, and never loaded.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("yaml")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import anpr_engine  # noqa: E402


@pytest.fixture
def engine_env(tmp_path, monkeypatch):
    models, config = tmp_path / "models", tmp_path / "config"
    models.mkdir()
    config.mkdir()
    for name in ("yolo11s.pt", "plate_det_mix_n.pt", "reader_crnn.onnx"):
        (models / name).write_bytes(b"")
    (config / "roi.yaml").write_text("{}\n")
    (config / "thresholds.yaml").write_text(
        "reading:\n  extra_crnn_weights: [models/reader_crnn_v6.onnx]\n"
    )
    monkeypatch.setattr(anpr_engine, "MODELS_DIR", models)
    monkeypatch.setattr(anpr_engine, "CONFIG_DIR", config)

    built: list[dict] = []

    class FakePipeline:
        def __init__(self, **kwargs):
            built.append(kwargs)

    stub = types.ModuleType("anpr.pipeline")
    stub.ANPRPipeline = FakePipeline
    monkeypatch.setitem(sys.modules, "anpr.pipeline", stub)
    return models, built


def test_the_configured_second_reader_is_passed_to_the_engine(engine_env):
    models, built = engine_env
    (models / "reader_crnn_v6.onnx").write_bytes(b"")

    engine = anpr_engine.AnprEngine(camera_id="cam")

    assert built[0]["reader_weights"] == [
        str(models / "reader_crnn.onnx"),
        str(models / "reader_crnn_v6.onnx"),
    ]
    assert engine.describe()["readers"] == ["reader_crnn.onnx", "reader_crnn_v6.onnx"]


def test_a_missing_second_reader_is_skipped_not_fatal(engine_env):
    models, built = engine_env

    anpr_engine.AnprEngine(camera_id="cam")

    assert built[0]["reader_weights"] == [str(models / "reader_crnn.onnx")]
