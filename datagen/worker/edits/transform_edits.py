"""
位姿类编辑：移动 / 缩放 / 旋转。
全部接入物理有效性（解析式：碰撞 + reseat + 边界），并记录完整 metadata。
"""
from __future__ import annotations
import math
import numpy as np

from datagen.worker.edits.base import EditOperator, EditInvalid
from datagen.worker.edits._common import noun, camera_basis, transform_dict
from datagen.worker.edits import _reference
from datagen.worker.edits import _carried
from datagen.worker.physics import validity, placement
from datagen.worker.geometry import frames
from datagen.worker.registry import register_edit

MAX_ATTEMPTS = 30
# 投影变化下限：变换在画面上的包围盒变化量(0~1)至少这么大，否则重采——把"渲染后变化不可见"提前拦下。
# 取得比渲染后的像素下限(min_pixel_change_ratio≈0.0012)略低，只当"廉价早退"：
# 明显看不见的提前重采省一次渲染，边界样本仍交给渲染 gate 决定，绝不比它更严（不误伤）。
MIN_PROJ_CHANGE = 0.001


def _instruction_frame(ctx) -> str:
    return getattr(ctx.spec, "instruction", {}).get("frame", "scene_anchored")


def _bounds(ctx):
    g = ctx.extras.get("scene_geom", {})
    return g.get("bounds_min"), g.get("bounds_max")


def _quality(ctx):
    """相机侧质量门槛（遮挡/屏占比）+ 分辨率，来自 render 配置。"""
    r = ctx.spec.render
    return {
        "min_visible": r.get("min_visible_fraction", 0.1),
        "min_area": r.get("min_subject_area_ratio", 0.005),
        "max_area": r.get("max_subject_area_ratio", 0.9),
    }, list(r.get("resolution", [768, 768]))


def _rotate_view_change(axis, degrees):
    """把旋转翻译成"对镜头露出哪一面"的客观事实，供 caption 生成视角类指令。

    绕竖轴(Z=yaw)：|deg|≈180 → 露出相反的一面（背对/正对切换）；≈90 → 露出侧面；否则转了一角度。
    绕 X/Y(tip/tilt)：物体被"放倒/翻转"，不是转身。front/back/side 名称交给看图的 VLM。
    """
    d = abs(float(degrees)) % 360.0
    d = min(d, 360.0 - d)                         # 折到 [0,180]
    if axis == "Z":
        kind = "opposite_side" if d >= 135 else "side_face" if d >= 45 else "partial_turn"
        return {"kind": kind, "about": "vertical", "yaw_degrees": round(d, 1),
                "relative_to": "camera"}          # 竖轴转 → 换对镜头的面
    return {"kind": "tipped", "about": "horizontal", "degrees": round(d, 1)}


def _rotate_turn_direction(axis_vec, ang, view=0):
    """相机相对的**顺时针/逆时针**（客观事实，供 "顺时针转90度" 类 caption）。

    顺逆**取决于视角**：只有当旋转轴大致**朝向/背离相机**（沿视线）时才有意义（像正对你的钟面）；
    轴与视线近垂直（如竖轴 yaw + 水平相机）时顺逆无意义 → 返回 None，改由 view_change 表达。
    右手定则：绕轴正转，从轴尖端回看是逆时针。相机看向 forward，故 clockwise ⇔ (ang·dot(forward,axis))>0。
    """
    try:
        _r, _u, fwd = camera_basis(view)
    except Exception:
        return None
    a = np.asarray(axis_vec, dtype=float)
    a = a / (np.linalg.norm(a) + 1e-9)
    d = float(np.dot(np.asarray(fwd, dtype=float), a))
    if abs(d) < 0.7:                              # 轴≈垂直于视线 → 顺逆无意义
        return None
    return "clockwise" if (ang * d) > 0 else "counterclockwise"


def _maybe_spawn_subject(op, ctx):
    """按 `subject_source` 权重选主体来源，再让算子对它变换。三种模式混着产：
      - scene   ：直接编辑场景**已有物体**（真实分布，默认）
      - spawn   ：加一个外部物体到**空的可放置表面**再操作（可控、小物件）
      - replace ：用外部物体**替换已有物体的槽位**（占位+对齐尺寸）再操作（家具级、整合进场景）

    形式沿用 sampling_weights/placement_weights 的加权字典惯例，可扩展、可混、per-算子可调。
    向后兼容旧的 `spawn_subject_prob`。放不下/放不稳会抛 EditInvalid（上层丢弃/重试）。
    """
    weights = op.params.get("subject_source")
    if not weights:                                   # 兼容旧配置
        p = float(op.params.get("spawn_subject_prob", 0.0))
        weights = {"scene": 1.0 - p, "spawn": p} if p > 0.0 else {"scene": 1.0}
    modes = [m for m in weights if float(weights[m]) > 0]
    if not modes or modes == ["scene"]:
        return
    probs = np.array([float(weights[m]) for m in modes], dtype=float)
    probs /= probs.sum()
    mode = str(ctx.rng.choice(modes, p=probs))
    if mode == "scene":
        return
    from datagen.worker.edits import _spawn
    params = op.params.get("spawn_params", {})
    if mode == "spawn":
        _spawn.spawn_surface_subject(ctx, params)
    elif mode == "replace":
        _spawn.replace_subject_with_external(ctx, params)


@register_edit("object_move")
class MoveEdit(EditOperator):
    """把主体重新放置到某种放置模式（支撑面/物体顶/天花板/墙/悬空）。"""

    def prepare(self, ctx):
        _maybe_spawn_subject(self, ctx)     # 可选：先 spawn 新物体到表面当主体，再挪它

    def apply(self, ctx):
        obj = ctx.subject
        rng = ctx.rng
        geom = ctx.extras.get("scene_geom", {})
        # 指代消歧要在**挪动前**算（"the chair on the left" 指的是它 before 的位置）
        ref = _reference.subject_phrase(ctx, obj)
        if ref is None:
            raise EditInvalid("object_move: 主体与画面里同类物体无法区分（歧义），丢弃")
        from datagen.worker.assets.indoor_categories import is_wall_integrated
        try:
            _cat = obj.get_cp("category")
        except Exception:
            _cat = None
        if is_wall_integrated(_cat):     # 壁挂/嵌入/靠墙件（镜/柜/床/洁具）移动会脱墙浮空/穿墙
            raise EditInvalid(f"object_move: {_cat} 是壁挂/嵌入/靠墙类，移动会脱墙，丢弃")
        loc0 = np.array(obj.get_location(), dtype=float)
        # 变换前快照"放在主体顶面上的物体"，稍后让它们随主体一起挪（否则桌上物留原地→悬空）
        carried = _carried.snapshot(_carried.resting_on(obj, ctx.all_objects))
        bmin, bmax = _bounds(ctx)
        baseline = validity.contacts(obj, ctx.all_objects)   # 原场景就接触的邻居，挪走后忽略

        # 最小位移：避免偶尔采到原点附近 → 几乎不动的废编辑（指令形同虚设）。
        bb0 = np.asarray(obj.get_bound_box())
        subj_r = float(np.linalg.norm((bb0.max(0) - bb0.min(0))[:2]) / 2.0)
        min_move = float(self.params.get("min_distance", max(0.8, 0.5 * subj_r)))

        qcfg, res = _quality(ctx)
        # move 后主体必须**清晰可见**：屏占比下限比全局更严，否则小物被挪远/挪地板会小到看不见
        # （date 挪到 4m 外物顶上→画面里找不到，是坏样本）。默认 0.004≈屏面积 0.4%。
        qcfg = dict(qcfg)
        qcfg["min_area"] = max(float(qcfg.get("min_area", 0.005)),
                               float(self.params.get("min_visible_area", 0.004)))
        modes = placement.available_modes(geom, ctx.distractors)
        weights = self.params.get("placement_weights", {})
        probs = np.array([weights.get(m, 1.0) for m in modes], dtype=float)
        probs /= probs.sum()

        chosen = {}

        def sample():
            mode = str(rng.choice(modes, p=probs))
            tgt = placement.sample_move_target(mode, ctx, obj, rng, self.params)
            chosen["mode"], chosen["tgt"] = mode, tgt
            obj.set_location(tgt["location"])
            # 只有"落在下方支撑面"的放置（地面/叠物）才竖直贴回；
            # ceiling(贴顶)/wall(贴墙)/floating(悬空) 不能向下 reseat，否则会掉到地上。
            support = tgt["support"]
            reseated = support == "ground" or str(support).startswith("object:")
            chosen["reseated"] = reseated
            if reseated:
                validity.reseat(obj)
            return mode

        def check(_mode):
            # 位移太小直接拒（最便宜的检查放最前）
            if float(np.linalg.norm(np.array(obj.get_location()) - loc0)) < min_move:
                return False
            if validity.collides(obj, ctx.all_objects, ignore=baseline):
                return False
            if not validity.in_bounds(obj, bmin, bmax):
                return False
            # 不能移动后被完全遮挡、也不能太小/太大
            ok, reason = validity.camera_quality_ok(obj, res, **qcfg)
            chosen["q_reason"] = reason
            if not ok:
                return False
            # 画面上位移要够明显（沿视线方向挪投影几乎不变）→ 提前重采
            return validity.projected_change_ratio(bb0, obj.get_bound_box(), res) >= MIN_PROJ_CHANGE

        ok, _, attempts = validity.find_valid(sample, check, MAX_ATTEMPTS)
        if not ok:
            raise EditInvalid("object_move: 找不到无碰撞的合法落点")

        mode, tgt = chosen["mode"], chosen["tgt"]
        loc1 = np.array(obj.get_location(), dtype=float)
        delta = loc1 - loc0
        _carried.follow_translate(carried, delta)        # 桌上物随主体平移，继续压在顶面
        right, up, fwd = camera_basis(0)
        dirinfo = frames.classify_translation(delta, right, up, fwd)

        n = noun(obj)
        if _instruction_frame(ctx) == "camera_relative":
            instr = f"move the {ref} {frames.semantic_phrase(dirinfo['semantic'])}"
        else:  # scene_anchored
            instr = f"move the {ref} {tgt['note']}"

        meta = {
            "op": "object_move",
            "noun": n,
            "placement_mode": mode,
            "translation_world": [round(float(x), 4) for x in delta],
            "translation_camera": dirinfo["camera"],
            "semantic_direction": dirinfo["semantic"],
            "support_after": tgt["support"],
            "final_location": [round(float(x), 4) for x in loc1],
            "final_transform": transform_dict(obj),
            "validity": {"strategy": "analytic", "num_attempts": attempts,
                         "collision_free": True,
                         "reseated": chosen.get("reseated", False),
                         "camera_quality": "ok"},
        }
        return instr, meta


@register_edit("object_scale")
class ScaleEdit(EditOperator):
    """等比缩放，锚定底部中心；放大查碰撞、缩小后 reseat。"""

    def prepare(self, ctx):
        _maybe_spawn_subject(self, ctx)     # 可选：先 spawn 新物体到表面当主体，再缩放它

    def apply(self, ctx):
        obj = ctx.subject
        rng = ctx.rng
        ref = _reference.subject_phrase(ctx, obj)
        if ref is None:
            raise EditInvalid("object_scale: 主体与画面里同类物体无法区分（歧义），丢弃")
        lo, hi = self.params.get("scale_range", [0.5, 1.8])
        # 避开 ~1.0 的无感缩放：放大至少 +min_delta、缩小至少 -min_delta，保证变化看得见
        min_delta = float(self.params.get("min_factor_delta", 0.25))
        can_grow, can_shrink = hi >= 1.0 + min_delta, lo <= 1.0 - min_delta
        bmin, bmax = _bounds(ctx)
        qcfg, res = _quality(ctx)
        cur = np.array(obj.get_scale(), dtype=float)
        bbox0 = np.asarray(obj.get_bound_box())
        bottom_z = float(bbox0.min(axis=0)[2])
        top_z0 = float(bbox0.max(axis=0)[2])
        # 变换前快照顶面上的物体，缩放后让它们落到新顶面（否则缩小床头柜→台灯悬空）
        carried = _carried.snapshot(_carried.resting_on(obj, ctx.all_objects))
        baseline = validity.contacts(obj, ctx.all_objects)   # 原场景就接触的邻居，编辑后忽略

        chosen = {}
        # 整齐倍数为主(让"放大1.5倍/缩小到一半"类数值 caption 成立) + 少量连续。
        scale_choices = self.params.get("scale_choices")          # 如 [0.5, 0.75, 1.25, 1.5, 2.0]
        cont_frac = float(self.params.get("continuous_fraction", 0.3))

        def sample():
            grow = (rng.uniform() < 0.5) if (can_grow and can_shrink) else can_grow
            pool = None
            if scale_choices and rng.uniform() >= cont_frac:      # 采整齐倍数(合方向 + 满足最小变化)
                pool = [float(c) for c in scale_choices
                        if (c >= 1.0 + min_delta if grow else c <= 1.0 - min_delta)]
            if pool:
                f = float(rng.choice(pool))
                chosen["round"] = True
            else:                                                 # 连续兜底
                if grow and can_grow:
                    f = float(rng.uniform(1.0 + min_delta, hi))
                elif can_shrink:
                    f = float(rng.uniform(lo, 1.0 - min_delta))
                else:
                    f = float(rng.uniform(lo, hi))   # 兜底：范围本身就在 1.0 附近
                chosen["round"] = False
            chosen["f"] = f
            obj.set_scale((cur * f).tolist())
            # 锚定底部中心：缩放后把底部拉回原底高，避免穿地/悬空
            bb = np.asarray(obj.get_bound_box())
            new_bottom = float(bb.min(axis=0)[2])
            loc = np.array(obj.get_location(), dtype=float)
            loc[2] += (bottom_z - new_bottom)
            obj.set_location(loc.tolist())
            validity.reseat(obj)          # 再精确贴回支撑面
            return f

        def check(_f):
            if validity.collides(obj, ctx.all_objects, ignore=baseline):
                return False              # 撞到**新**邻居才算无效（基线接触忽略）
            if not validity.in_bounds(obj, bmin, bmax):
                return False
            # 关键：缩小后不能太小（屏占比过低）、放大后不能撑满/出框
            ok2, _r = validity.camera_quality_ok(obj, res, **qcfg)
            if not ok2:
                return False
            # 画面上变化要够大，否则渲染后会被判"变化不可见"→ 提前重采（零额外渲染）
            return validity.projected_change_ratio(bbox0, obj.get_bound_box(), res) >= MIN_PROJ_CHANGE

        ok, _, attempts = validity.find_valid(sample, check, MAX_ATTEMPTS)
        if not ok:
            raise EditInvalid("object_scale: 缩放后碰撞/越界/太小/太大")

        f = chosen["f"]
        # 承载物随主体缩放：顶面降 dz + footprint 按 f 缩放（水平向主体中心收，否则悬在新顶面外）
        center_xy = ((bbox0.max(axis=0) + bbox0.min(axis=0)) / 2.0)[:2]
        _carried.follow_scale_top(
            carried, center_xy, f,
            top_z0 - float(np.asarray(obj.get_bound_box()).max(axis=0)[2]))

        n = noun(obj)
        bigger = f >= 1.0
        instr = self.phrase(
            [f"make the {ref} bigger", f"enlarge the {ref}"] if bigger
            else [f"make the {ref} smaller", f"shrink the {ref}"], rng)
        meta = {
            "op": "object_scale", "noun": n,
            "factor": round(f, 4), "per_axis": [round(f, 4)] * 3,
            "factor_is_round": bool(chosen.get("round")),   # 整齐倍数 → caption 敢报"1.5倍"
            "uniform": True, "anchor": "bottom_center", "reseated": True,
            "validity": {"strategy": "analytic", "num_attempts": attempts,
                         "collision_free": True, "reseated": True},
        }
        return instr, meta


@register_edit("object_rotate")
class RotateEdit(EditOperator):
    """每对只绕一个轴（X/Y/Z 随机选）旋转；绕 X/Y 后重新落稳并查碰撞。"""

    AXES = {"X": [1, 0, 0], "Y": [0, 1, 0], "Z": [0, 0, 1]}

    def prepare(self, ctx):
        _maybe_spawn_subject(self, ctx)     # 可选：先 spawn 新物体到表面当主体，再旋转它

    def apply(self, ctx):
        obj = ctx.subject
        rng = ctx.rng
        ref = _reference.subject_phrase(ctx, obj)
        if ref is None:
            raise EditInvalid("object_rotate: 主体与画面里同类物体无法区分（歧义），丢弃")
        from datagen.worker.assets.indoor_categories import is_wall_integrated
        try:
            _cat = obj.get_cp("category")
        except Exception:
            _cat = None
        if is_wall_integrated(_cat):
            raise EditInvalid(f"object_rotate: {_cat} 是壁挂/嵌入/靠墙类，原地旋转必假，丢弃")
        max_deg = float(self.params.get("max_degrees", 180))
        allowed = self.params.get("axes", ["X", "Y", "Z"])
        bmin, bmax = _bounds(ctx)
        qcfg, res = _quality(ctx)
        rot0 = np.array(obj.get_rotation_euler(), dtype=float)
        bb0 = np.asarray(obj.get_bound_box())
        center_xy = ((bb0.max(axis=0) + bb0.min(axis=0)) / 2.0)[:2]
        baseline = validity.contacts(obj, ctx.all_objects)   # 原场景就接触的邻居，旋转后忽略
        # 变换前快照顶面上的物体，绕竖轴旋转后让它们一起转（否则转桌子→桌上物不动、错位）
        carried = _carried.snapshot(_carried.resting_on(obj, ctx.all_objects))
        # 有承载物时**只准绕竖轴 Z**：绕 X/Y 翻倒会让桌上物悬在半空（承载物只对 Z 跟随）。
        if carried:
            allowed = [a for a in allowed if a == "Z"]
            if not allowed:
                raise EditInvalid("object_rotate: 主体顶面有物体，不能绕 X/Y 翻转（会致悬空）")
        chosen = {}

        min_deg = float(self.params.get("min_degrees", 15))
        # 整齐角度为主(让"顺时针90度"类数值 caption 成立) + 少量连续(视觉多样性)。
        angle_choices = self.params.get("angle_choices")          # 如 [45, 90, 135, 180]
        cont_frac = float(self.params.get("continuous_fraction", 0.3))

        def sample():
            axis = str(rng.choice(allowed))
            if angle_choices and rng.uniform() >= cont_frac:      # 采整齐角度
                mag = float(rng.choice(angle_choices))
                chosen["round"] = True
            else:                                                 # 采"有意义"的连续幅值(避开~0°小旋转)
                mag = float(rng.uniform(min_deg, max_deg))
                chosen["round"] = False
            sign = 1.0 if rng.uniform() < 0.5 else -1.0
            ang = math.radians(sign * mag)
            chosen["axis"], chosen["ang"] = axis, ang
            rot = rot0.copy()
            idx = {"X": 0, "Y": 1, "Z": 2}[axis]
            rot[idx] += ang
            obj.set_rotation_euler(rot.tolist())
            if axis in ("X", "Y"):           # 俯仰/横滚可能翻倒 → 落稳
                validity.reseat(obj)
            return axis

        def check(_axis):
            if validity.collides(obj, ctx.all_objects, ignore=baseline):
                return False              # 撞到**新**邻居才算无效（基线接触忽略）
            if not validity.in_bounds(obj, bmin, bmax):
                return False
            ok2, _r = validity.camera_quality_ok(obj, res, **qcfg)
            return ok2

        ok, _, attempts = validity.find_valid(sample, check, MAX_ATTEMPTS)
        if not ok:
            raise EditInvalid("object_rotate: 旋转后碰撞/越界/不可见")

        axis, ang = chosen["axis"], chosen["ang"]
        if axis == "Z":                          # 绕竖轴：承载物一起转（X/Y 翻倒不好跟随，略过）
            _carried.follow_rotate_z(carried, center_xy, ang)
        n = noun(obj)
        instr = self.phrase([f"rotate the {ref}", f"turn the {ref} around"], rng)
        reseated = axis in ("X", "Y")
        deg = round(math.degrees(ang), 2)
        meta = {
            "op": "object_rotate", "noun": n,
            "axis": axis, "axis_world_vector": self.AXES[axis],
            "degrees": deg,
            "rotation_space": "world", "euler_order": "XYZ",
            "delta_quat": [round(x, 6) for x in
                           frames.axis_angle_to_quat(self.AXES[axis], ang)],
            # 相机相对视角变化（纯几何，正确）：绕竖轴(Z)转 → 换成对镜头的另一面；
            # front/back/side 的语义命名交给看图的 VLM captioner，这里只给客观事实。
            "view_change": _rotate_view_change(axis, deg),
            # 相机相对顺逆(仅轴≈沿视线时非 None) + 是否整齐角度(供数值 caption 决定敢不敢报数)。
            "turn_direction": _rotate_turn_direction(self.AXES[axis], ang),
            "angle_is_round": bool(chosen.get("round")),
            "reseated": reseated,
            "final_transform": transform_dict(obj),
            "validity": {"strategy": "analytic", "num_attempts": attempts,
                         "collision_free": True, "reseated": reseated},
        }
        return instr, meta
