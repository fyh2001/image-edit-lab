"""
坐标系与方向：把世界系位移分解到相机系，并归类成语义方向词。
纯 Python（仅依赖 numpy），不碰 Blender，可单元测试。

约定：
- 世界系：+Z 向上，右手系，单位米。
- 相机系基向量（世界系下的单位向量）：
    right   : 相机视图的"右"
    up      : 相机视图的"上"
    forward : 相机视线方向（从相机指向场景）。+forward = 远离相机。
"""
from __future__ import annotations
from typing import Dict, List, Sequence
import numpy as np


def camera_basis_from_matrix(cam2world: Sequence[Sequence[float]]):
    """从 4x4 相机外参取 right/up/forward（世界系单位向量）。

    Blender 相机：局部 +X=right, +Y=up, 视线沿 -Z。
    forward 定义为视线方向 = -局部Z。
    """
    M = np.asarray(cam2world, dtype=float)
    R = M[:3, :3]
    right = _unit(R[:, 0])
    up = _unit(R[:, 1])
    forward = _unit(-R[:, 2])
    return right, up, forward


def classify_translation(delta_world: Sequence[float],
                         right: Sequence[float],
                         up: Sequence[float],
                         forward: Sequence[float],
                         rel_threshold: float = 0.15,
                         max_terms: int = 2) -> Dict:
    """把世界系位移向量分解到相机系并给出语义方向词。

    Args:
        delta_world: 世界系位移 [dx,dy,dz]（米）
        right/up/forward: 相机系基向量（世界系）
        rel_threshold: 某轴分量绝对值占总位移长度的比例阈值，低于则忽略该轴
        max_terms: 最多保留几个方向词（按分量大小排序）

    Returns:
        {
          "camera": {"right": r, "up": u, "forward": f},  # 米
          "semantic": ["left","up"],                       # 语义方向词
        }
    """
    d = np.asarray(delta_world, dtype=float)
    r = float(np.dot(d, _unit(right)))
    u = float(np.dot(d, _unit(up)))
    f = float(np.dot(d, _unit(forward)))

    length = float(np.linalg.norm(d)) or 1e-9
    terms = [
        (abs(r) / length, "right" if r > 0 else "left", abs(r)),
        (abs(u) / length, "up" if u > 0 else "down", abs(u)),
        # +forward = 远离相机 = farther；-forward = 靠近 = closer
        (abs(f) / length, "farther" if f > 0 else "closer", abs(f)),
    ]
    terms.sort(reverse=True)
    semantic = [word for ratio, word, _ in terms
                if ratio >= rel_threshold][:max_terms]

    return {
        "camera": {"right": round(r, 4), "up": round(u, 4), "forward": round(f, 4)},
        "semantic": semantic,
    }


def semantic_phrase(words: List[str]) -> str:
    """把方向词拼成自然短语，如 ['left','up'] -> 'to the upper left'。"""
    if not words:
        return "slightly"
    mapping = {
        "left": "left", "right": "right", "up": "up", "down": "down",
        "closer": "closer to the camera", "farther": "away from the camera",
    }
    parts = [mapping.get(w, w) for w in words]
    if len(parts) == 1:
        return f"to the {parts[0]}" if parts[0] in ("left", "right", "up", "down") else parts[0]
    return "to the " + " ".join(reversed([p for p in parts if p in ("left", "right", "up", "down")])) \
        if all(p in ("left", "right", "up", "down") for p in parts) else ", ".join(parts)


def direction_consistency(semantic: Sequence[str], d_px: float, d_py: float) -> Dict:
    """校验语义方向词与主体在画面里的实际像素位移是否一致（纯函数，可单测）。

    图像坐标约定：x 向右增大、y **向下**增大（左上角为原点）。
        left  → d_px < 0 ；right → d_px > 0
        up    → d_py < 0 ；down  → d_py > 0
    closer/farther 是深度方向，无法只靠 xy 判定，跳过（记为 None，不计入 consistent）。

    Returns:
        {"per_term": {term: bool|None}, "consistent": bool}
        consistent = 所有可判定（左右上下）的词都对；没有可判定词时为 True。
    """
    per_term = {}
    for term in semantic:
        if term == "left":
            per_term[term] = d_px < 0
        elif term == "right":
            per_term[term] = d_px > 0
        elif term == "up":
            per_term[term] = d_py < 0
        elif term == "down":
            per_term[term] = d_py > 0
        else:                                  # closer / farther → 深度，不判
            per_term[term] = None
    checkable = [v for v in per_term.values() if v is not None]
    return {"per_term": per_term, "consistent": all(checkable)}


def axis_angle_to_quat(axis: Sequence[float], angle_rad: float) -> List[float]:
    """绕单位轴 axis 旋转 angle_rad 的四元数，Hamilton 约定，返回 [w, x, y, z]。

    纯函数（仅 numpy），用于记录 object_rotate 的 delta_quat。可单测。
    """
    a = _unit(axis)
    h = angle_rad / 2.0
    s = float(np.sin(h))
    return [float(np.cos(h)), float(a[0] * s), float(a[1] * s), float(a[2] * s)]


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v
