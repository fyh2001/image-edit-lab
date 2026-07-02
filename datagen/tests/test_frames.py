"""geometry/frames 纯函数单测（仅 numpy，不依赖 Blender）。

    cd <项目根> && python -m pytest tests/ -q
"""
import os
import sys
import math

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datagen.worker.geometry import frames

# 相机基：右=+X、上=+Z、视线（forward）=+Y（+forward 表示远离相机）
RIGHT, UP, FWD = [1, 0, 0], [0, 0, 1], [0, 1, 0]


def test_classify_left_right():
    assert "right" in frames.classify_translation([2, 0, 0], RIGHT, UP, FWD)["semantic"]
    assert "left" in frames.classify_translation([-2, 0, 0], RIGHT, UP, FWD)["semantic"]


def test_classify_closer_farther():
    assert "farther" in frames.classify_translation([0, 2, 0], RIGHT, UP, FWD)["semantic"]
    assert "closer" in frames.classify_translation([0, -2, 0], RIGHT, UP, FWD)["semantic"]


def test_classify_threshold_drops_tiny_component():
    # 主要往右，向上分量只占 ~5% < 15% 阈值 → 只保留 right
    info = frames.classify_translation([2.0, 0, 0.1], RIGHT, UP, FWD)
    assert info["semantic"] == ["right"]


def test_classify_camera_components():
    c = frames.classify_translation([1.5, 0, 0], RIGHT, UP, FWD)["camera"]
    assert abs(c["right"] - 1.5) < 1e-6 and abs(c["up"]) < 1e-6


def test_semantic_phrase():
    assert frames.semantic_phrase(["left"]) == "to the left"
    assert frames.semantic_phrase([]) == "slightly"


def test_camera_basis_from_identity():
    r, u, f = frames.camera_basis_from_matrix(np.eye(4))
    # Blender 相机：局部 +X=right, +Y=up, 视线 -Z
    assert np.allclose(r, [1, 0, 0])
    assert np.allclose(u, [0, 1, 0])
    assert np.allclose(f, [0, 0, -1])


def test_axis_angle_identity():
    q = frames.axis_angle_to_quat([0, 0, 1], 0.0)
    assert abs(q[0] - 1.0) < 1e-9 and max(abs(x) for x in q[1:]) < 1e-9


def test_axis_angle_z_90deg():
    q = frames.axis_angle_to_quat([0, 0, 1], math.pi / 2)
    assert abs(q[0] - math.sqrt(0.5)) < 1e-6      # w = cos(45°)
    assert abs(q[3] - math.sqrt(0.5)) < 1e-6      # z = sin(45°)
    assert abs(q[1]) < 1e-9 and abs(q[2]) < 1e-9


def test_axis_angle_normalizes_axis():
    # 非单位轴也应归一化
    q = frames.axis_angle_to_quat([0, 0, 5], math.pi / 2)
    assert abs(q[3] - math.sqrt(0.5)) < 1e-6


def test_direction_consistency_left_up_correct():
    # 图像 y 向下：left=d_px<0, up=d_py<0
    r = frames.direction_consistency(["left", "up"], d_px=-30, d_py=-20)
    assert r["consistent"] and r["per_term"] == {"left": True, "up": True}


def test_direction_consistency_detects_mismatch():
    # 说 right 但实际往左（d_px<0）→ 不一致
    r = frames.direction_consistency(["right"], d_px=-30, d_py=0)
    assert not r["consistent"] and r["per_term"]["right"] is False


def test_direction_consistency_down():
    r = frames.direction_consistency(["down"], d_px=0, d_py=25)
    assert r["consistent"]


def test_direction_consistency_depth_terms_skipped():
    # closer/farther 不可判（深度）→ 记 None，不影响 consistent
    r = frames.direction_consistency(["closer", "farther"], d_px=5, d_py=5)
    assert r["consistent"] and all(v is None for v in r["per_term"].values())


def test_direction_consistency_mixed_depth_and_xy():
    r = frames.direction_consistency(["left", "farther"], d_px=-10, d_py=3)
    assert r["consistent"]
    assert r["per_term"]["left"] is True and r["per_term"]["farther"] is None
