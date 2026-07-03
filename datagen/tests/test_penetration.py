"""穿地/穿支撑检测单测（纯 Python，用假物体，不依赖 Blender）。"""
import numpy as np

from datagen.worker.physics.validity import floor_penetration, support_penetration


class Box:
    """轴对齐盒：中心 (cx,cy)、底 z=bottom、高 h、水平半宽 half。"""
    def __init__(self, bottom, h=0.3, half=0.2, cx=0.0, cy=0.0):
        self.bottom, self.h, self.half, self.cx, self.cy = bottom, h, half, cx, cy

    def get_bound_box(self):
        lo, hi = self.bottom, self.bottom + self.h
        return [np.array([self.cx + dx, self.cy + dy, z])
                for dx in (-self.half, self.half) for dy in (-self.half, self.half)
                for z in (lo, hi)]


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


# ---- 穿支撑物 ----
def _table(top=0.75):
    return Box(bottom=0.0, h=top, half=0.5)          # 桌：底 0、顶 top、footprint 半宽 0.5


def test_object_resting_on_table_no_penetration():
    obj = Box(bottom=0.75, h=0.2, half=0.1)          # 稳稳架在桌面
    assert support_penetration(obj, [_table()]) == 0.0


def test_object_clipping_into_table_detected():
    obj = Box(bottom=0.4, h=0.2, half=0.1)           # 底陷到桌体中部(0.4 < 0.75)
    pen = support_penetration(obj, [_table()])
    assert abs(pen - (0.75 - 0.4)) < 1e-6            # 深度 = 桌顶 - 主体底


def test_object_beside_table_not_flagged():
    obj = Box(bottom=0.4, h=0.2, half=0.1, cx=2.0)   # 中心在桌 footprint 外
    assert support_penetration(obj, [_table()]) == 0.0
