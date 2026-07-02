"""
QualityFilter：渲染后、打包前的一道质量门槛（廉价像素度量，纯 numpy）。

判定项（阈值可在 config 的 render.quality 覆盖，默认对干净数据留足余量）：
- change_ratio 上限：变化铺满全图 → 主体撑爆/整帧闪烁，丢。（下限交给 run_job 的可见性过滤）
- sharpness 下限：拉普拉斯方差过低 → 模糊/欠渲染/近黑，丢。
- background_diff 上限：编辑区之外还在大幅变化 → 整体错位/相机漂移，对齐差，丢。

语义类（指令-效果一致性，CLIP/VLM）不在这里：需模型，放到打包前的独立阶段
（见 orchestrator/collector，后续接入）。这里只做不需要模型的快检。
"""
from __future__ import annotations
from typing import Dict, Optional, Tuple

from datagen.worker.quality.metrics import compute_scores


class QualityFilter:
    DEFAULTS = {
        "min_change_ratio": 0.0,      # 下限主要由 run_job 的可见性过滤把关，这里默认不重复拦
        "max_change_ratio": 0.85,
        "min_sharpness": 3.0,
        "max_background_diff": 3.0,
        "min_brightness": 10.0,       # 平均亮度<此(0-255) → 欠照明近黑、看不清，丢。标定：4.7丢/15.4留
    }

    def __init__(self, cfg: Optional[Dict] = None):
        self.cfg = {**self.DEFAULTS, **(cfg or {})}

    def evaluate(self, before, after) -> Tuple[bool, Dict[str, float], str]:
        """返回 (是否通过, 分数 dict, 失败原因串)。"""
        s = compute_scores(before, after)
        c = self.cfg
        reasons = []
        if s["change_ratio"] < c["min_change_ratio"]:
            reasons.append("change_too_small")
        if s["change_ratio"] > c["max_change_ratio"]:
            reasons.append("change_too_large")
        if s["sharpness"] < c["min_sharpness"]:
            reasons.append("too_blurry")
        if s.get("brightness", 255) < c["min_brightness"]:
            reasons.append("too_dark")
        if s["background_diff"] > c["max_background_diff"]:
            reasons.append("background_unstable")
        return (not reasons), s, ";".join(reasons)
