"""The plate upscaler: shape contract and the enhancer's routing."""
from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from anpr import sr  # noqa: E402


def test_model_upscales_by_scale_and_stays_in_range():
    model = sr.build_model()
    x = torch.rand(2, 3, 12, 40)
    with torch.no_grad():
        y = model(x)
    assert tuple(y.shape) == (2, 3, 48, 160)
    assert float(y.min()) >= 0.0 and float(y.max()) <= 1.0


def test_tensor_round_trip():
    img = (np.random.default_rng(0).random((10, 30, 3)) * 255).astype(np.uint8)
    back = sr.to_image(sr.to_tensor(img))
    assert back.shape == img.shape
    assert int(np.abs(back.astype(int) - img.astype(int)).max()) <= 1


def test_upscaler_loads_a_checkpoint(tmp_path):
    model = sr.build_model()
    ck = tmp_path / "plate_sr.pt"
    torch.save({"model": model.state_dict(), "spec": sr.SrSpec().__dict__, "epoch": 1}, ck)
    up = sr.PlateUpscaler(str(ck), "cpu")
    out = up.upscale(np.full((9, 33, 3), 120, np.uint8))
    assert out.shape == (36, 132, 3)
    assert up.name == "plate_sr_x4"


def test_enhancer_prefers_plate_weights_when_present(tmp_path, monkeypatch):
    from anpr import enhance
    model = sr.build_model()
    torch.save({"model": model.state_dict(), "spec": sr.SrSpec().__dict__}, tmp_path / "plate_sr.pt")
    monkeypatch.setattr(enhance, "MODELS_DIR", tmp_path)
    res = enhance.SuperResolver("auto", 4)
    assert res.name == "plate_sr_x4"
    out = res.upscale(np.full((8, 32, 3), 90, np.uint8))
    assert out.shape == (32, 128, 3)
    # Without weights the resolver still works.
    monkeypatch.setattr(enhance, "MODELS_DIR", tmp_path / "empty")
    res2 = enhance.SuperResolver("plate", 4)
    assert res2.name == "lanczos"
    assert res2.upscale(np.full((8, 32, 3), 90, np.uint8)).shape == (32, 128, 3)
