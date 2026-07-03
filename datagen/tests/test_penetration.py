"""穿地检测 floor_penetration 单测（纯 Python，用假物体，不依赖 Blender）。"""
import numpy as np

from datagen.worker.physics.validity import floor_penetration


class Box:
    """最低点在 z=bottom、顶在 z=bottom+h 的假物体。"""
    def __init__(self, bottom, h=0.3, half=0.2):
        self.bottom, self.h, self.half = bottom, h, half

    def get_bound_box(self):
        lo, hi = self.bottom, self.bottom + self.h
        return [np.array([dx, dy, z]) for dx in (-self.half, self.half)
                for dy in (-self.half, self.half) for z in (lo, hi)]


def test_resting_on_floor_no_penetration():
    assert floor_penetration(Box(bottom=0.0), floor_z=0.0) == 0.0        # 贴地


def test_penetrating_floor_detected():
    assert abs(floor_penetration(Box(bottom=-0.15), floor_z=0.0) - 0.15) < 1e-6  # 陷入地面 15cm


def test_object_on_table_not_flagged():
    # 桌上物：最低点在桌面 0.75m，远高于地面 → 穿地深度 0（不误报）
    assert floor_penetration(Box(bottom=0.75), floor_z=0.0) == 0.0


def test_floating_not_penetration():
    # 悬空在地面上方 → 穿地深度 0（穿地只管地面以下）
    assert floor_penetration(Box(bottom=0.3), floor_z=0.0) == 0.0
