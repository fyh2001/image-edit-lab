"""
move 的「放置模式」目标采样。吸附/悬空不是单独编辑类型，而是 move 的落点之一。

依赖场景几何描述 ctx.extras["scene_geom"]（由 SceneBuilder 填）：
  {
    "ground_z":  0.0,
    "ceiling_z": 2.8 | None,      # 无天花板（tabletop）则 None
    "bounds_min": [x,y,z] | None, # 场景/房间包围盒
    "bounds_max": [x,y,z] | None,
  }
ctx.distractors 提供"可叠放的物体"（object_top 模式用）。

每个采样器只**提议**一个目标位置；真正的贴合/碰撞由 validity 在算子里把关
（apply 后 reseat + collide + in_bounds + 可见性）。
"""
from __future__ import annotations
from typing import Dict
import numpy as np


PLACEMENT_MODES = ["support_surface", "object_top", "ceiling", "wall", "floating"]


def available_modes(geom: Dict, distractors) -> list:
    """按当前场景几何，过滤出可用的放置模式。"""
    modes = ["support_surface", "floating"]
    if distractors:
        modes.append("object_top")
    if geom.get("ceiling_z") is not None:
        modes.append("ceiling")
    if geom.get("bounds_min") is not None and geom.get("ceiling_z") is not None:
        modes.append("wall")            # 有完整房间才好定义墙
    return modes


def _bbox(obj):
    bb = np.asarray(obj.get_bound_box())
    return bb.min(axis=0), bb.max(axis=0)


def _camera_in_view(location) -> bool:
    """落点是否**相机真能看到**：在视锥内 + 从相机到落点这条视线没被墙/家具挡住。

    只用视锥不够——大场景里视锥锥体会延伸到别的房间，18m 外被墙挡住的桌面也算"在锥内"。
    再补一条相机→落点的遮挡射线：中途先撞上别的东西就是看不见。API 不符时不拦截。
    """
    try:
        import bpy
        import numpy as np
        import blenderproc as bproc
        pose = np.asarray(bproc.camera.get_camera_pose())
        loc = np.asarray(location, dtype=float)
        if not bool(bproc.camera.is_point_inside_camera_frustum(loc, pose)):
            return False
        cam = pose[:3, 3]
        d = loc - cam
        dist = float(np.linalg.norm(d))
        if dist < 1e-3:
            return True
        scene = bpy.context.scene
        deps = bpy.context.evaluated_depsgraph_get()
        hit, hitloc, _n, _i, _o, _m = scene.ray_cast(deps, cam, (d / dist).tolist())
        if hit and float(np.linalg.norm(np.asarray(hitloc) - cam)) < dist - 0.15:
            return False                              # 视线中途撞墙/家具 → 被挡住
        return True
    except Exception:
        return True


def _safe_uniform(rng, lo, hi):
    """low>high（物体比房间还高、房间比留边还窄等）时不崩，退化为中点；合法性交给 check() 把关。"""
    lo, hi = float(lo), float(hi)
    if hi <= lo:
        return (lo + hi) / 2.0
    return float(rng.uniform(lo, hi))


def _rand_xy(rng, geom, margin=0.3):
    bmin, bmax = geom.get("bounds_min"), geom.get("bounds_max")
    if bmin is None:
        # tabletop：在原点附近一个方形区域
        return rng.uniform(-1.2, 1.2, size=2)
    x = _safe_uniform(rng, bmin[0] + margin, bmax[0] - margin)
    y = _safe_uniform(rng, bmin[1] + margin, bmax[1] - margin)
    return np.array([x, y])


def sample_move_target(mode: str, ctx, subject, rng, params: Dict) -> Dict:
    """为给定放置模式提议一个目标位姿。

    Returns:
        {"location": [x,y,z], "support": <label>, "note": <str>}
    """
    geom = ctx.extras.get("scene_geom", {})
    lo, hi = _bbox(subject)
    half_h = (hi[2] - lo[2]) / 2.0
    ground_z = geom.get("ground_z", 0.0)

    if mode == "support_surface":
        xy = _rand_xy(rng, geom)
        z = ground_z + half_h                    # 之后 reseat 精修
        return {"location": [xy[0], xy[1], z], "support": "ground",
                "note": "onto the floor"}

    if mode == "object_top":
        # 表面感知：射线找真实水平顶面（桌面/台面/柜顶/座面/冰箱顶），放得下不悬边
        from datagen.worker.physics import surfaces
        # move：把主体挪到它当前位置附近的家具顶面（同一块可见区域，别挪到别的房间）
        near = None
        try:
            near = list(subject.get_location())
        except Exception:
            near = None
        res = surfaces.find_support_point(ctx, subject, rng, prefer_furniture=True,
                                          in_view=_camera_in_view, near=near)
        if res is not None:
            loc, label, sobj = res
            note = "onto a surface"
            try:
                cat = sobj.get("category") or sobj.get("noun")
                if cat:
                    note = f"on top of the {str(cat).replace('_', ' ')}"
            except Exception:
                pass
            return {"location": loc, "support": label, "note": note}
        # 兜底：老的"包围盒顶面中心"（找不到合适支撑面时）
        target = ctx.distractors[int(rng.integers(0, len(ctx.distractors)))]
        t_lo, t_hi = _bbox(target)
        cx = (t_lo[0] + t_hi[0]) / 2
        cy = (t_lo[1] + t_hi[1]) / 2
        z = t_hi[2] + half_h
        return {"location": [cx, cy, z],
                "support": f"object:{target.get_name()}",
                "note": "on top of another object"}

    if mode == "ceiling":
        ceil = geom["ceiling_z"]
        xy = _rand_xy(rng, geom)
        z = ceil - half_h                         # 顶部贴天花板下表面
        return {"location": [xy[0], xy[1], z], "support": "ceiling",
                "note": "up on the ceiling"}

    if mode == "wall":
        bmin, bmax = np.asarray(geom["bounds_min"]), np.asarray(geom["bounds_max"])
        half = (hi - lo) / 2.0
        side = rng.integers(0, 4)
        z = _safe_uniform(rng, ground_z + half_h, geom["ceiling_z"] - half_h)
        if side == 0:   # 贴 -X 墙
            loc = [bmin[0] + half[0], _rand_xy(rng, geom)[1], z]
        elif side == 1: # +X
            loc = [bmax[0] - half[0], _rand_xy(rng, geom)[1], z]
        elif side == 2: # -Y
            loc = [_rand_xy(rng, geom)[0], bmin[1] + half[1], z]
        else:           # +Y
            loc = [_rand_xy(rng, geom)[0], bmax[1] - half[1], z]
        return {"location": loc, "support": "wall", "note": "on the wall"}

    # floating：故意悬空
    xy = _rand_xy(rng, geom)
    if geom.get("ceiling_z") is not None:
        z = _safe_uniform(rng, ground_z + 2 * half_h, geom["ceiling_z"] - half_h)
    else:
        z = ground_z + rng.uniform(0.6, 1.6) + half_h
    return {"location": [xy[0], xy[1], z], "support": "none",
            "note": "floating in the air"}
