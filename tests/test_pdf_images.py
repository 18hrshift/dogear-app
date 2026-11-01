"""Page coverage by tiled or overlapping raster images, excluding gaps and off-page parts."""

import random
from types import SimpleNamespace

import pytest

from dogear.extract.pdf_images import image_boxes, image_page_share


def page(*boxes, bounds=(0, 0, 100, 100)):
    return SimpleNamespace(
        bbox=bounds,
        images=[dict(zip(("x0", "top", "x1", "bottom"), box, strict=True)) for box in boxes],
    )


@pytest.mark.parametrize(
    ("boxes", "share"),
    [
        ([], 0),
        ([(0, 0, 100, 100)], 1),
        ([(0, 0, 100, 50), (0, 50, 100, 100)], 1),
        ([(0, 0, 50, 100), (50, 0, 100, 100)], 1),
        ([(0, 0, 40, 100), (60, 0, 100, 100)], 0.8),
        ([(0, 0, 60, 100), (40, 0, 100, 100)], 1),
        ([(0, 0, 60, 60)] * 10, 0.36),
        ([(0, 0, 100, 10), (0, 0, 10, 100)], 0.19),  # empty space inside an L
        ([(-50, -50, 50, 50), (50, 50, 150, 150)], 0.5),
        ([(-50, -50, 150, 150)], 1),
        ([(100, 100, 200, 200), (0, 0, 0, 100), (20, 20, 10, 30)], 0),
    ],
)
def test_coverage_counts_only_the_union_of_visible_images(boxes, share):
    assert image_page_share(page(*boxes)) == pytest.approx(share)


def test_a_page_can_start_at_an_offset():
    assert image_page_share(page((0, 0, 200, 200), bounds=(50, 50, 150, 150))) == 1
    assert image_boxes(page((0, 0, 100, 100), bounds=(50, 50, 150, 150))) == [(50, 50, 100, 100)]


@pytest.mark.parametrize("bounds", [(0, 0, 0, 100), (0, 0, float("inf"), 100)])
def test_invalid_page_area_does_not_divide_by_zero_or_give_nan(bounds):
    assert image_page_share(page((0, 0, 100, 100), bounds=bounds)) == 0


def test_many_disjoint_tiles_are_measured_without_a_grid_of_all_edge_pairs():
    boxes = [(i * 2, i * 2, i * 2 + 1, i * 2 + 1) for i in range(5000)]
    assert image_page_share(page(*boxes, bounds=(0, 0, 10000, 10000))) == pytest.approx(0.00005)


@pytest.mark.parametrize("seed", range(5))
def test_overlapping_rectangles_match_the_covered_unit_squares(seed):
    randomizer = random.Random(seed)
    boxes = []
    squares = set()
    for _ in range(30):
        x0, x1 = sorted(randomizer.sample(range(11), 2))
        y0, y1 = sorted(randomizer.sample(range(11), 2))
        boxes.append((x0, y0, x1, y1))
        squares.update((x, y) for x in range(x0, x1) for y in range(y0, y1))
    assert image_page_share(page(*boxes, bounds=(0, 0, 10, 10))) == pytest.approx(
        len(squares) / 100
    )
