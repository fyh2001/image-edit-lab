""""承载物"跟随：编辑一个**放着东西的**主体（床头柜/桌/柜）时，让**放在它顶面上的物体**
跟着一起变，避免"缩小床头柜→台灯悬空""移动桌子→桌上物留在原地"这类穿帮。

用法（在 apply 里，变换**前**快照，变换成功**后**跟随）：
    carried = snapshot(resting_on(subject, ctx.all_objects))
    ... 变换 subject（find_valid 通过）...
    follow_translate/ follow_drop/ follow_rotate_z(carried, ...)
跟随作为**后置一步**（主体变换定稿后做一次），不掺进 find_valid 重采循环，简单稳。
"""
from __future__ import annotations
import numpy as np


def _bb(o):
    return np.asarray(o.get_bound_box())


def resting_on(subject, others, gap=0.08, min_overlap=0.5):
    """others 里"坐落在 subject 顶面上"的物体。收紧判据以免把**旁边相邻**的物误当承载物：
      ① 底部贴近 subject 顶面（gap 内，收到 8cm）；
      ② 承载物**水平中心**必须落在 subject 顶面 footprint 内（是"压在上面"而非"挨在旁边"）；
      ③ 大部分（≥50%）底面压在 subject 上。
    """
    try:
        sb = _bb(subject)
    except Exception:
        return []
    s_top = float(sb.max(0)[2])
    s_lo, s_hi = sb.min(0), sb.max(0)
    out = []
    for o in others:
        if o is subject:
            continue
        try:
            ob = _bb(o)
        except Exception:
            continue
        if abs(float(ob.min(0)[2]) - s_top) > gap:          # ① 底部不在主体顶面高度
            continue
        cx = 0.5 * float(ob.min(0)[0] + ob.max(0)[0])
        cy = 0.5 * float(ob.min(0)[1] + ob.max(0)[1])
        if not (s_lo[0] <= cx <= s_hi[0] and s_lo[1] <= cy <= s_hi[1]):   # ② 中心须在主体顶面内
            continue
        ix = min(s_hi[0], ob.max(0)[0]) - max(s_lo[0], ob.min(0)[0])
        iy = min(s_hi[1], ob.max(0)[1]) - max(s_lo[1], ob.min(0)[1])
        if ix <= 0 or iy <= 0:
            continue
        area_o = float((ob.max(0)[0] - ob.min(0)[0]) * (ob.max(0)[1] - ob.min(0)[1]))
        if float(ix * iy) >= min_overlap * max(1e-6, area_o):   # ③ 大部分底面压在主体上
            out.append(o)
    return out


def snapshot(objs):
    """记录承载物变换前的位姿，供跟随时增量应用。"""
    snap = []
    for o in objs:
        try:
            snap.append((o, np.array(o.get_location(), float), np.array(o.get_rotation_euler(), float)))
        except Exception:
            pass
    return snap


def follow_translate(snap, delta):
    """move：承载物随主体平移同一位移（继续压在顶面上）。"""
    delta = np.asarray(delta, float)
    for o, loc, _rot in snap:
        try:
            o.set_location((loc + delta).tolist())
        except Exception:
            pass


def follow_drop(snap, dz):
    """（旧）纯竖直下落 dz。scale 请用 follow_scale_top（还要随顶面缩放向中心收）。"""
    for o, loc, _rot in snap:
        try:
            o.set_location([float(loc[0]), float(loc[1]), float(loc[2]) - float(dz)])
        except Exception:
            pass


def follow_scale_top(snap, center_xy, factor, dz):
    """scale：主体缩放后顶面既**下降 dz**、footprint 又按 factor **缩放**。承载物要随之：
    水平位置按 factor 向主体中心收（否则缩小后悬在新顶面外），竖直落到新顶面。"""
    cx, cy = float(center_xy[0]), float(center_xy[1])
    f = float(factor)
    for o, loc, _rot in snap:
        try:
            nx = cx + (float(loc[0]) - cx) * f
            ny = cy + (float(loc[1]) - cy) * f
            o.set_location([nx, ny, float(loc[2]) - float(dz)])
        except Exception:
            pass


def follow_rotate_z(snap, center_xy, ang):
    """rotate(绕竖轴)：承载物绕主体竖轴一起转（位置绕心旋转 + 自身朝向加 ang）。"""
    c, s = float(np.cos(ang)), float(np.sin(ang))
    cx, cy = float(center_xy[0]), float(center_xy[1])
    for o, loc, rot in snap:
        try:
            dx, dy = float(loc[0]) - cx, float(loc[1]) - cy
            o.set_location([cx + c * dx - s * dy, cy + s * dx + c * dy, float(loc[2])])
            o.set_rotation_euler([float(rot[0]), float(rot[1]), float(rot[2]) + float(ang)])
        except Exception:
            pass
