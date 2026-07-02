"""
HSSD 场景：加载一个 Habitat Synthetic Scenes Dataset 房间（stage 外壳 + 一堆家具实例），
随机选一件家具当编辑主体。数据用 orchestrator/prefetch_hssd.py 预下载到本地。

坐标系：HSSD/Habitat 是 **Y-up**，Blender 是 **Z-up**。glb 导入时 Blender 已把每个网格
Y-up→Z-up（记作 C）；而 scene_instance.json 里的实例变换 M 是 Habitat 世界系下的，所以
物体的 Blender 世界矩阵 = C · M · C⁻¹（对 identity 的 stage 即保持导入态，不会二次翻转）。
"""
from __future__ import annotations
import os
import json
import glob
import numpy as np
import blenderproc as bproc

from datagen.worker.scene.base import SceneBuilder
from datagen.worker.registry import register_scene, build

# Y-up → Z-up： (x,y,z) -> (x,-z,y)
_C = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=float)
_Cinv = np.linalg.inv(_C)


@register_scene("hssd")
class HSSDScene(SceneBuilder):
    def build(self, ctx):
        rng = ctx.rng
        p = self.params
        root = p["data_dir"]
        scene_id = p.get("scene_id")
        if not scene_id:
            cands = sorted(glob.glob(os.path.join(root, "scenes", "*.scene_instance.json")))
            if not cands:
                raise RuntimeError(f"{root}/scenes 下没有 HSSD 场景，请先 prefetch_hssd。")
            scene_id = os.path.basename(str(rng.choice(cands))).split(".")[0]
        ctx.extras["used_hssd_scene"] = scene_id
        spec = json.load(open(os.path.join(root, "scenes", f"{scene_id}.scene_instance.json")))
        semantics = _load_semantics(os.path.join(root, "semantics_objects.csv"))

        # 1) stage（房间外壳：墙/地/天花板）—— 结构件，不参与编辑/碰撞。
        # stage_instance 是 identity，glb 导入器已做 Y-up→Z-up，**保持导入态即可**
        # （别再乘 C，否则会被二次旋转 90°，与按 C·M·C⁻¹ 放置的家具错开一个坐标系）。
        stage_tmpl = spec["stage_instance"]["template_name"]
        stage_glb = os.path.join(root, f"{stage_tmpl}.glb")
        stage_objs = _load_glb(stage_glb) if os.path.exists(stage_glb) else []

        # 2) 家具实例
        editable = []
        for inst in spec.get("object_instances", []):
            t = inst["template_name"]
            path = _resolve_obj(root, t)
            if not path:
                continue
            obj = _load_glb_joined(path)
            if obj is None:
                continue
            M = _C @ _habitat_matrix(inst) @ _Cinv
            obj.blender_obj.matrix_world = _to_matrix(M)
            if not _has_image_texture(obj):           # 贴图已还原的保留真实材质；没有的才兜底中性色
                _neutralize(obj, rng)
            sem = semantics.get(t, {})
            obj.set_cp("asset_uid", f"hssd:{t}")
            obj.set_cp("category", sem.get("category", "object"))
            obj.set_cp("noun", sem.get("noun", "object"))     # 真实名词："bed"/"stool"/...
            if sem.get("description"):
                obj.set_cp("description", sem["description"])  # HSSD 原始物体名当描述
            editable.append(obj)

        if not editable:
            raise RuntimeError(f"HSSD 场景 {scene_id} 没加载到任何可编辑家具。")

        # 3) 选主体 + 其余作干扰物（结构件 stage 不算）。
        # 优先挑「有语义类别 + 体量够大」的家具：广角房间视角下小物件编辑看不清，
        # 大件（沙发/床/柜/桌/地毯）删/缩放才明显；同时能拿到真实名词而非 "the object"。
        min_size = float(p.get("min_subject_size", 0.4))
        sizable = [o for o in editable if _big_enough(o, min_size)]
        labeled = [o for o in (sizable or editable) if _cp(o, "category") != "object"]
        pool = labeled or sizable or editable
        subject = pool[int(rng.integers(0, len(pool)))]
        ctx.register_object(subject, is_subject=True)
        for o in editable:
            if o is not subject:
                ctx.register_object(o, is_subject=False)
        ctx.extras["distractor_categories"] = [_cp(o, "category") for o in ctx.distractors]

        # 4) 房间几何（用 stage 包围盒）+ 室内灯
        ctx.extras["scene_geom"] = _room_geom(stage_objs or editable)
        ctx.extras["subject_support"] = "ground"
        ctx.extras["ground"] = stage_objs[0] if stage_objs else None
        _setup_lighting(ctx, ctx.extras["scene_geom"])

        # 5) 相机：广角 + 站房间边缘朝主体拍（把整间房带进画面）
        geom = ctx.extras["scene_geom"]
        set_wide_fov(ctx.spec.render.get("resolution", [512, 512]),
                     fov_rad=float(p.get("fov_rad", 1.30)))
        n_views = int(p.get("camera_views", 1))
        for _ in range(n_views):
            bproc.camera.add_camera_pose(_sample_camera(rng, subject, geom))

        # 摊销加载：候选主体 + 重新取景（run_job 一次房间产多对，每对换家具 + 重新框相机）
        ctx.extras["editable_subjects"] = list(pool)

        def _reframe(subj):
            try:
                bproc.utility.reset_keyframes()
            except Exception:
                pass
            bproc.camera.add_camera_pose(_sample_camera(rng, subj, geom))
        ctx.extras["reframe_camera"] = _reframe

        def _closeup(target):
            """近景框住小物体（表面感知 add 用）：站 1.2-1.9m、略高、直视物体中心。"""
            try:
                bproc.utility.reset_keyframes()
            except Exception:
                pass
            bproc.camera.add_camera_pose(_closeup_camera(rng, target, geom))
        ctx.extras["closeup_camera"] = _closeup


# ----------------------- 工具 -----------------------

def _load_semantics(csv_path):
    """读 HSSD semantics/objects.csv → {object_id: {category, noun, description}}。

    优先 main_category，否则用 wnsynsetkey 去掉 .n.01 后缀；name 当物体描述。
    """
    import csv
    import re
    out = {}
    if not os.path.exists(csv_path):
        return out
    try:
        with open(csv_path, encoding="utf-8") as f:
            rows = csv.reader(f)
            header = next(rows)
            idx = {k: i for i, k in enumerate(header)}

            def cell(r, key):
                i = idx.get(key, -1)
                return r[i] if 0 <= i < len(r) else ""
            for r in rows:
                if not r:
                    continue
                main = cell(r, "main_category")
                wn = re.sub(r"\.n\.\d+$", "", cell(r, "wnsynsetkey"))
                cat = main or wn or "object"
                out[r[0]] = {"category": cat,
                             "noun": cat.replace("_", " ").strip() or "object",
                             "description": cell(r, "name")}
    except Exception as e:
        print(f"[hssd] semantics 解析跳过: {e}")
    return out


def _cp(obj, key):
    try:
        v = obj.get_cp(key)
        return str(v) if v else "object"
    except Exception:
        return "object"


def _habitat_matrix(inst):
    t = inst.get("translation", [0, 0, 0])
    q = inst.get("rotation", [1, 0, 0, 0])                 # [w,x,y,z]
    s = inst.get("non_uniform_scale", inst.get("uniform_scale", [1, 1, 1]))
    if isinstance(s, (int, float)):
        s = [s, s, s]
    M = np.eye(4)
    M[:3, :3] = _quat_to_mat(q) @ np.diag(s)
    M[:3, 3] = t
    return M


def _quat_to_mat(q):
    w, x, y, z = q
    n = (w * w + x * x + y * y + z * z) ** 0.5 or 1.0
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ], dtype=float)


def _to_matrix(M):
    import mathutils
    return mathutils.Matrix(M.tolist())


def _set_world(obj, M):
    try:
        obj.blender_obj.matrix_world = _to_matrix(M)
    except Exception:
        pass


# 家居中性色板（暖白/浅灰/灰褐/木/深木/浅蓝灰），比随机糖果色更像"样板间"，不那么塑料
_NEUTRAL_PALETTE = [
    (0.82, 0.80, 0.76), (0.70, 0.68, 0.64), (0.56, 0.52, 0.46),
    (0.45, 0.34, 0.24), (0.33, 0.25, 0.18), (0.58, 0.60, 0.63), (0.78, 0.74, 0.68),
]


def _neutralize(obj, rng):
    """无真实贴图时的兜底材质：家居中性色 + 真实粗糙度（basisu 贴图 Blender 读不了 →
    否则显品红/塑料感）。用家居色板 + 每件轻微扰动，看着像样板间而非玩具。"""
    try:
        base = np.array(_NEUTRAL_PALETTE[int(rng.integers(0, len(_NEUTRAL_PALETTE)))])
        base = np.clip(base * float(rng.uniform(0.9, 1.1)), 0.03, 0.97)
        mat = bproc.material.create("hssd_neutral")
        mat.set_principled_shader_value("Base Color", [float(base[0]), float(base[1]), float(base[2]), 1.0])
        mat.set_principled_shader_value("Roughness", float(rng.uniform(0.55, 0.9)))
        try:
            mat.set_principled_shader_value("Specular", 0.25)   # 降高光，别塑料反光
        except Exception:
            pass
        obj.replace_materials(mat)
    except Exception:
        pass


def _resolve_obj(root, template):
    c = template[0]
    for rel in (f"objects/{c}/{template}.glb",
                f"objects/decomposed/{c}/{template}.glb",
                f"objects/decomposed/{template}.glb"):
        path = os.path.join(root, rel)
        if os.path.exists(path):
            return path
    return None


def _load_glb(path):
    loaded = bproc.loader.load_obj(path)
    return loaded if isinstance(loaded, list) else [loaded]


def _has_geometry(o):
    try:
        b = o.blender_obj
        return b is not None and b.type == "MESH" and b.data is not None \
            and len(b.data.vertices) > 0
    except Exception:
        return False


def _load_glb_joined(path):
    objs = _load_glb(path)
    meshes = [o for o in objs if _has_geometry(o)]
    if not meshes:
        for o in objs:
            try:
                o.delete()
            except Exception:
                pass
        return None
    mesh_ids = {id(o) for o in meshes}
    for o in objs:
        if id(o) not in mesh_ids:
            try:
                o.delete()
            except Exception:
                pass
    obj = meshes[0]
    if len(meshes) > 1:
        obj.join_with_other_objects(meshes[1:])
    return obj


def _room_geom(objs):
    mins, maxs = [], []
    for o in objs:
        try:
            bb = np.asarray(o.get_bound_box())
            mins.append(bb.min(axis=0))
            maxs.append(bb.max(axis=0))
        except Exception:
            continue
    if not mins:
        return {"ground_z": 0.0, "ceiling_z": None, "bounds_min": None, "bounds_max": None}
    bmin = np.min(mins, axis=0)
    bmax = np.max(maxs, axis=0)
    return {"ground_z": float(bmin[2]), "ceiling_z": float(bmax[2]),
            "bounds_min": [float(x) for x in bmin], "bounds_max": [float(x) for x in bmax]}


def _set_world_ambient(strength=0.5, color=(1.0, 0.98, 0.95)):
    """给世界背景一个柔和环境光，整体抬暗部——避免大房间/暗角纯黑（HSSD stage 不带灯）。"""
    try:
        import bpy
        world = bpy.context.scene.world
        if world is None:
            world = bpy.data.worlds.new("World")
            bpy.context.scene.world = world
        world.use_nodes = True
        nt = world.node_tree
        bg = nt.nodes.get("Background") or nt.nodes.new("ShaderNodeBackground")
        bg.inputs[0].default_value = (color[0], color[1], color[2], 1.0)
        bg.inputs[1].default_value = float(strength)
    except Exception as e:
        print(f"[hssd] 环境光跳过: {e}")


def _setup_lighting(ctx, geom):
    """房间照明。两种模式（config `assets.environment` 决定）：

      - **hdri**（推荐，真实感）：HDRI 图像照明（真实光色/方向/反射）+ 一盏固定方向光（接触阴影/
        对比，脱平光的 CG 感）+ 很弱的面光兜底（enclosed 房间防暗角纯黑）。世界平 ambient 调很低。
      - **flat**（兜底）：原来的天花板网格面光 + 0.5 世界 ambient（均匀但发假）。

    固定光在 before/after 不变 → 不破坏"只有主体变"的对齐（主体移动带来的阴影变化是物理正确的）。
    """
    env_cfg = {}
    try:
        env_cfg = ctx.spec.assets.get("environment") or {}
    except Exception:
        env_cfg = {}

    if env_cfg.get("provider"):
        try:
            env = build("asset", env_cfg["provider"], **env_cfg.get("params", {}))
            env.apply(ctx)                                # 设 HDRI 世界背景（或其兜底平世界光）
            _add_sun_and_fill(geom, ctx.rng)             # 方向光 + 极弱面光兜底
            _set_world_ambient(0.08)                     # 平 ambient 压到很低，让 HDRI/太阳主导明暗
            ctx.extras["lighting"] = "hdri"
            return
        except Exception as e:
            print(f"[hssd] HDRI 照明失败，回退平光: {e}")

    _add_fill_light(geom)                                 # 兜底：原平光
    ctx.extras["lighting"] = "flat"


def _add_sun_and_fill(geom, rng):
    """方向性太阳光（给明暗/接触阴影）+ 天花板几盏很弱的面光（防 enclosed 房间暗角纯黑）。"""
    try:
        bmin = geom.get("bounds_min") or [-3, -3, 0]
        bmax = geom.get("bounds_max") or [3, 3, 3]
        ground = float(geom.get("ground_z", bmin[2]))
        ceil = float(geom.get("ceiling_z") or (ground + 3.0))
        cx = 0.5 * (bmin[0] + bmax[0])
        cy = 0.5 * (bmin[1] + bmax[1])
        # 固定太阳（能量/角度略随机 → 光照多样性），从上方斜射给方向性明暗。
        sun = bproc.types.Light()
        sun.set_type("SUN")
        sun.set_energy(float(rng.uniform(2.5, 4.5)))
        sun.set_location([cx + 2, cy - 2, ceil + 2])
        sun.set_rotation_euler([float(rng.uniform(0.3, 0.7)),
                                float(rng.uniform(0.1, 0.4)),
                                float(rng.uniform(0.0, 6.283))])
        # 极弱面光兜底（比原来暗一个量级，只抬死黑不抹平方向感）
        sx, sy = float(bmax[0] - bmin[0]), float(bmax[1] - bmin[1])
        nx, ny = max(1, int(round(sx / 4.0))), max(1, int(round(sy / 4.0)))
        for i in range(nx):
            for j in range(ny):
                x = bmin[0] + (i + 0.5) * sx / nx
                y = bmin[1] + (j + 0.5) * sy / ny
                light = bproc.types.Light()
                light.set_type("AREA")
                light.set_location([x, y, ceil - 0.15])
                light.set_energy(float(np.clip(20.0 * (sx / nx) * (sy / ny), 60.0, 250.0)))
                try:
                    light.blender_obj.data.size = min(3.0, 0.9 * min(sx / nx, sy / ny))
                except Exception:
                    pass
    except Exception as e:
        print(f"[hssd] 方向光/兜底面光跳过: {e}")


def _add_fill_light(geom):
    """房间补光（HSSD stage 自身不带灯）：天花板铺一格网面光（按房间大小自适应，
    避免单点照不匀→暗角发黑）+ 世界环境光抬暗部。"""
    try:
        bmin = geom.get("bounds_min") or [-3, -3, 0]
        bmax = geom.get("bounds_max") or [3, 3, 3]
        ground = float(geom.get("ground_z", bmin[2]))
        ceil = float(geom.get("ceiling_z") or (ground + 3.0))
        cz = ceil - 0.15
        sx = float(bmax[0] - bmin[0])
        sy = float(bmax[1] - bmin[1])
        # 每 ~3m 一盏，大房间自动多灯铺满；每盏能量随格子面积走（大格子要更亮）
        nx = max(1, int(round(sx / 3.0)))
        ny = max(1, int(round(sy / 3.0)))
        cell = (sx / nx) * (sy / ny)
        energy = float(np.clip(120.0 * cell, 350.0, 1500.0))
        for i in range(nx):
            for j in range(ny):
                x = bmin[0] + (i + 0.5) * sx / nx
                y = bmin[1] + (j + 0.5) * sy / ny
                light = bproc.types.Light()
                light.set_type("AREA")
                light.set_location([x, y, cz])
                light.set_energy(energy)
                try:
                    light.blender_obj.data.size = min(3.0, 0.9 * min(sx / nx, sy / ny))
                except Exception:
                    pass
        _set_world_ambient(0.5)
    except Exception as e:
        print(f"[hssd] 补光跳过: {e}")


def _has_image_texture(obj) -> bool:
    """物体材质里是否有已加载的图片贴图（贴图已还原的家具用真实材质，不再兜底平涂）。"""
    try:
        for m in obj.blender_obj.data.materials:
            if m and m.use_nodes:
                for n in m.node_tree.nodes:
                    if n.type == "TEX_IMAGE" and n.image is not None and n.image.size[0] > 0:
                        return True
        return False
    except Exception:
        return False


def _big_enough(o, thr) -> bool:
    """物体最长边是否 ≥ 阈值（挑体量够大的家具当主体，广角下编辑才看得清）。"""
    try:
        bb = np.asarray(o.get_bound_box())
        return float((bb.max(axis=0) - bb.min(axis=0)).max()) >= thr
    except Exception:
        return True


def _closeup_camera(rng, target, geom):
    """近景机位：站在离小物体 1.2-1.9m、略高于它处，**直视物体中心**（而非地面），
    让新加的小物件在画面里够大够清楚；要求物体在视锥内。"""
    t = np.asarray(target.get_location(), dtype=float)
    bmin = np.asarray(geom.get("bounds_min") or [-3, -3, 0], dtype=float)
    bmax = np.asarray(geom.get("bounds_max") or [3, 3, 3], dtype=float)
    ground = float(geom.get("ground_z", bmin[2]))
    fallback = None
    for _ in range(60):
        ang = rng.uniform(0, 2 * np.pi)
        dist = rng.uniform(1.2, 1.9)
        cam = np.array([t[0] + np.cos(ang) * dist,
                        t[1] + np.sin(ang) * dist,
                        t[2] + rng.uniform(0.3, 0.8)])
        cam[0] = float(np.clip(cam[0], bmin[0] + 0.4, bmax[0] - 0.4))
        cam[1] = float(np.clip(cam[1], bmin[1] + 0.4, bmax[1] - 0.4))
        cam[2] = float(np.clip(cam[2], ground + 0.5, bmax[2] - 0.2))
        pose = bproc.math.build_transformation_mat(
            cam, bproc.camera.rotation_from_forward_vec(t - cam))
        if fallback is None:
            fallback = pose
        # 物体在视锥内 + 视线没被挡（小物体常在台面/柜后，容易被前面家具遮住）
        if _in_frustum(t, pose) and _subject_visible(cam, target, min_frac=0.6):
            return pose
    return fallback if fallback is not None else pose


def _in_frustum(point, cam2world) -> bool:
    """主体点是否在相机视锥内（API 不符时不拦截）。"""
    try:
        return bool(bproc.camera.is_point_inside_camera_frustum(point, cam2world))
    except Exception:
        return True


def _subject_visible(cam, subject, min_frac: float = 0.5) -> bool:
    """从相机能不能真看到主体：对主体包围盒上若干点各打一条相机→点的射线，
    中途先撞上别的东西就算这点被挡。可见点占比 ≥ min_frac 才通过。

    专治两类坏机位：① 相机卡在墙里/家具后（前景一堵墙挡半屏）；② 主体被别的家具遮住。
    """
    try:
        import bpy
        scene = bpy.context.scene
        deps = bpy.context.evaluated_depsgraph_get()
        bb = np.asarray(subject.get_bound_box())
        ctr = (bb.max(axis=0) + bb.min(axis=0)) / 2.0
        # 采样点：中心 + 8 个包围盒角（略向中心收，避免正好擦过表面自遮挡）
        pts = [ctr] + [c + (ctr - c) * 0.15 for c in bb]
        cam = np.asarray(cam, dtype=float)
        vis = 0
        for p in pts:
            d = np.asarray(p, dtype=float) - cam
            dist = float(np.linalg.norm(d))
            if dist < 1e-3:
                vis += 1
                continue
            hit, loc, _n, _i, _o, _m = scene.ray_cast(deps, cam, (d / dist).tolist())
            if (not hit) or float(np.linalg.norm(np.asarray(loc) - cam)) >= dist - 0.12:
                vis += 1                              # 没撞到 或 撞到的就是主体本身 → 这点可见
        return vis / len(pts) >= min_frac
    except Exception:
        return True


def set_wide_fov(resolution, fov_rad=1.30):
    """把相机设成广角（~75° 水平），像室内实拍那样把整个房间带进画面。"""
    try:
        res = resolution or [512, 512]
        bproc.camera.set_intrinsics_from_blender_params(
            lens=float(fov_rad), image_width=int(res[0]), image_height=int(res[1]),
            lens_unit="FOV")
    except Exception as e:
        print(f"[hssd] 设广角跳过: {e}")


def _sample_camera(rng, subject, geom):
    """站在房间**边缘**、离主体一段距离、广角朝主体拍 → 画面里是"房间 + 其中的主体"，
    而不是贴脸特写。要求主体在视锥内；配合 set_wide_fov 的广角能带出整间房。"""
    subj = np.asarray(subject.get_location(), dtype=float)
    bmin = np.asarray(geom.get("bounds_min") or [-3, -3, 0], dtype=float)
    bmax = np.asarray(geom.get("bounds_max") or [3, 3, 3], dtype=float)
    center = (bmin + bmax) / 2.0
    ground = float(geom.get("ground_z", bmin[2]))
    H = float(bmax[2] - bmin[2])
    # 站在房间**内部**（离墙留距，别贴墙/穿墙），半径取半宽的一部分随机
    half_x = (bmax[0] - bmin[0]) / 2
    half_y = (bmax[1] - bmin[1]) / 2
    fallback = None
    for _ in range(60):
        ang = rng.uniform(0, 2 * np.pi)
        fx = rng.uniform(0.25, 0.6)                            # 0=房间中心, 0.6=靠近墙但仍在内
        cam = np.array([center[0] + np.cos(ang) * fx * half_x,
                        center[1] + np.sin(ang) * fx * half_y,
                        ground + rng.uniform(1.3, min(1.8, max(1.4, H - 0.4)))])
        cam[0] = float(np.clip(cam[0], bmin[0] + 0.6, bmax[0] - 0.6))
        cam[1] = float(np.clip(cam[1], bmin[1] + 0.6, bmax[1] - 0.6))
        if float(np.linalg.norm(cam[:2] - subj[:2])) < 1.5:   # 太近→贴脸，要距离带出房间
            continue
        look = subj.copy()
        look[2] = ground + 0.4
        pose = bproc.math.build_transformation_mat(
            cam, bproc.camera.rotation_from_forward_vec(look - cam))
        if fallback is None:
            fallback = pose
        # 主体既要在视锥内，又要**真的看得见**（不被墙/家具挡）——砍掉穿墙/被挡机位
        if _in_frustum(subj, pose) and _subject_visible(cam, subject):
            return pose
    return fallback if fallback is not None else pose
