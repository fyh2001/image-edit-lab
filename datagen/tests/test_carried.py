"""承载物跟随逻辑单测（纯 Python，用假物体模拟 Blender 接口，不依赖 Blender）。"""
import numpy as np

from datagen.worker.edits import _carried


class FakeObj:
    """最小假物体：location = 包围盒中心，bbox = 中心 ± size/2。够测检测/跟随。"""

    def __init__(self, center, size):
        self.loc = np.array(center, float)
        self.size = np.array(size, float)
        self.rot = np.zeros(3)

    def get_bound_box(self):
        h = self.size / 2.0
        return [self.loc + [dx, dy, dz]
                for dx in (-h[0], h[0]) for dy in (-h[1], h[1]) for dz in (-h[2], h[2])]

    def get_location(self):
        return self.loc.tolist()

    def set_location(self, l):
        self.loc = np.array(l, float)

    def get_rotation_euler(self):
        return self.rot.tolist()

    def set_rotation_euler(self, r):
        self.rot = np.array(r, float)


def _subject():
    return FakeObj([0, 0, 0.5], [1.0, 1.0, 1.0])          # 顶面 z=1.0


def test_detects_object_on_top():
    subj = _subject()
    lamp = FakeObj([0.2, 0, 1.15], [0.2, 0.2, 0.3])       # 底 z=1.0，压在主体上
    far = FakeObj([5, 0, 1.15], [0.2, 0.2, 0.3])          # 水平不重叠
    floating = FakeObj([0, 0, 2.0], [0.2, 0.2, 0.3])      # 高高在上，底不贴顶面
    on = _carried.resting_on(subj, [lamp, far, floating, subj])
    assert lamp in on and far not in on and floating not in on


def test_follow_translate():
    lamp = FakeObj([0.2, 0, 1.15], [0.2, 0.2, 0.3])
    snap = _carried.snapshot([lamp])
    _carried.follow_translate(snap, [1.0, -0.5, 0.0])
    assert np.allclose(lamp.get_location(), [1.2, -0.5, 1.15])


def test_follow_drop():
    lamp = FakeObj([0.2, 0, 1.15], [0.2, 0.2, 0.3])
    snap = _carried.snapshot([lamp])
    _carried.follow_drop(snap, 0.3)                       # 主体顶面降 0.3
    assert np.allclose(lamp.get_location(), [0.2, 0, 0.85])


def test_resting_on_rejects_adjacent():
    # 旁边一个等高的矮柜，中心落在主体 footprint 之外 → 不该被当承载物（否则 move 会拖走它）
    subj = _subject()                                     # 顶面 z=1.0, footprint x/y∈[-0.5,0.5]
    beside = FakeObj([0.9, 0, 1.15], [0.4, 0.4, 0.3])     # 中心 x=0.9 在 footprint 外
    assert beside not in _carried.resting_on(subj, [beside])


def test_follow_scale_top_moves_toward_center():
    # scale 缩小到 0.5：承载物水平向主体中心收一半、竖直落 dz
    lamp = FakeObj([0.4, 0.2, 1.15], [0.1, 0.1, 0.3])
    snap = _carried.snapshot([lamp])
    _carried.follow_scale_top(snap, center_xy=[0.0, 0.0], factor=0.5, dz=0.3)
    assert np.allclose(lamp.get_location(), [0.2, 0.1, 0.85])


def test_follow_rotate_z():
    lamp = FakeObj([0.3, 0, 1.15], [0.2, 0.2, 0.3])
    snap = _carried.snapshot([lamp])
    _carried.follow_rotate_z(snap, [0.0, 0.0], np.pi / 2)  # 绕原点转 90°
    assert np.allclose(lamp.get_location(), [0.0, 0.3, 1.15], atol=1e-6)
    assert np.isclose(lamp.get_rotation_euler()[2], np.pi / 2)


def test_snapshot_is_taken_before_follow():
    # 快照记录变换前位姿，多次跟随基于快照增量（这里验证单次即可）
    lamp = FakeObj([0, 0, 1.15], [0.2, 0.2, 0.3])
    snap = _carried.snapshot([lamp])
    lamp.set_location([9, 9, 9])                          # 快照后主体被别的逻辑动过
    _carried.follow_translate(snap, [1, 0, 0])            # 跟随仍基于快照的原始位姿
    assert np.allclose(lamp.get_location(), [1, 0, 1.15])
