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


def background_diff(before, after, pix_delta: int = 12, pad_frac: float = 0.05) -> float:
    """**编辑区之外**的平均绝对差（0~255）。

    编辑区 = 变化掩码的包围框（move/scale 会同时覆盖前后两处位置，取并集包围框即可）。
    本管线相机/场景固定、只改主体，理论上编辑区外只剩阴影/GI 轻微变化 → 该值应很小。
    偏大 = 编辑区外还有别的东西在变（整体错位/闪烁/相机漂移），对齐变差。
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
    ys, xs = np.where(mask)
    px, py = pad_frac * W, pad_frac * H
    x0 = int(max(0, np.floor(xs.min() - px)))
    x1 = int(min(W, np.ceil(xs.max() + 1 + px)))
    y0 = int(max(0, np.floor(ys.min() - py)))
    y1 = int(min(H, np.ceil(ys.max() + 1 + py)))
    outside = np.ones((H, W), dtype=bool)
    outside[y0:y1, x0:x1] = False                          # 抠掉编辑区（含 pad）
    if not outside.any():
        return 0.0                                         # 变化铺满全图，无"区外"
    return float(diff[outside].mean())


def compute_scores(before, after, pix_delta: int = 12) -> Dict[str, float]:
    """一次性算齐所有标量分数。"""
    return {
        "change_ratio": round(change_ratio(before, after, pix_delta), 4),
        "sharpness": round(laplacian_sharpness(after), 2),
        "background_diff": round(background_diff(before, after, pix_delta), 3),
    }
