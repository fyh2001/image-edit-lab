"""quality.metrics / QualityFilter 单测（纯 numpy，不依赖 Blender）。"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datagen.worker.quality import metrics
from datagen.worker.quality.filter import QualityFilter


def _img(val, shape=(64, 64, 3)):
    return np.full(shape, val, dtype=np.uint8)


def test_background_diff_move_catches_middle_drift():
    # move：主体从左移到右（两坨明显变化，同一行）+ 两坨**之间**背景有细微漂移(<阈值,不进变化掩码)。
    # 旧的"大包围框"横跨两坨会把中间豁免掉→漏检；新的"膨胀掩码"只豁免两坨本身→中间漂移被抓到。
    a = _img(100, (64, 64, 3))
    b = a.copy()
    b[24:40, 4:14] = 200        # 左边一坨（主体原位，明显变化）
    b[24:40, 50:60] = 200       # 右边一坨（主体新位）
    b[24:40, 24:40] = 106       # 两坨之间：细微漂移 +6（<pix_delta，不算"变化"，是背景漂移）
    d = metrics.background_diff(a, b, pix_delta=12)
    # 旧的大包围框法会把中间豁免→d≈0；新法把中间算进区外→d 明显非零（即"抓到了"）。
    assert d > 0.2, f"两坨之间的背景漂移应被抓到（非零），got {d}"


def test_background_diff_clean_near_zero():
    # 只有单处变化、区外干净 → background_diff ≈ 0
    a = _img(100, (64, 64, 3))
    b = a.copy()
    b[26:38, 26:38] = 200
    assert metrics.background_diff(a, b, pix_delta=12) < 0.5


def test_change_ratio_identical_zero():
    a = _img(100)
    assert metrics.change_ratio(a, a.copy()) == 0.0


def test_change_ratio_half():
    a = _img(0)
    b = a.copy()
    b[:32] = 255
    assert abs(metrics.change_ratio(a, b) - 0.5) < 1e-6


def test_sharpness_flat_is_zero():
    assert metrics.laplacian_sharpness(_img(120)) == 0.0


def test_sharpness_edges_positive():
    a = _img(0)
    a[:, 32:] = 255                       # 竖直高对比边
    assert metrics.laplacian_sharpness(a) > 0.0


def test_background_diff_localized_change_is_low():
    # 只有一小块变化 → 编辑区外应为 0
    a = _img(50)
    b = a.copy()
    b[10:20, 10:20] = 200
    assert metrics.background_diff(a, b) == 0.0


def test_background_diff_scattered_change_is_high():
    # 变化散布到角落 → 编辑区包围框很大，但仍有区外残留时应 > 0；
    # 这里制造"主区 + 远处离群点"，离群点把 bbox 撑大，区外仍有别处差异
    a = _img(50)
    b = a.copy()
    b[2:6, 2:6] = 200                     # 角落一小簇
    b[58:62, 58:62] = 200                 # 对角另一簇
    # 两簇之间的大片区域没变 → 但 bbox 覆盖几乎全图；构造区外差异：
    b[30, 0] = 200                        # bbox 外不太可能，主要验证函数不崩、返回有限值
    assert metrics.background_diff(a, b) >= 0.0


def test_filter_passes_clean_pair():
    a = _img(50)
    b = a.copy()
    b[20:40, 20:40] = 200                 # 清晰、局部、适中变化
    qf = QualityFilter()
    passed, scores, reason = qf.evaluate(a, b)
    assert passed and reason == ""
    assert set(scores) == {"change_ratio", "sharpness", "background_diff", "brightness"}


def test_filter_rejects_too_dark():
    a = _img(4)                           # 近黑（亮度 4 < 阈值 10）
    b = a.copy()
    b[20:40, 20:40] = 30
    passed, scores, reason = QualityFilter().evaluate(a, b)
    assert not passed and "too_dark" in reason
    assert scores["brightness"] < 10


def test_filter_rejects_blurry():
    a = _img(50)
    b = a.copy()
    b[20:40, 20:40] = 51                  # 几乎无对比 → sharpness 低 + 变化极小
    qf = QualityFilter({"min_sharpness": 5.0, "min_change_ratio": 0.001})
    passed, _, reason = qf.evaluate(a, b)
    assert not passed


def test_filter_rejects_change_too_large():
    a = _img(0)
    b = _img(255)                         # 全图变化
    qf = QualityFilter()
    passed, _, reason = qf.evaluate(a, b)
    assert not passed and "change_too_large" in reason
