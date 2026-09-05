"""Spend the frame budget where a plate might actually be readable.

A fixed stride is the wrong sampler for ANPR on street CCTV. Plates on this
estate are 50-120px only while a vehicle is close to the lens, and that is a
window of a second or two out of every minute. Sampling every twentieth frame
for twenty-five frames - the old default - looks at about a second of footage
spread thinly across a minute, and so it almost never lands on the moment the
plate is large. Measured on this estate's own frames: a camera can propose
thirty plate boxes over fifteen frames when a vehicle is near, and zero over
fifteen frames when the road is empty. The difference is entirely *when* you
look.

So this looks cheaply and often, and spends the expensive passes in bursts:

  SCAN     every `stride` frames, run detection only. Cheap.
  BURST    a vehicle appeared that is physically large enough to carry a
           readable plate, so process every frame for a while - the plate is
           growing, and the best crop is a moment away.

The trigger is the vehicle's box width, not a plate detection, because the
plate is not findable until it is already large: waiting for one to appear
before sampling densely means missing the approach that produces it. Vehicle
width is a usable proxy - a plate is roughly a quarter of it head-on, which is
the same estimate the suitability diagnostic uses.

Nothing here decides what is a plate. It decides only where to spend effort,
and a burst that finds nothing costs a few frames of detection.
"""
from __future__ import annotations

from dataclasses import dataclass


#: A plate is about this fraction of a vehicle box's width, head-on. Generous
#: on purpose - over-triggering costs a few cheap frames, under-triggering
#: costs the read entirely.
PLATE_FRACTION = 0.25

#: Estimated plate width, in pixels, worth spending dense frames on. Below the
#: readable floor there is nothing to catch; this sits under it deliberately so
#: the burst starts while the vehicle is still approaching and gets larger.
BURST_PLATE_PX = 45.0


@dataclass
class SamplerStats:
    looked: int = 0
    processed: int = 0
    bursts: int = 0


class AdaptiveSampler:
    """Decides which frames get the expensive pass.

    Usage per frame:

        if sampler.should_process(index):
            vehicles = detect(frame)
            sampler.note(vehicles, frame_width)
    """

    def __init__(
        self,
        *,
        stride: int = 20,
        burst_frames: int = 24,
        burst_plate_px: float = BURST_PLATE_PX,
    ) -> None:
        #: Frames between routine looks while nothing is close.
        self.stride = max(1, stride)
        #: How many consecutive frames a trigger buys. Sized to cover a vehicle
        #: crossing the near field at typical junction speeds.
        self.burst_frames = max(1, burst_frames)
        self.burst_plate_px = burst_plate_px
        self._burst_left = 0
        self.stats = SamplerStats()

    @property
    def bursting(self) -> bool:
        return self._burst_left > 0

    def should_process(self, index: int) -> bool:
        self.stats.looked += 1
        if self._burst_left > 0:
            self._burst_left -= 1
            self.stats.processed += 1
            return True
        if index % self.stride == 0:
            self.stats.processed += 1
            return True
        return False

    def note(self, vehicle_widths_px) -> None:
        """Tell the sampler how large the vehicles in the last processed frame
        were, so it can decide whether a readable plate is coming."""
        widest = max((float(w) for w in vehicle_widths_px), default=0.0)
        if widest * PLATE_FRACTION >= self.burst_plate_px:
            if self._burst_left == 0:
                self.stats.bursts += 1
            # Re-arm rather than accumulate: a vehicle that stays large keeps
            # the burst alive without letting a queue of them run for minutes.
            self._burst_left = self.burst_frames

    def describe(self) -> dict:
        return {
            "looked": self.stats.looked,
            "processed": self.stats.processed,
            "bursts": self.stats.bursts,
            "stride": self.stride,
            "burst_frames": self.burst_frames,
            "burst_plate_px": self.burst_plate_px,
        }
