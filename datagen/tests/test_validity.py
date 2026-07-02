"""physics/validity 纯函数单测（change_is_visible / find_valid，不依赖 Blender）。

    cd <项目根> && python -m pytest tests/ -q
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datagen.worker.physics import validity


def test_change_is_visible_identical():
    img = np.zeros((16, 16, 3), np.uint8)
    vis, ratio = validity.change_is_visible(img, img.copy(), min_ratio=0.01)
    assert not vis and ratio == 0.0


def test_change_is_visible_big_change():
    a = np.zeros((16, 16, 3), np.uint8)
    b = a.copy()
    b[:8, :, :] = 255                       # 改了一半像素
    vis, ratio = validity.change_is_visible(a, b, min_ratio=0.01)
    assert vis and ratio >= 0.49


def test_change_is_visible_below_threshold():
    a = np.zeros((16, 16, 3), np.uint8)
    b = a.copy()
    b[0, 0, :] = 255                        # 仅 1/256 像素变化
    vis, ratio = validity.change_is_visible(a, b, min_ratio=0.05)
    assert not vis and ratio < 0.05


def test_change_is_visible_small_delta_ignored():
    a = np.full((16, 16, 3), 100, np.uint8)
    b = a + 5                               # 差值 5 < pix_delta(12) → 视作无变化
    vis, ratio = validity.change_is_visible(a, b, min_ratio=0.01)
    assert not vis and ratio == 0.0


def test_change_is_visible_shape_mismatch():
    a = np.zeros((4, 4, 3), np.uint8)
    b = np.zeros((5, 5, 3), np.uint8)
    vis, ratio = validity.change_is_visible(a, b)
    assert vis and ratio == 1.0


def test_find_valid_succeeds_on_4th():
    seq = iter([1, 2, 3, 7, 8])
    ok, cand, n = validity.find_valid(lambda: next(seq), lambda x: x >= 7, 10)
    assert ok and cand == 7 and n == 4


def test_find_valid_exhausts():
    ok, cand, n = validity.find_valid(lambda: 0, lambda x: x > 5, 5)
    assert not ok and n == 5
