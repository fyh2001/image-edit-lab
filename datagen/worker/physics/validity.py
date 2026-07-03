"""
物理有效性检查（混合策略：解析式 + 物理沉降）。

解析式（快、可控，用于 move/scale/rotate）：
  - collides()        碰撞/穿模检测（BVH overlap）
  - support_gap()     悬空检测（向下射线到支撑面的间隙）
  - reseat()          竖直落到支撑面
  - in_bounds()       房间/场景边界检测
物理沉降（自然、鲁棒，用于 add/replace）：
  - settle_physics()  丢进物理引擎落稳
通用：
  - find_valid()      拒绝采样循环
  - change_is_visible() 渲染后比对，确保变化在画面里可见（纯 numpy，可单测）

※ Blender/BlenderProc API（BVHTree、scene.ray_cast、simulate_physics）在不同
  版本签名可能不同，已尽量隔离在小函数里，第一次用请 `blenderproc debug` 核对。
"""
from __future__ import annotations
from typing import Callable, Optional, Tuple
import numpy as np

CONTACT_EPS = 1e-3   # 1mm 接触容差，避免 z-fighting / 浮点抖动


# ----------------------- 解析式检查 -----------------------

def _bvh_of(obj):
    """对单个 bproc MeshObject 建**世界系** BVH。

    注意：BVHTree.FromObject(obj, deps) 得到的是物体**局部坐标**下的 BVH（不含
    matrix_world）。若直接用它做 overlap，所有物体都被当成在各自原点 → 永远误判碰撞。
    所以这里用 bmesh 取出 evaluated mesh，再用 matrix_world 把顶点烘到世界系。
    """
    import bpy
    import bmesh
    from mathutils.bvhtree import BVHTree
    bobj = obj.blender_obj
    deps = bpy.context.evaluated_depsgraph_get()
    eval_obj = bobj.evaluated_get(deps)
    bm = bmesh.new()
    try:
        bm.from_mesh(eval_obj.to_mesh())
        bm.transform(bobj.matrix_world)          # 局部 → 世界系
        return BVHTree.FromBMesh(bm)
    finally:
        bm.free()
        try:
            eval_obj.to_mesh_clear()
        except Exception:
            pass


def collides(subject, others, ignore=()) -> bool:
    """subject 是否与 others 中任一物体网格相交。"""
    try:
        from mathutils.bvhtree import BVHTree  # noqa
        sub_bvh = _bvh_of(subject)
        for o in others:
            if o is subject or o in ignore:
                continue
            try:
                if sub_bvh.overlap(_bvh_of(o)):
                    return True
            except Exception:
                continue
        return False
    except Exception as e:
        print(f"[validity] collides 检查不可用，跳过: {e}")
        return False


def contacts(subject, others):
    """返回 subject 当前位姿下与之网格相交的物体列表（**基线接触**）。

    真实稠密场景里家具本来就挨着/微交叠（椅子塞桌下、沙发贴柜）。编辑前记下这些基线接触，
    编辑后把它们传给 collides 的 ignore——只在撞到**新**邻居时才判无效，避免稠密场景被大量误杀。
    """
    out = []
    try:
        from mathutils.bvhtree import BVHTree  # noqa
        sub_bvh = _bvh_of(subject)
        for o in others:
            if o is subject:
                continue
            try:
                if sub_bvh.overlap(_bvh_of(o)):
                    out.append(o)
            except Exception:
                continue
    except Exception as e:
        print(f"[validity] contacts 检查不可用，跳过: {e}")
    return out


def _bottom_center(obj):
    bbox = np.asarray(obj.get_bound_box())     # 8x3 世界系
    lo = bbox.min(axis=0)
    hi = bbox.max(axis=0)
    return np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]]), lo, hi


def support_gap(obj) -> Optional[float]:
    """从物体底部中心向 -Z 射线，返回到下方支撑面的间隙（米）。

    None 表示下方无任何面（纯悬空且非故意）。0 附近表示贴合。
    """
    import bpy
    origin, lo, hi = _bottom_center(obj)
    scene = bpy.context.scene
    deps = bpy.context.evaluated_depsgraph_get()
    origin_up = origin + np.array([0, 0, 1e-3])
    try:
        hit, loc, normal, idx, hobj, mat = scene.ray_cast(
            deps, origin_up.tolist(), (0, 0, -1)
        )
    except Exception as e:
        print(f"[validity] ray_cast 不可用: {e}")
        return None
    if not hit:
        return None
    return float(origin[2] - loc[2])


def reseat(obj, max_drop: float = 5.0) -> bool:
    """把物体竖直下落，使底部贴到下方支撑面（留 CONTACT_EPS）。返回是否成功。"""
    gap = support_gap(obj)
    if gap is None or gap > max_drop:
        return False
    loc = np.asarray(obj.get_location(), dtype=float)
    loc[2] -= (gap - CONTACT_EPS)
    obj.set_location(loc.tolist())
    return True


def in_bounds(obj, bounds_min, bounds_max, margin: float = 0.0) -> bool:
    """物体包围盒是否完全落在 [bounds_min, bounds_max] 内。"""
    if bounds_min is None or bounds_max is None:
        return True
    bbox = np.asarray(obj.get_bound_box())
    lo, hi = bbox.min(axis=0), bbox.max(axis=0)
    bmin = np.asarray(bounds_min) + margin
    bmax = np.asarray(bounds_max) - margin
    return bool(np.all(lo >= bmin) and np.all(hi <= bmax))


# ----------------------- 相机视角检查（遮挡 / 屏占比）-----------------------

def _cam_location(view: int = 0):
    import blenderproc as bproc
    M = np.asarray(bproc.camera.get_camera_pose(frame=view))
    return M[:3, 3]


def visible_fraction(obj, view: int = 0) -> float:
    """主体在某机位下的可见比例（射线从相机投向主体的采样点，看是否被别的物体挡住）。

    返回 0~1。≈0 表示被完全遮挡。用于剔除"移动后藏到别的物体后面"的废对。
    """
    import bpy
    try:
        cam = _cam_location(view)
        bb = np.asarray(obj.get_bound_box())          # 8x3
        pts = list(bb) + [bb.mean(axis=0)]            # 8 角 + 中心
        target = obj.blender_obj
        deps = bpy.context.evaluated_depsgraph_get()
        scene = bpy.context.scene
        vis, tot = 0, 0
        for p in pts:
            p = np.asarray(p, dtype=float)
            d = p - cam
            dist = float(np.linalg.norm(d))
            if dist < 1e-6:
                continue
            dirn = d / dist
            hit, loc, nrm, idx, hobj, mat = scene.ray_cast(
                deps, (cam + dirn * 1e-3).tolist(), dirn.tolist())
            tot += 1
            # 第一命中是主体本身 / 或几乎到达采样点都算"该点可见"
            if (not hit) or (hobj == target) or \
               (np.linalg.norm(np.asarray(loc) - cam) >= dist - 5e-3):
                vis += 1
        return vis / max(tot, 1)
    except Exception as e:
        print(f"[validity] visible_fraction 不可用，跳过: {e}")
        return 1.0


def projected_area_ratio(obj, resolution, view: int = 0):
    """主体投影到画面的包围框面积占整张图的比例（估计屏上大小）。

    用于：太小（缩小后变成几个像素）或太大（放大到撑满/出框）都拒绝。
    返回 None 表示投影 API 不可用（不拦截）。
    """
    import blenderproc as bproc
    try:
        bb = np.asarray(obj.get_bound_box())
        px = np.asarray(bproc.camera.project_points(bb, frame=view))  # 8x2
        W, H = resolution
        xs = np.clip(px[:, 0], 0, W)
        ys = np.clip(px[:, 1], 0, H)
        area = (xs.max() - xs.min()) * (ys.max() - ys.min())
        return float(area / (W * H))
    except Exception as e:
        print(f"[validity] projected_area_ratio 不可用，跳过: {e}")
        return None


def camera_quality_ok(obj, resolution, *, min_visible=0.1,
                      min_area=0.005, max_area=0.9, view=0):
    """综合相机侧质量门槛：未被完全遮挡 + 屏占比在 [min_area, max_area] 内。"""
    if visible_fraction(obj, view) < min_visible:
        return False, "occluded"
    ratio = projected_area_ratio(obj, resolution, view)
    if ratio is None:
        return True, "area_unchecked"
    if ratio < min_area:
        return False, "too_small"
    if ratio > max_area:
        return False, "too_large"
    return True, "ok"


# ----------------------- 物理沉降 -----------------------

def settle_physics(active_obj, passive_objs, max_sim: float = 4.0) -> None:
    """让 active_obj 在重力下落稳到 passive_objs 上。**固定物理步长/求解迭代**以求同 seed 可复现
    （Blender 物理在固定 substeps + 相同初始态下是确定的；不固定则同 seed 两次落点可能不同）。"""
    import blenderproc as bproc
    try:
        import bpy
        scene = bpy.context.scene
        if scene.rigidbody_world is None:
            try:
                bpy.ops.rigidbody.world_add()
            except Exception:
                pass
        rw = scene.rigidbody_world
        if rw is not None:                       # 固定步长 → 可复现
            try:
                rw.substeps_per_frame = 20
                rw.solver_iterations = 20
            except Exception:
                pass
        active_obj.enable_rigidbody(active=True)
        for o in passive_objs:
            if o is active_obj:
                continue
            o.enable_rigidbody(active=False)
        bproc.object.simulate_physics_and_fix_final_poses(
            min_simulation_time=1.0, max_simulation_time=max_sim,
            check_object_interval=0.5,
        )
    except Exception as e:
        print(f"[validity] 物理沉降不可用，回退解析 reseat: {e}")
        reseat(active_obj)


# ----------------------- 通用 -----------------------

def find_valid(sample_fn: Callable[[], object],
               check_fn: Callable[[object], bool],
               max_attempts: int = 30) -> Tuple[bool, object, int]:
    """拒绝采样：反复 sample 直到 check 通过。

    Returns: (是否成功, 最后一次候选, 尝试次数)
    """
    cand = None
    for i in range(1, max_attempts + 1):
        cand = sample_fn()
        if check_fn(cand):
            return True, cand, i
    return False, cand, max_attempts


def projected_change_ratio(pts_before_world, pts_after_world, res) -> float:
    """把编辑前/后主体的世界系点集投影到画面，估算**画面上的变化幅度**（0~1）。

    不用渲染就能预判"这次变换在画面里看不看得出来"：取两组投影点各自的 2D 包围盒，
    返回 (并集 - 交集) / 图像面积 的近似。沿视线方向的小移动、远处的小幅缩放/旋转 → 值很小，
    让 find_valid 继续重采，把"渲染后才发现变化不可见"提前拦下、转成成功对。API 不可用则返回 1.0（不拦）。
    """
    try:
        import blenderproc as bproc
        W, H = (res or [512, 512])[0], (res or [512, 512])[1]
        p0 = np.asarray(bproc.camera.project_points(np.asarray(pts_before_world, dtype=float)))
        p1 = np.asarray(bproc.camera.project_points(np.asarray(pts_after_world, dtype=float)))

        def _box(p):
            return (max(0.0, float(p[:, 0].min())), max(0.0, float(p[:, 1].min())),
                    min(float(W), float(p[:, 0].max())), min(float(H), float(p[:, 1].max())))

        b0, b1 = _box(p0), _box(p1)

        def _area(b):
            return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])

        ix = (max(b0[0], b1[0]), max(b0[1], b1[1]), min(b0[2], b1[2]), min(b0[3], b1[3]))
        inter = _area(ix)
        union = _area(b0) + _area(b1) - inter
        return float((union - inter) / (W * H)) if W * H > 0 else 1.0
    except Exception:
        return 1.0


def change_is_visible(before_img, after_img,
                      min_ratio: float = 0.01,
                      pix_delta: int = 12) -> Tuple[bool, float]:
    """比对 before/after，判断变化是否在画面里可见（纯 numpy，可单测）。

    返回 (是否可见, 变化像素占比)。用于剔除"看似没变"的废对
    （深度方向小移动、对称物体旋转、被完全遮挡等）。
    """
    a = np.asarray(before_img).astype(np.int16)
    b = np.asarray(after_img).astype(np.int16)
    if a.shape != b.shape:
        return True, 1.0
    diff = np.abs(a - b).max(axis=-1)         # 每像素最大通道差
    changed = float((diff > pix_delta).mean())
    return changed >= min_ratio, changed
