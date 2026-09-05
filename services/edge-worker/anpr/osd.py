"""Suppress burned-in overlays so they stop being read as plates.

Motivation, measured on a real estate rather than assumed. Running a plate/text
detector over CCTV frames turns the camera's own furniture into "registrations":

    a clock reading 22:07:07     -> ZZ207207
    a title caption "AHMEDABAD"  -> AHMEDABAD parsed as a series

The grammar's speculative-read guard refuses to *confirm* those, which is the
important protection. Suppressing them at the source is better still: an overlay
that is never cropped cannot consume OCR budget, cannot enter the fusion vote,
and cannot fill the review queue with clock digits.

METHOD — positional stability, learned live.

A number plate moves through the frame; a burned-in overlay does not. So a
detection box that keeps reappearing at the SAME coordinates across many frames
is furniture, not traffic. This learns those regions from the running stream
itself - no survey step, no per-camera configuration - and then discards any
plate candidate whose centre falls inside one.

It uses the SAME detector whose false positives it suppresses, so it targets
exactly the regions that actually cause the problem rather than regions that
merely look static. And it is deliberately slow to condemn: a region has to
recur across a configurable number of distinct frames before anything is
excluded, because suppressing a real lane where plates happen to cluster would
be worse than the overlays it removes.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return float(inter / ua) if ua > 0 else 0.0


@dataclass
class _Cluster:
    """One recurring position, and how consistently a box lands on it."""

    box: tuple[float, float, float, float]
    #: Distinct frames a box has landed here. Frames, not detections, so a
    #: detector that fires twice on one overlay in one frame does not inflate it.
    frames: set = field(default_factory=set)
    #: Whether a plate ever CONFIRMED here. A real plate can pass through a
    #: static-looking spot; a confirmation there rescues the region from being
    #: condemned as furniture.
    ever_confirmed: bool = False


class OsdSuppressor:
    """Per-camera. Learns overlay regions from the stream and masks them."""

    def __init__(
        self,
        *,
        stability_iou: float = 0.5,
        min_frames: int = 8,
        forget_frames: int = 300,
    ) -> None:
        #: IoU at which two boxes count as the same position.
        self._stability_iou = stability_iou
        #: Distinct frames a position must recur in before it is condemned.
        self._min_frames = min_frames
        #: A cluster not seen for this many frames is dropped, so a caption that
        #: is removed mid-session stops being masked.
        self._forget_frames = forget_frames
        self._clusters: list[_Cluster] = []
        self._frame = 0

    def reset(self) -> None:
        """Forget everything - called on a scene discontinuity, where the whole
        frame (overlays included) may have changed."""
        self._clusters.clear()
        self._frame = 0

    def _match(self, box: tuple[float, float, float, float]) -> _Cluster | None:
        best, best_iou = None, self._stability_iou
        for cluster in self._clusters:
            iou = _iou(cluster.box, box)
            if iou >= best_iou:
                best, best_iou = cluster, iou
        return best

    def observe(self, box: tuple[float, float, float, float], *, confirmed: bool = False) -> None:
        """Record a plate/text candidate box seen this pass.

        Feed it EVERY candidate the detector proposed, whether or not it was
        read - an overlay is defined by the detector firing on it repeatedly, so
        the candidates are exactly the right signal. `confirmed` marks a box
        where a plate actually settled, which protects the region from ever
        being treated as furniture.
        """
        cluster = self._match(box)
        if cluster is None:
            cluster = _Cluster(box=box)
            self._clusters.append(cluster)
        cluster.frames.add(self._frame)
        cluster.ever_confirmed = cluster.ever_confirmed or confirmed

    def advance(self) -> None:
        """Move to the next frame and forget stale clusters. Call once per frame
        after observing this frame's candidates."""
        self._frame += 1
        if self._forget_frames and self._frame % 50 == 0:
            cutoff = self._frame - self._forget_frames
            self._clusters = [
                c for c in self._clusters if c.frames and max(c.frames) >= cutoff
            ]

    def is_overlay(self, box: tuple[float, float, float, float]) -> bool:
        """True when this candidate's centre falls in a learned overlay region.

        A region qualifies only once it has recurred across `min_frames`
        distinct frames AND has never carried a confirmed plate - a plate that
        settles somewhere is proof the spot is a lane, not furniture.
        """
        cx = (box[0] + box[2]) / 2.0
        cy = (box[1] + box[3]) / 2.0
        for cluster in self._clusters:
            if cluster.ever_confirmed or len(cluster.frames) < self._min_frames:
                continue
            bx1, by1, bx2, by2 = cluster.box
            if bx1 <= cx <= bx2 and by1 <= cy <= by2:
                return True
        return False

    @property
    def learned_regions(self) -> list[tuple[float, float, float, float]]:
        """The overlay boxes currently being masked - for logging and the UI."""
        return [
            c.box
            for c in self._clusters
            if not c.ever_confirmed and len(c.frames) >= self._min_frames
        ]
