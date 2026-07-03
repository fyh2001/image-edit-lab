"""
质量度量：纯 numpy，不依赖 Blender，可单测。

给 (before, after) 一对图（HxWx3 uint8）+ 可选的主体投影框，算出几个标量分数，
供 QualityFilter 判定是否保留这一对。语义类（指令-效果一致性，CLIP/VLM）不在这里——
那需要模型、放到打包前的独立阶段。
"""
from __future__ import annotations
from typing import Dict
import numpy as np


def _gray(img) -> np.ndarray:
    a = np.asarray(img, dtype=np.float32)
    if a.ndim == 3:
        a = a[..., :3].mean(axis=-1)
    return a


def change_mask(before, after, pix_delta: int = 12) -> np.ndarray:
    """逐像素最大通道差 > pix_delta 的布尔掩码。"""
    a = np.asarray(before, dtype=np.int16)
    b = np.asarray(after, dtype=np.int16)
    if a.shape != b.shape:
        return np.ones(a.shape[:2], dtype=bool)
    return np.abs(a - b).max(axis=-1) > pix_delta


def change_ratio(before, after, pix_delta: int = 12) -> float:
    """变化像素占比（0~1）。"""
    return float(change_mask(before, after, pix_delta).mean())


def laplacian_sharpness(img) -> float:
    """拉普拉斯方差，衡量清晰度（越大越锐；模糊/欠渲染会很低）。纯 numpy 4 邻域算子。"""
    g = _gray(img)
    if g.shape[0] < 3 or g.shape[1] < 3:
        return 0.0
    lap = (-4.0 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1]
           + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    """布尔掩码盒式膨胀半径 r（纯 numpy 积分图，精确、O(HW)）。"""
    if r <= 0:
        return mask
    H, W = mask.shape
    ii = np.zeros((H + 1, W + 1), dtype=np.int32)
    ii[1:, 1:] = mask.astype(np.int32).cumsum(0).cumsum(1)
    y0 = np.clip(np.arange(H) - r, 0, H)
    y1 = np.clip(np.arange(H) + r + 1, 0, H)
    x0 = np.clip(np.arange(W) - r, 0, W)
    x1 = np.clip(np.arange(W) + r + 1, 0, W)
    Y0, X0 = np.meshgrid(y0, x0, indexing="ij")
    Y1, X1 = np.meshgrid(y1, x1, indexing="ij")
    s = ii[Y1, X1] - ii[Y0, X1] - ii[Y1, X0] + ii[Y0, X0]
    return s > 0


def background_diff(before, after, pix_delta: int = 12, pad_frac: float = 0.05) -> float:
    """**编辑区之外**的平均绝对差（0~255）。

    编辑区 = 变化掩码本身**膨胀 pad** 后的区域（不是它的大包围框）。move 会同时覆盖前后两处
    位置——用包围框会把两处**之间的大片背景**也豁免掉，从而漏掉"背景整体漂移"；用膨胀掩码
    则只豁免真正变化的两坨,中间背景若在动照样能抓到。相机/场景固定、只改主体，此值应很小。
    """
    a = np.asarray(before, dtype=np.float32)
    b = np.asarray(after, dtype=np.float32)
    if a.shape != b.shape:
        return 255.0
    diff = np.abs(a[..., :3] - b[..., :3]).mean(axis=-1)   # HxW
    H, W = diff.shape
    mask = change_mask(before, after, pix_delta)
    if not mask.any():
        return 0.0
    r = int(round(pad_frac * min(H, W)))
    edited = _dilate(mask, r)                              # 变化区（膨胀 pad）——只抠这些，不抠大框
    outside = ~edited
    if not outside.any():
        return 0.0                                         # 变化铺满全图，无"区外"
    return float(diff[outside].mean())


def mean_luminance(img) -> float:
    """整幅平均亮度（0-255）。太低 → 场景欠照明、几乎看不清。"""
    return float(_gray(img).mean())


def compute_scores(before, after, pix_delta: int = 12) -> Dict[str, float]:
    """一次性算齐所有标量分数。"""
    return {
        "change_ratio": round(change_ratio(before, after, pix_delta), 4),
        "sharpness": round(laplacian_sharpness(after), 2),
        "background_diff": round(background_diff(before, after, pix_delta), 3),
        # 取 before/after **较暗的一帧**：after 因编辑变黑（删主光源/纯黑替换物）也要拦
        "brightness": round(min(mean_luminance(before), mean_luminance(after)), 1),
    }
