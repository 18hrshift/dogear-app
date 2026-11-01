"""Raster image geometry on a PDF page, clipped to its visible bounds.

Scanners sometimes store one page as many image tiles. Their combined coverage is the
union of their rectangles, not the sum (overlaps count once) or their bounding box
(gaps don't count). A sweep over rectangle edges measures it in O(n log n) time and
O(n) memory, even for many disjoint or overlapping images from an untrusted PDF.
"""

from __future__ import annotations

import math
from typing import Any

Box = tuple[float, float, float, float]  # x0, top, x1, bottom


def image_boxes(page: Any) -> list[Box]:
    """Each image's visible rectangle, excluding empty and off-page images."""
    x0, top, x1, bottom = page.bbox
    boxes = []
    for image in page.images:
        box = (
            max(image["x0"], x0),
            max(image["top"], top),
            min(image["x1"], x1),
            min(image["bottom"], bottom),
        )
        if all(math.isfinite(value) for value in box) and box[0] < box[2] and box[1] < box[3]:
            boxes.append(box)
    return boxes


def image_page_share(page: Any) -> float:
    """The share of the visible page covered by raster images, with overlaps counted once."""
    x0, top, x1, bottom = page.bbox
    area = (x1 - x0) * (bottom - top)
    if not math.isfinite(area) or area <= 0:
        return 0
    boxes = image_boxes(page)
    return min(1.0, _union_area(boxes) / area) if boxes else 0


def _union_area(boxes: list[Box]) -> float:
    ys = sorted({y for _, top, _, bottom in boxes for y in (top, bottom)})
    positions = {y: index for index, y in enumerate(ys)}
    events = []
    for x0, top, x1, bottom in boxes:
        start, stop = positions[top], positions[bottom]
        events.extend([(x0, 1, start, stop), (x1, -1, start, stop)])
    events.sort(key=lambda event: event[0])
    coverage = _Coverage(ys)
    area = 0.0
    previous = events[0][0]
    for x, change, start, stop in events:
        area += (x - previous) * coverage.length[1]
        coverage.update(1, 0, len(ys) - 1, start, stop, change)
        previous = x
    return area


class _Coverage:
    """Covered y length between consecutive x edges. Each tree node holds one range
    of y intervals, its covering rectangle count, and the length covered below it."""

    def __init__(self, ys: list[float]) -> None:
        self.ys = ys
        self.count = [0] * (4 * len(ys))
        self.length = [0.0] * (4 * len(ys))

    def update(self, node: int, lo: int, hi: int, start: int, stop: int, change: int) -> None:
        if start <= lo and hi <= stop:
            self.count[node] += change
        else:
            middle = (lo + hi) // 2
            if start < middle:
                self.update(node * 2, lo, middle, start, stop, change)
            if stop > middle:
                self.update(node * 2 + 1, middle, hi, start, stop, change)
        if self.count[node] > 0:
            self.length[node] = self.ys[hi] - self.ys[lo]
        elif hi - lo == 1:
            self.length[node] = 0.0
        else:
            self.length[node] = self.length[node * 2] + self.length[node * 2 + 1]
