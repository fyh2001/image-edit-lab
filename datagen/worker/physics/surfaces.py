"""
表面感知放置：在场景里找**真实水平支撑面**（桌面/台面/柜顶/座面/冰箱顶/地面），
把物体自然地放上去——不悬边、放得下、贴着面。

做法：从上往下射线打到某点的上表面 → 检查法线朝上（水平面）、不是墙/天花板；
再从主体四个底角向下射线，四角都落在**同一高度的面**上才算"放得下不悬边"。
纯依赖 scene.ray_cast，第一次用建议 `blenderproc debug` 核对。
"""
from __future__ import annotations
import numpy as np


def _raycast_down(x, y, z_top):
    import bpy
    scene = bpy.context.scene
    deps = bpy.context.evaluated_depsgraph_get()
    hit, loc, nrm, idx, obj, mat = scene.ray_cast(deps, (float(x), float(y), float(z_top)), (0, 0, -1))
    return hit, np.asarray(loc, dtype=float), np.asarray(nrm, dtype=float), obj


def _corners_supported(x, y, z, s_halfxy, edge_margin, z_tol, z_top):
    """主体四个底角是否都落在同高度、够平的面上（放得下、不悬边）。"""
    for dx, dy in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
        h2, l2, n2, _o = _raycast_down(x + dx * (s_halfxy + edge_margin),
                                       y + dy * (s_halfxy + edge_margin), z_top)
        if (not h2) or abs(float(l2[2]) - z) > z_tol or n2[2] < 0.6:
            return False
    return True


def find_support_point(ctx, subject, rng, edge_margin: float = 0.05,
                       z_tol: float = 0.05, prefer_furniture: bool = True,
                       max_tries: int = 200, in_view=None, prefer_on_object: bool = False,
                       max_support_h: float = 1.4, near=None, near_dist: float = 3.5):
    """把主体放到某个**物体的顶面**上（桌面/台面/柜顶/座面/冰箱顶，或桌上的笔记本电脑顶）。
    放不下就返回 None。

    做法：先挑"顶面够高够大能放下主体"的物体，再在它顶面 footprint 内采点、射线确认、
    四角检查不悬边。返回 (location[x,y,z], support_label, hit_blender_obj) 或 None。

    in_view: 可选 callable(location)->bool；给了就要求落点在画面里（否则 move 到的
        家具可能在广角外，after 里主体"凭空消失"像删除，是个坏配对）。
    prefer_on_object: 偏向"已经架在别的东西上的小物体"当支撑（如桌上的笔记本电脑），
        实现"把本子放到桌上那台笔记本上"这种更细的叠放。
    """
    ground = float(ctx.extras.get("scene_geom", {}).get("ground_z", 0.0))
    sb = np.asarray(subject.get_bound_box())
    s_half = (sb.max(axis=0) - sb.min(axis=0)) / 2.0
    s_halfxy = float(max(s_half[0], s_half[1]))
    s_h = float(sb.max(axis=0)[2] - sb.min(axis=0)[2])
    need = s_halfxy + edge_margin
    # 支撑物顶面要**明显大于**主体 footprint（留 1.4× 余量）——否则像台灯 shade 这种勉强够的、
    # 又细又空的会被选中，物体架上去要么悬边要么插进支撑体（穿模）。比 need 更严，只用于筛支撑物。
    support_need = s_halfxy * 1.4 + edge_margin
    near_xy = None if near is None else np.asarray(near, dtype=float)[:2]

    # 候选支撑物：顶面高于地面、顶面 footprint 半径足够放下主体
    cands, elevated = [], []
    for o in list(ctx.all_objects):
        if o is subject:
            continue
        try:
            bb = np.asarray(o.get_bound_box())
        except Exception:
            continue
        top = float(bb.max(axis=0)[2])
        base = float(bb.min(axis=0)[2])
        half = (bb.max(axis=0) - bb.min(axis=0)) / 2.0
        # 大场景常是多房间：只收**相机正框着的那块区域**附近的支撑面，否则会选到 18m 外
        # 另一个房间的桌面（在视锥锥体延长线里但实际被墙挡住 → 变化不可见）。
        if near_xy is not None:
            c_xy = ((bb.max(axis=0) + bb.min(axis=0)) / 2.0)[:2]
            if float(np.linalg.norm(c_xy - near_xy)) > near_dist:
                continue
        # 顶面要在"看得清的桌/台/柜/座面高度"区间：太高（衣柜顶/吊柜/接近天花板）相机拍不到、
        # 还会被自身遮挡 → 变化不可见。cap 到 ground+max_support_h。
        if (ground + 0.08 < top <= ground + max_support_h
                and min(float(half[0]), float(half[1])) >= support_need):
            cands.append((o, bb))
            if base > ground + 0.15:                    # 底部离地 → 它本身架在别的东西上
                elevated.append((o, bb))
    if not cands:
        return None

    for _ in range(max_tries):
        # 偏向"架在别的东西上的小物体"（桌上的笔记本电脑）→ 叠更细的层
        pool = elevated if (prefer_on_object and elevated and rng.uniform() < 0.6) else cands
        o, bb = pool[int(rng.integers(0, len(pool)))]
        z_top = float(bb.max(axis=0)[2]) + 0.5
        x = float(rng.uniform(bb.min(axis=0)[0] + need, bb.max(axis=0)[0] - need))
        y = float(rng.uniform(bb.min(axis=0)[1] + need, bb.max(axis=0)[1] - need))
        hit, loc, nrm, obj = _raycast_down(x, y, z_top)
        if (not hit) or nrm[2] < 0.75 or float(loc[2]) <= ground + 0.08:
            continue
        z = float(loc[2])
        if not _corners_supported(x, y, z, s_halfxy, edge_margin, z_tol, z_top):
            continue
        location = [x, y, z + s_h / 2.0 + 1e-3]
        if in_view is not None and not in_view(location):
            continue                                  # 落点在画面外 → 换一个家具/点
        hit_obj = obj if obj is not None else o.blender_obj
        return location, f"object:{hit_obj.name}", hit_obj
    return None
