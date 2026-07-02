"""
room 场景：一个合成的封闭房间（地面 + 4 面墙 + 天花板 + 室内灯），零下载。

目的：在没有 3D-FRONT 大数据集时，也能验证**房间级放置模式**（ceiling/wall/floating）
和房间边界约束——这些在 tabletop（无天花板/墙）下永远走不到。它也可当 3D-FRONT 的轻量
替身 / 单测夹具。结构件（地面/墙/天花板）不放进 all_objects，避免被当成碰撞物。
"""
from __future__ import annotations
import math
import numpy as np
import blenderproc as bproc

from datagen.worker.scene.base import SceneBuilder
from datagen.worker.registry import register_scene, build


@register_scene("room")
class RoomScene(SceneBuilder):
    def build(self, ctx):
        rng = ctx.rng
        spec = ctx.spec
        p = self.params
        W = float(p.get("width", 6.0))
        D = float(p.get("depth", 6.0))
        H = float(p.get("height", 3.0))

        # 1) 结构件：地面 / 天花板 / 4 面墙（都是 PLANE，Cycles 双面可见）
        floor = _plane("floor", scale=[W / 2, D / 2, 1], loc=[0, 0, 0], rot=[0, 0, 0])
        ceiling = _plane("ceiling", scale=[W / 2, D / 2, 1], loc=[0, 0, H], rot=[math.pi, 0, 0])
        walls = [
            _plane("wall_xneg", [H / 2, D / 2, 1], [-W / 2, 0, H / 2], [0, math.pi / 2, 0]),
            _plane("wall_xpos", [H / 2, D / 2, 1], [W / 2, 0, H / 2], [0, math.pi / 2, 0]),
            _plane("wall_yneg", [W / 2, H / 2, 1], [0, -D / 2, H / 2], [math.pi / 2, 0, 0]),
            _plane("wall_ypos", [W / 2, H / 2, 1], [0, D / 2, H / 2], [math.pi / 2, 0, 0]),
        ]
        structures = [floor, ceiling] + walls

        # 场景几何：地面/天花板高度 + 房间边界（启用 ceiling/wall/floating 放置模式）
        ctx.extras["scene_geom"] = {
            "ground_z": 0.0, "ceiling_z": H,
            "bounds_min": [-W / 2, -D / 2, 0.0], "bounds_max": [W / 2, D / 2, H],
        }
        ctx.extras["subject_support"] = "ground"
        ctx.extras["ground"] = floor                 # 物理沉降的被动碰撞体
        ctx.extras["room_structures"] = structures

        # 2) 室内灯（封闭房间外部光进不来，必须有内灯）
        light = bproc.types.Light()
        light.set_type("AREA")
        light.set_location([0, 0, H - 0.05])
        light.set_energy(float(p.get("light_energy", 120.0)))
        try:
            light.blender_obj.data.size = min(W, D) * 0.6
        except Exception:
            pass

        # 3) 主体（地面中心）。房间是 ~6m，主体要归一到家具尺度(~1m)，否则像 primitives
        #    那样 ~2.7m 的大块会把相机顶满、占满画面，移动一下就改了整帧。
        subj_size = float(p.get("subject_size", 1.0))
        subj_cfg = spec.assets["subject"]
        provider = build("asset", subj_cfg["provider"], **subj_cfg.get("params", {}))
        subject = provider.sample_object(ctx)
        _fit_size(subject, subj_size)
        subject.set_location([0, 0, _drop_height(subject)])
        subject.set_rotation_euler([0, 0, float(rng.uniform(0, 6.283))])
        ctx.register_object(subject, is_subject=True)

        # 4) 干扰物（地面、房间内、类别异于主体、无碰撞）
        from datagen.worker.physics import validity
        unique_cat = p.get("ensure_unique_subject_category", True)
        subj_cat = _category(subject)
        margin = 0.6
        n_lo, n_hi = p.get("num_distractors", [1, 2])
        n_distract = int(rng.integers(n_lo, n_hi + 1))
        for _ in range(n_distract):
            d = None
            for _os in range(8):
                cand = provider.sample_object(ctx)
                if not unique_cat or _category(cand) != subj_cat:
                    d = cand
                    break
                cand.delete()
            if d is None:
                continue
            _fit_size(d, subj_size)
            placed = False
            for _try in range(40):
                x = float(rng.uniform(-W / 2 + margin, W / 2 - margin))
                y = float(rng.uniform(-D / 2 + margin, D / 2 - margin))
                d.set_location([x, y, _drop_height(d)])
                d.set_rotation_euler([0, 0, float(rng.uniform(0, 6.283))])
                if not validity.collides(d, ctx.all_objects):
                    placed = True
                    break
            if placed:
                ctx.register_object(d, is_subject=False)
            else:
                d.delete()
        ctx.extras["distractor_categories"] = [_category(o) for o in ctx.distractors]

        # 5) 相机：房间内、朝向主体、留墙距
        sb = np.asarray(subject.get_bound_box())
        subj_center = (sb.min(axis=0) + sb.max(axis=0)) / 2.0
        n_views = int(p.get("camera_views", 1))
        for _ in range(n_views):
            bproc.camera.add_camera_pose(_sample_camera(rng, subj_center, W, D, H))


def _plane(name, scale, loc, rot):
    o = bproc.object.create_primitive("PLANE")
    o.set_scale(scale)
    o.set_location(loc)
    o.set_rotation_euler(rot)
    o.set_name(name)
    return o


def _category(obj) -> str:
    for key in ("category", "noun"):
        try:
            v = obj.get_cp(key)
            if v:
                return str(v)
        except Exception:
            pass
    return "object"


def _fit_size(obj, target):
    """把物体最长边缩到 target 米（房间里统一家具尺度，便于取景与放置）。"""
    try:
        bb = np.asarray(obj.get_bound_box())
        longest = float((bb.max(axis=0) - bb.min(axis=0)).max()) or 1.0
        s = target / longest
        cur = obj.get_scale()
        obj.set_scale([cur[0] * s, cur[1] * s, cur[2] * s])
    except Exception:
        pass


def _drop_height(obj) -> float:
    try:
        bbox = obj.get_bound_box()
        return float(-bbox.min(axis=0)[2])
    except Exception:
        return 0.0


def _sample_camera(rng, look_at, W, D, H):
    """站在房间一角朝室内对角看，尽量同时纳入地面/对面墙/天花板，
    这样 move 到墙/天花板/悬空的主体多半还在画面里（朝主体中心的近距机位看不到边角）。"""
    cx = float(rng.choice([-1.0, 1.0])) * (W / 2 - 0.5)
    cy = float(rng.choice([-1.0, 1.0])) * (D / 2 - 0.5)
    height = float(rng.uniform(1.3, min(2.0, H - 0.5)))
    cam = np.array([cx, cy, height])
    # 看向房间中心略偏上（纳入天花板/墙），而非死盯地面上的主体
    target = np.array([0.0, 0.0, H * 0.42])
    rot = bproc.camera.rotation_from_forward_vec(target - cam)
    return bproc.math.build_transformation_mat(cam, rot)
