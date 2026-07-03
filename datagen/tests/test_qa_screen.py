"""QA 启发式初筛规则单测（纯 Python，用小图 + 合成 metadata）。"""
import numpy as np

from datagen.orchestrator.qa_screen import screen_pair


def _img(val):
    return np.full((32, 32, 3), val, dtype=np.uint8)


def _codes(sample, before=None, after=None):
    before = before if before is not None else _img(120)
    after = after if after is not None else _img(120)
    return [c for c, _r, _s in screen_pair(sample, before, after)]


def test_flags_imperceptible():
    s = {"edit": {"op": "object_move", "translation_world": [0.1, 0, 0],
                  "validity": {"pixel_change_ratio": 0.001}}}
    assert "imperceptible" in _codes(s)


def test_flags_far_move():
    s = {"edit": {"op": "object_move", "translation_world": [2.0, 4.2, 1.5],
                  "validity": {"quality": {"change_ratio": 0.05}}}}
    assert "far_move" in _codes(s)


def test_flags_odd_placement():
    s = {"edit": {"op": "object_move", "placement_mode": "ceiling",
                  "translation_world": [0.5, 0.5, 2.0],
                  "validity": {"quality": {"change_ratio": 0.05}}}}
    assert "odd_placement" in _codes(s)


def test_flags_extreme_scale():
    s = {"edit": {"op": "object_scale", "factor": 3.0,
                  "validity": {"quality": {"change_ratio": 0.1}}}}
    assert "extreme_scale" in _codes(s)


def test_flags_dropped_objects():
    s = {"edit": {"op": "object_delete", "dropped_supported": 3,
                  "validity": {"quality": {"change_ratio": 0.1}}}}
    assert "dropped_objects" in _codes(s)


def test_clean_pair_no_flags():
    s = {"edit": {"op": "object_scale", "factor": 1.3,
                  "validity": {"quality": {"change_ratio": 0.08, "background_diff": 0.5}}}}
    assert _codes(s) == []


def test_flags_dark_from_pixels():
    s = {"edit": {"op": "object_delete", "validity": {"quality": {"change_ratio": 0.1}}}}
    assert "dark" in _codes(s, before=_img(5), after=_img(5))
