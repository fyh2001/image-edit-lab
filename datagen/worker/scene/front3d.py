"""
front3d 场景：加载一个 3D-FRONT 带家具房间，自动选一件家具当编辑主体，
在房间内采样「看得见主体」的相机机位。与 tabletop 并存，config 里一键切换。

数据准备（3D-FRONT 三件套，需各自申请/下载）：
    - 3D-FRONT 场景 json 目录   (front_json_dir)
    - 3D-FUTURE 模型目录        (future_model_dir)
    - 3D-FRONT-texture 目录     (front_texture_dir)

API 参考 BlenderProc 官方示例 examples/datasets/front_3d(_with_improved_mat)。
※ load_front3d 的参数名、Front3DPointInRoomSampler、light_surface 等在不同
  版本可能略有差异，第一次用 `blenderproc debug` 在 GUI 里核对一遍。
"""
from __future__ import annotations
import os
import glob
import numpy as np
import blenderproc as bproc

from datagen.worker.scene.base import SceneBuilder
from datagen.worker.registry import register_scene

# 3D-FRONT 里属于「结构」的物体，不作为可编辑主体
_STRUCTURE_KEYWORDS = (
    "wall", "floor", "ceiling", "baseboard", "pocket", "front", "back",
    "slab", "hole", "door", "window", "column", "beam", "ceil", "cornice",
)


@register_scene("front3d")
class Front3DScene(SceneBuilder):
    def build(self, ctx):
        rng = ctx.rng
        p = self.params
        front_json_dir = p["front_json_dir"]
        future_model_dir = p["future_model_dir"]
        front_texture_dir = p["front_texture_dir"]

        # 1) 随机选一个房间 json（按 seed 可复现）
        jsons = sorted(glob.glob(os.path.join(front_json_dir, "*.json")))
        if not jsons:
            raise RuntimeError(f"在 {front_json_dir} 没找到 3D-FRONT json，请先下载数据。")
        # 可选：排除「已用账本」里用过的场景，保证批次间不重复
        used = _load_used_scenes(p.get("exclude_used_ledger"))
        if used:
            avail = [j for j in jsons if os.path.basename(j) not in used]
            jsons = avail or jsons   # 万一全用过了，退回全集，避免空跑
        scene_json = str(rng.choice(jsons))
        # 记录本 job 用的场景（供「已用账本」过滤）
        ctx.extras["used_front3d_scene"] = os.path.basename(scene_json)

        # 2) 加载房间（含家具）
        mapping = bproc.utility.LabelIdMapping.from_csv(
            bproc.utility.resolve_resource(os.path.join("front_3D", "3D_front_mapping.csv"))
        )
        loaded = bproc.loader.load_front3d(
            json_path=scene_json,
            future_model_path=future_model_dir,
            front_3D_texture_path=front_texture_dir,
            label_mapping=mapping,
        )
        mesh_objs = [o for o in loaded if isinstance(o, bproc.types.MeshObject)]
        ctx.all_objects.extend(mesh_objs)

        # 场景几何描述（房间包围盒 → 地面/天花板/墙边界），供 placement / validity
        ctx.extras["scene_geom"] = _room_geom(mesh_objs)
        ctx.extras["subject_support"] = "ground"

        # 3) 室内打光：点亮灯具和天花板（3D-FRONT 自身不带灯光）
        _light_room(loaded)

        # 4) 选一件家具当编辑主体
        subject = self._pick_subject(mesh_objs, rng)
        ctx.subject = subject
        ctx.distractors = [o for o in mesh_objs if o is not subject]

        # 5) 在房间内采样能看见主体的相机机位（before/after 共用）
        n_views = int(self.params.get("camera_views", 1))
        _sample_cameras_facing_subject(loaded, mesh_objs, subject, rng, n_views)

    def _pick_subject(self, mesh_objs, rng):
        candidates = [o for o in mesh_objs if _is_furniture(o)]
        if not candidates:
            raise RuntimeError("该房间没有可用作主体的家具，换一个场景 json 重试。")
        return candidates[int(rng.integers(0, len(candidates)))]


def _room_geom(mesh_objs) -> dict:
    """从房间所有网格的世界包围盒，估计地面/天花板高度与房间边界。"""
    import numpy as np
    mins, maxs = [], []
    for o in mesh_objs:
        try:
            bb = np.asarray(o.get_bound_box())
            mins.append(bb.min(axis=0))
            maxs.append(bb.max(axis=0))
        except Exception:
            continue
    if not mins:
        return {"ground_z": 0.0, "ceiling_z": None,
                "bounds_min": None, "bounds_max": None}
    bmin = np.min(mins, axis=0)
    bmax = np.max(maxs, axis=0)
    return {
        "ground_z": float(bmin[2]),
        "ceiling_z": float(bmax[2]),
        "bounds_min": [float(x) for x in bmin],
        "bounds_max": [float(x) for x in bmax],
    }


def _load_used_scenes(ledger_path) -> set:
    """从已用账本读取已使用过的场景文件名（不存在则返回空集）。"""
    if not ledger_path or not os.path.exists(ledger_path):
        return set()
    try:
        import json
        with open(ledger_path, encoding="utf-8") as f:
            return set(json.load(f).get("front3d_scenes", []))
    except Exception:
        return set()


def _is_furniture(obj) -> bool:
    name = (obj.get_name() or "").lower()
    return not any(k in name for k in _STRUCTURE_KEYWORDS)


def _light_room(loaded):
    """点亮灯具表面 + 天花板做柔光，让室内不全黑。"""
    try:
        lamps = bproc.filter.by_attr(loaded, "name", ".*[Ll]amp.*", regex=True)
        if lamps:
            bproc.lighting.light_surface(lamps, emission_strength=15.0)
    except Exception as e:
        print(f"[front3d] 灯具点亮跳过: {e}")
    try:
        ceilings = bproc.filter.by_attr(loaded, "name", "[Cc]eiling.*", regex=True)
        if ceilings:
            bproc.lighting.light_surface(ceilings, emission_strength=2.0)
    except Exception as e:
        print(f"[front3d] 天花板补光跳过: {e}")


def _sample_cameras_facing_subject(loaded, mesh_objs, subject, rng, n_views,
                                   max_tries=2000):
    """在房间内随机站位，朝向主体，并确保视线不被墙挡死。"""
    point_sampler = bproc.sampler.Front3DPointInRoomSampler(loaded)
    bvh = bproc.object.create_bvh_tree_multi_objects(mesh_objs)
    target = np.array(subject.get_location(), dtype=float)

    poses, tries = 0, 0
    while poses < n_views and tries < max_tries:
        tries += 1
        loc = point_sampler.sample(height=float(rng.uniform(1.2, 1.8)))
        loc = np.array(loc, dtype=float)
        forward = target - loc
        dist = np.linalg.norm(forward)
        if dist < 0.8 or dist > 6.0:          # 离主体太近/太远都跳过
            continue
        rot = bproc.camera.rotation_from_forward_vec(
            forward, inplane_rot=float(rng.uniform(-0.05, 0.05))
        )
        cam2world = bproc.math.build_transformation_mat(loc, rot)
        # 主体要在视野内、且视线无遮挡（API 不存在时降级为不检查，避免崩）
        if not _obstacle_ok(cam2world, bvh):
            continue
        if not _in_frustum(target, cam2world):
            continue
        bproc.camera.add_camera_pose(cam2world)
        poses += 1

    if poses == 0:
        # 兜底：直接放一个朝向主体的机位，保证不空跑
        loc = target + np.array([2.5, 2.5, 1.5])
        rot = bproc.camera.rotation_from_forward_vec(target - loc)
        bproc.camera.add_camera_pose(bproc.math.build_transformation_mat(loc, rot))


def _obstacle_ok(cam2world, bvh) -> bool:
    try:
        return bproc.camera.perform_obstacle_in_view_check(
            cam2world, {"min": 0.5}, bvh, sqrt_number_of_rays=10
        )
    except Exception:
        return True   # API 签名不符就不拦截


def _in_frustum(point, cam2world) -> bool:
    try:
        return bproc.camera.is_point_inside_camera_frustum(point, cam2world)
    except Exception:
        return True
