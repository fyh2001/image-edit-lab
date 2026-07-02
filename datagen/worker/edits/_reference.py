"""主体指代消歧：给"被编辑的物体"一个在**画面里无歧义**的指代短语。

真实房间常有多把椅子/多个柜子，"delete the chair" 会指代不清（before/after 只改了其中一个，
却教模型任选一个）。这里只对**当前画面里可见的同类物体**做消歧（镜头外/被挡的不算）：
- 画面里没有同类可见物 → 直接用名词 "chair"；
- 有 → 加一个能唯一区分主体的空间限定词（on the left / on the right / nearest / farthest）；
- 都无法唯一区分 → 返回 None，调用方丢弃该对（宁可丢也不出歧义标签）。
"""
from __future__ import annotations
import numpy as np

from datagen.worker.edits._common import noun as _display_noun


def _center(o):
    bb = np.asarray(o.get_bound_box())
    return (bb.max(axis=0) + bb.min(axis=0)) / 2.0


def _cam_pose():
    import blenderproc as bproc
    return np.asarray(bproc.camera.get_camera_pose())


def _visible(o, pose):
    """物体中心是否在画面里且没被挡（在视锥内 + 相机→中心视线未中途撞别的东西）。"""
    import bpy
    import blenderproc as bproc
    c = _center(o)
    try:
        if not bproc.camera.is_point_inside_camera_frustum(c, pose):
            return False
    except Exception:
        return True
    cam = pose[:3, 3]
    d = c - cam
    dist = float(np.linalg.norm(d))
    if dist < 1e-3:
        return True
    try:
        deps = bpy.context.evaluated_depsgraph_get()
        hit, loc, _n, _i, _ob, _m = bpy.context.scene.ray_cast(deps, cam.tolist(), (d / dist).tolist())
        if hit and float(np.linalg.norm(np.asarray(loc) - cam)) < dist - 0.2:
            return False
    except Exception:
        pass
    return True


def _cp(o, key):
    try:
        return o.get_cp(key)
    except Exception:
        return None


def _unique_min(vals):
    return vals[0] < min(vals[1:]) if len(vals) > 1 else True


def _unique_max(vals):
    return vals[0] > max(vals[1:]) if len(vals) > 1 else True


def subject_phrase(ctx, obj):
    """返回主体的无歧义指代短语（供 "the {phrase}" 用），或 None（无法消歧→丢弃）。"""
    base = _display_noun(obj)
    try:
        pose = _cam_pose()
    except Exception:
        return base                                  # 无相机信息就不纠结
    if not _visible(obj, pose):
        return base                                  # 主体本身不可见 → 交给可见性过滤兜底

    cat = _cp(obj, "category")
    peers = [o for o in ctx.all_objects
             if o is not obj and _cp(o, "category") == cat and _visible(o, pose)]
    if not peers:
        return base                                  # 画面里就一个同类 → 不含糊

    import blenderproc as bproc
    group = [obj] + peers
    cam = pose[:3, 3]
    try:
        pts = np.asarray(bproc.camera.project_points(np.asarray([_center(o) for o in group])))
        xs = [float(p[0]) for p in pts]              # 屏幕横坐标（小=左）
    except Exception:
        xs = None
    dists = [float(np.linalg.norm(_center(o) - cam)) for o in group]  # 到相机距离

    if xs is not None and _unique_min(xs):
        return f"{base} on the left"
    if xs is not None and _unique_max(xs):
        return f"{base} on the right"
    if _unique_min(dists):
        return f"nearest {base}"
    if _unique_max(dists):
        return f"farthest {base}"
    return None                                      # 极值都不占（挤在中间）→ 无法唯一区分
