"""指令风格定义 + **数据集级风格均衡采样**（纯逻辑，可单测）。

设计见 docs/CAPTION_DESIGN.md：训练主集**每对一条** caption，captioner 按权重挑一种风格。
为避免风格倾斜（比如全是"直接命令"），用**亏空采样**（和算子均衡同一套思路）：
谁离目标占比最亏就更可能被选中，让 direct/spatial/intent/goal/casual 都够。
"""
from __future__ import annotations

# 五种真实用户表达风格（与 CAPTION_DESIGN.md 的谱系对应）。
STYLES = ["direct", "spatial", "intent", "goal", "casual"]

# 目标产出占比（可被 config 覆盖）。默认略压 goal（有些算子难自然表达成"结果目标"）。
DEFAULT_WEIGHTS = {
    "direct": 1.0,
    "spatial": 1.0,
    "intent": 1.0,
    "goal": 0.6,
    "casual": 0.8,
}

# 有些算子天然不适合某些风格（如 scale 说成"清空桌面"这种 goal 很别扭）→ 允许禁用。
# 不在表里的算子 = 允许全部风格。
OP_STYLE_BLOCK = {
    "object_scale": {"goal"},        # "让它变大"很难自然表达成场景级目标
    "object_rotate": {"goal"},
}


def allowed_styles(op):
    """给定算子，返回它允许的风格列表（保持 STYLES 顺序）。"""
    blocked = OP_STYLE_BLOCK.get(op, set())
    return [s for s in STYLES if s not in blocked]


def normalize_weights(weights=None):
    """把用户权重并进默认表，负数/缺失归零；返回覆盖所有 STYLES 的 dict。"""
    w = dict(DEFAULT_WEIGHTS)
    for k, v in (weights or {}).items():
        if k in w:
            w[k] = max(0.0, float(v))
    return w


def sample_style(rng, counts, op=None, weights=None):
    """**亏空采样**选一种风格。

    counts: {style: 已产出条数}（跨整个数据集累加，captioner 维护）。
    op:     限定到该算子允许的风格（None = 全部）。
    weights:{style: 目标占比}，缺省 DEFAULT_WEIGHTS。
    规则：把每种风格的"目标累计条数"(占比×总数)减去"已产条数"得亏空，亏空越大越可能被选；
    全部达标时退回按权重随机。返回选中的 style 字符串。
    """
    w = normalize_weights(weights)
    cand = allowed_styles(op) if op else list(STYLES)
    cand = [s for s in cand if w.get(s, 0.0) > 0.0]
    if not cand:                                   # 全被 block/清零 → 兜底 direct
        return "direct"

    total = sum(counts.get(s, 0) for s in cand)
    wsum = sum(w[s] for s in cand)
    deficits = []
    for s in cand:
        target = (w[s] / wsum) * (total + 1)       # +1：给下一条定目标
        deficits.append(max(0.0, target - counts.get(s, 0)))

    dsum = sum(deficits)
    if dsum <= 1e-9:                               # 都不亏 → 按权重随机
        probs = [w[s] / wsum for s in cand]
    else:
        probs = [d / dsum for d in deficits]
    return cand[int(rng.choice(len(cand), p=probs))]
