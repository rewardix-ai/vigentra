import numpy as np

from app.frame_quality import FrameQuality, FrameQualityRouter


def _frame(edge: float, seed: int = 0) -> np.ndarray:
    """A noisy grey frame with extra edge strength on the 16-px grid (a blocky encoder or damage)."""
    rng = np.random.default_rng(seed)
    img = rng.normal(120, 6, (720, 1280)).astype(np.float32)
    img[:, ::16] += edge
    img[::16, :] += edge
    return np.clip(img, 0, 255).astype(np.uint8)


def test_a_camera_that_is_always_a_little_blocky_is_not_called_corrupted():
    r = FrameQualityRouter()
    calls = [r.classify(_frame(12 + (i % 3), i)).quality for i in range(40)]   # 1.87-2.12, like cam10
    assert FrameQuality.CORRUPTED not in calls


def test_damage_far_above_the_cameras_own_level_is_corrupted():
    r = FrameQualityRouter()
    for i in range(30):
        r.classify(_frame(1, i))
    assert r.classify(_frame(40, 99)).quality is FrameQuality.CORRUPTED
