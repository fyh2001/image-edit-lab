"""
存在性类编辑：增加 / 删除 / 替换。

add 与 delete 互为逆操作，用 prepare()/apply() 控制 before/after：
- delete:  before 有主体 -> apply 隐藏          => "remove the X"
- add:     prepare 隐藏主体（before 无）-> apply 显示 => "add a X"
- replace: apply 隐藏旧主体、原位放新主体（物理沉降落稳）=> "replace X with Y"

放置类用物理沉降（settle_physics）保证落稳、不穿模（混合策略）。
"""
from __future__ import annotations

from datagen.worker.edits.base import EditOperator, EditInvalid
from datagen.worker.edits._common import noun, hide, copy_transform, transform_dict
from datagen.worker.edits import _reference
from datagen.worker.physics import validity
from datagen.worker.registry import register_edit, build

# 统一用 _common.transform_dict（含 rotation_quat），保持 metadata 一致
_transform_dict = transform_dict


@register_edit("object_delete")
class DeleteEdit(EditOperator):
    def apply(self, ctx):
        obj = ctx.subject
        n = noun(obj)
        ref = _reference.subject_phrase(ctx, obj)   # 消歧要在 hide 前算（那时物体还在画面）
        if ref is None:
            raise EditInvalid("object_delete: 主体与画面里同类物体无法区分（歧义），丢弃")
        meta = {
            "op": "object_delete", "noun": n,
            "asset_uid": _safe_cp(obj, "asset_uid"),
            "transform": _transform_dict(obj),
            "support": ctx.extras.get("subject_support", "ground"),
            "validity": {"strategy": "none"},   # 删除不会产生碰撞
        }
        hide(obj, True)                          # 被遮挡区域会被 3D 正确补全
        instr = self.phrase([f"remove the {ref}", f"delete the {ref}",
                             f"erase the {ref} from the scene"], ctx.rng)
        return instr, meta


@register_edit("object_add")
class AddEdit(EditOperator):
    """增加一个物体。两种模式：
    - 默认（reverse-delete）：主体已由 SceneBuilder 合法放置，before 藏起来、after 显示。
    - spawn=True（表面感知放置）：现场从 provider 取一个**小物体**，用 surfaces.find_support_point
      放到某个表面上（桌面/台面/柜顶，或桌上的笔记本电脑顶），before 藏、after 显 →
      "add a book on top of the laptop"。放不下就 EditInvalid（换算子）。
    """

    def prepare(self, ctx):
        if self.params.get("spawn"):
            self._spawn_on_surface(ctx)
        elif ctx.subject is not None:
            hide(ctx.subject, True)

    def _spawn_on_surface(self, ctx):
        from datagen.worker.edits import _spawn
        # 复用共享放置逻辑：取小物体→表面放置→相机框到它上；add 要 before 藏起来
        _obj, label, snoun = _spawn.spawn_surface_subject(ctx, self.params, hide_after=True)
        ctx.extras["_add_support"] = (label, snoun)

    def apply(self, ctx):
        obj = ctx.subject
        n = noun(obj)
        hide(obj, False)
        support_label, support_noun = ctx.extras.get(
            "_add_support", (ctx.extras.get("subject_support", "ground"), None))
        meta = {
            "op": "object_add", "noun": n,
            "asset_uid": _safe_cp(obj, "asset_uid"),
            "transform": _transform_dict(obj),
            "support": support_label,
            "validity": {"strategy": "surface" if self.params.get("spawn") else "scene_build",
                         "collision_free": True},
        }
        if support_noun:                          # "add a book on top of the laptop"
            instr = self.phrase(
                [f"add a {n} on top of the {support_noun}",
                 f"place a {n} on the {support_noun}",
                 f"put a {n} on the {support_noun}"], ctx.rng)
        else:
            instr = self.phrase([f"add a {n} to the scene", f"place a {n} in the image",
                                 f"insert a {n}"], ctx.rng)
        return instr, meta


@register_edit("object_replace")
class ReplaceEdit(EditOperator):
    def apply(self, ctx):
        old = ctx.subject
        old_n = noun(old)
        old_ref = _reference.subject_phrase(ctx, old)   # 消歧要在 hide 旧物前算
        if old_ref is None:
            raise EditInvalid("object_replace: 旧物与画面里同类物体无法区分（歧义），丢弃")
        old_tf = _transform_dict(old)
        old_uid = _safe_cp(old, "asset_uid")

        prov_name = self.params.get("provider", "objaverse")
        prov_params = self.params.get("provider_params", {})
        provider = build("asset", prov_name, **prov_params)
        # 替换域由配置控制（same_category_prob）：
        #   =0（默认）→ 换成**不同类**（含跨域"沙发→橡皮"这种，故意保留——OOD 编辑对模型有价值）；
        #   =1        → 尽量换成**同类**（"沙发→扶手椅"，更合理）；中间值 → 按概率混。
        #   注：同类要靠资产池里正好有同 category 的物体；池子小时会优雅回退到"不同类"。
        same_cat = float(ctx.rng.uniform()) < float(self.params.get("same_category_prob", 0.0))
        # 旧物在原场景就接触的邻居（贴着的桌/柜/墙）→ 新物落稳后忽略这些，只在撞到新邻居时才拒
        baseline = validity.contacts(old, ctx.all_objects)
        new_obj = _sample_replacement(provider, ctx, old_n, old_uid, same_cat)
        copy_transform(old, new_obj)
        _match_size(old, new_obj)            # 尺度归一：与旧物"占地可比"，避免沙发换纽扣
        hide(old, True)

        # 新物体尺寸不同 -> 物理沉降落稳，再查碰撞
        # 被动碰撞体要包含地面，否则新物体会穿过地面坠落、从画面里消失（看起来像删除）。
        passive = [o for o in ctx.all_objects if o is not old]
        ground = ctx.extras.get("ground")
        if ground is not None:
            passive = passive + [ground]
        validity.settle_physics(new_obj, passive)
        # 兜底：若仍穿地坠落（地面碰撞形状异常等），改用解析 reseat 落回支撑面
        ground_z = ctx.extras.get("scene_geom", {}).get("ground_z", 0.0)
        if float(new_obj.get_location()[2]) < ground_z - 0.5:
            copy_transform(old, new_obj)
            validity.reseat(new_obj)
        if validity.collides(new_obj, [o for o in ctx.all_objects if o is not old],
                             ignore=baseline):
            raise EditInvalid("object_replace: 新物体落稳后仍与场景碰撞")

        # 关键：新物体自己必须在画面里**可见且够大**。否则"移走旧物"是大变化、能过 change_is_visible，
        # 但新物没露脸 → 这条样本实际是"旧物→(空)"，却标成"换成 Y"，是错标（碰到过 地毯→太阳能板）。
        r = ctx.spec.render
        res = list(r.get("resolution", [768, 768]))
        # 替换物要求比一般主体更"看得见"：至少占画面一定比例（默认 1%，可 config 调）。
        min_new_area = float(self.params.get(
            "min_new_visible_area", max(0.01, float(r.get("min_subject_area_ratio", 0.005)))))
        vis_ok, why = validity.camera_quality_ok(
            new_obj, res,
            min_visible=float(r.get("min_visible_fraction", 0.1)),
            min_area=min_new_area,
            max_area=float(r.get("max_subject_area_ratio", 0.9)))
        if not vis_ok:
            raise EditInvalid(f"object_replace: 新物体在画面里不可见/太小（{why}），丢弃避免错标")

        ctx.subject = new_obj
        new_n = noun(new_obj)
        meta = {
            "op": "object_replace",
            "from": {"asset_uid": old_uid, "category": old_n, "transform": old_tf},
            "to": {"asset_uid": _safe_cp(new_obj, "asset_uid"), "category": new_n,
                   "transform": _transform_dict(new_obj)},
            "support": ctx.extras.get("subject_support", "ground"),
            "validity": {"strategy": "physics", "collision_free": True},
        }
        if new_n == old_n:                    # 同类替换：换成"另一个/不同的"同类物，别读成没变
            instr = self.phrase([f"replace the {old_ref} with a different {new_n}",
                                 f"swap the {old_ref} for another {new_n}",
                                 f"change the {old_ref} to a different one"], ctx.rng)
        else:
            instr = self.phrase([f"replace the {old_ref} with a {new_n}",
                                 f"swap the {old_ref} for a {new_n}",
                                 f"change the {old_ref} into a {new_n}"], ctx.rng)
        return instr, meta


def _sample_replacement(provider, ctx, old_n, old_uid, same_cat, tries=10):
    """取一个替换物体。same_cat=True 尽量取同类(同 noun)，否则取不同类(变化/跨域)。
    永远拒绝"换成同一个资产"(换了个寂寞)。理想物体没抽到时回退到抽到的第一个合法候选。"""
    fallback = None
    for _ in range(tries):
        cand = provider.sample_object(ctx)
        c_n = noun(cand)
        c_uid = _safe_cp(cand, "asset_uid")
        if c_uid is not None and c_uid == old_uid:   # 同一资产 → 直接弃
            cand.delete()
            continue
        ideal = (c_n == old_n) if same_cat else (c_n != old_n)
        if ideal:
            if fallback is not None and fallback is not cand:
                fallback.delete()
            return cand
        if fallback is None:
            fallback = cand
        else:
            cand.delete()
    if fallback is not None:
        return fallback
    return provider.sample_object(ctx)                # 兜底（极端情况下都被弃）


def _match_size(ref, obj):
    """把 obj 缩放到「最长边与 ref 相同」，使替换前后占地可比（§3.6）。"""
    import numpy as np
    rb = np.asarray(ref.get_bound_box())
    ob = np.asarray(obj.get_bound_box())
    ref_long = float((rb.max(axis=0) - rb.min(axis=0)).max())
    obj_long = float((ob.max(axis=0) - ob.min(axis=0)).max()) or 1e-6
    r = ref_long / obj_long
    cur = np.array(obj.get_scale(), dtype=float)
    obj.set_scale((cur * r).tolist())


def _safe_cp(obj, key):
    try:
        return obj.get_cp(key)
    except Exception:
        return None


def _support_noun(blender_obj):
    """从被命中的支撑 blender 对象取一个可读名词（category/noun cp），供指令用。"""
    try:
        for key in ("category", "noun"):
            v = blender_obj.get(key)
            if v and str(v) != "object":
                return str(v).replace("_", " ").strip()
    except Exception:
        pass
    return None
