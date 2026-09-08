"""The plate reader's shared pieces: geometry, decode, fusion, and the floor."""
from __future__ import annotations

import numpy as np
import pytest

from anpr import reader as rd
from anpr.readability import ReadabilityLedger, HARD_FLOOR_PX


def test_preprocess_keeps_aspect_and_pads_with_median():
    crop = np.full((20, 100, 3), 200, np.uint8)
    x = rd.preprocess(crop)
    assert x.shape == (1, rd.INPUT_H, rd.INPUT_W)
    # 100x20 -> 160x32: the right 32 columns are padding at the median level.
    assert abs(float(x[0, :, 170:].mean()) - 200 / 255) < 1e-3
    assert abs(float(x[0, :, :160].mean()) - 200 / 255) < 1e-3


def test_preprocess_squeezes_rather_than_cuts_wide_crops():
    crop = np.zeros((10, 400), np.uint8)
    crop[:, -10:] = 255                       # a bright last "character"
    x = rd.preprocess(crop)
    assert float(x[0, :, -4:].mean()) > 0.5   # still present at the right edge


def _matrix(tokens: list[int], T: int = 48) -> np.ndarray:
    """A log-prob matrix that emits `tokens` (0 = blank) in order."""
    m = np.full((T, rd.NUM_CLASSES), -20.0, np.float32)
    for t in range(T):
        m[t, tokens[t] if t < len(tokens) else 0] = 0.0
    return m


def test_greedy_decode_collapses_repeats_and_blanks():
    G, J, zero, one = (rd.CHARSET.index(c) + 1 for c in "GJ01")
    text, conf = rd.greedy_decode(_matrix([G, G, 0, J, 0, zero, zero, 0, 0, one, one, one]))
    assert text == "GJ01"
    assert conf == pytest.approx(1.0)


def test_greedy_decode_empty_when_all_blank():
    assert rd.greedy_decode(_matrix([])) == ("", 0.0)


def test_fusion_lets_one_confident_frame_win_a_slot():
    A, B = rd.CHARSET.index("A") + 1, rd.CHARSET.index("B") + 1
    sure = _matrix([A, 0, B])
    # An undecided frame: A and B equally likely at t=0, B sure at t=2.
    unsure = _matrix([0, 0, B])
    unsure[0, [A, B]] = np.log(0.5)
    unsure[0, 0] = -20.0
    fused = rd.fuse_log_probs([sure, unsure])
    assert fused is not None and fused.shape == sure.shape
    assert rd.greedy_decode(fused)[0] == "AB"
    # Per timestep it is still a distribution.
    assert np.allclose(np.exp(fused).sum(axis=1), 1.0, atol=1e-4)


def test_fusion_refuses_mismatched_shapes():
    assert rd.fuse_log_probs([_matrix([1], 48), _matrix([1], 40)]) is None


def test_ledger_floor_is_configurable():
    default = ReadabilityLedger()
    assert not default.observe(1, HARD_FLOOR_PX - 1)
    lowered = ReadabilityLedger(floor_px=24.0)
    assert lowered.observe(1, 30.0)
    assert not lowered.observe(1, 20.0)
    assert lowered.below_floor == 1


def test_model_forward_shape():
    torch = pytest.importorskip("torch")
    model = rd.build_model()
    x = torch.zeros(2, 1, rd.INPUT_H, rd.INPUT_W)
    with torch.no_grad():
        out = model(x)
    assert tuple(out.shape) == (2, rd.TIME_STEPS, rd.NUM_CLASSES)
    assert torch.allclose(out.exp().sum(-1), torch.ones(2, rd.TIME_STEPS), atol=1e-4)
