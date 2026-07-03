"""共享：把一个 provider 物体**表面感知地放进场景当主体**。

两处复用：
- object_add：spawn 一个新物体放到表面，before 藏 / after 显（"加了个杯子"）。
- move/scale/rotate（可选）：先 spawn 一个新物体到表面，再对它变换——这样既能"直接编辑
  场景已有家具"（真实分布），也能"先加进去再操作"（可控、高多样性），两种样本混着产。

放置逻辑（找可见表面 + 不悬边 + 相机框到物体上）复用 surfaces/placement/hssd 的现成能力。
"""
from __future__ import annotations

from datagen.worker.edits.base import EditInvalid
from datagen.worker.edits._common import hide
from datagen.worker.physics import surfaces, placement, validity
from datagen.worker.registry import build

_DEFAULT_PROVIDER_PARAMS = {
    "uid_list": "./assets/objaverse_uids.txt",
    "local_cache": "./assets/objaverse",
    "category_map": "./assets/objaverse_categories.json",
}


def support_noun(blender_obj):
    """从被命中的支撑 blender 对象取一个可读名词（category/noun cp），供指令用。"""
    try:
        for key in ("category", "noun"):
            v = blender_obj.get(key)
            if v and str(v) != "object":
                return str(v).replace("_", " ").strip()
    except Exception:
        pass
    return None


def spawn_surface_subject(ctx, params=None, hide_after=False):
    """取一个小物体、表面感知放到附近可见表面、把相机框到它上面，设为 ctx.subject。

    返回 (obj, support_label, support_noun)。放不下则抛 EditInvalid（交给上层丢弃/重试）。
    hide_after=True 时把物体藏起来（add 的 before 用）；否则保持可见（先加再变换用）。
    """
    params = params or {}
    prov_name = params.get("provider", "objaverse")
    if params.get("provider_params"):
        prov_params = params["provider_params"]              # 调用方给全（含 target_size）
    else:
        prov_params = dict(_DEFAULT_PROVIDER_PARAMS,
                           target_size=float(params.get("target_size", 0.3)))
    provider = build("asset", prov_name, **prov_params)
    prefer_on_object = bool(params.get("prefer_on_object", True))

    # 相机此刻框着 ctx.subject（重选出的家具）→ 把新物体放到它附近的表面，才在画面里
    near = None
    try:
        if ctx.subject is not None:
            near = list(ctx.subject.get_location())
    except Exception:
        near = None

    new_obj, res = None, None
    for _ in range(6):                       # 换几个物体/落点重试：放不下 or 放上去穿模都换
        if new_obj is not None:
            try:
                new_obj.delete()
            except Exception:
                pass
        new_obj = provider.sample_object(ctx)
        res = surfaces.find_support_point(
            ctx, new_obj, ctx.rng, in_view=placement._camera_in_view,
            prefer_on_object=prefer_on_object, near=near)
        if res is None:
            continue
        new_obj.set_location(res[0])
        # 放置后查碰撞：干净地架在支撑上不算（离面 1mm），插进支撑体/邻物才算 → 穿模就换
        if validity.collides(new_obj, ctx.all_objects):
            res = None
            continue
        break
    if res is None:
        if new_obj is not None:
            try:
                new_obj.delete()
            except Exception:
                pass
        raise EditInvalid("spawn_surface_subject: 找不到能稳放且不穿模的表面")

    loc, label, sobj = res
    ctx.subject = new_obj
    ctx.extras["subject_origin"] = "spawned"     # 溯源：主体是现场加进去的，非场景原有
    # 相机框到新物体上（中景），before/after 同机位、像素对齐
    reframe = ctx.extras.get("closeup_camera") or ctx.extras.get("reframe_camera")
    if reframe is not None:
        try:
            reframe(new_obj)
        except Exception as e:
            print(f"[spawn] reframe 跳过: {e}")
    if hide_after:
        hide(new_obj, True)
    return new_obj, label, support_noun(sobj)


def replace_subject_with_external(ctx, params=None):
    """把当前主体（场景已有物体）**替换成一个外部物体**：占它的位置、对齐它的尺寸、落稳，
    设为新 ctx.subject（保持可见）。之后由算子对这个"整合进场景槽位的外部物体"做变换。

    相机不动（新物体在原槽位、原机位就框着）；尺度对齐旧物 → 家具级、广角下够大好拍，
    与 spawn 的小物件互补。放不稳则抛 EditInvalid。
    """
    from datagen.worker.edits._common import copy_transform, hide as _hide
    from datagen.worker.edits.presence_edits import _match_size
    from datagen.worker.physics import validity
    params = params or {}
    old = ctx.subject
    if old is None:
        raise EditInvalid("replace_subject_with_external: 没有可替换的主体")

    prov_name = params.get("provider", "objaverse")
    if params.get("provider_params"):
        prov_params = params["provider_params"]
    else:
        prov_params = dict(_DEFAULT_PROVIDER_PARAMS,
                           target_size=float(params.get("target_size", 0.6)))
    provider = build("asset", prov_name, **prov_params)

    new_obj = provider.sample_object(ctx)
    copy_transform(old, new_obj)
    _match_size(old, new_obj)                     # 对齐旧物尺寸（占地可比）
    _hide(old, True)                              # 藏掉旧物（before 就是"新物在槽位里"）

    passive = [o for o in ctx.all_objects if o is not old]
    ground = ctx.extras.get("ground")
    if ground is not None:
        passive = passive + [ground]
    validity.settle_physics(new_obj, passive)     # 落稳
    ground_z = ctx.extras.get("scene_geom", {}).get("ground_z", 0.0)
    if float(new_obj.get_location()[2]) < ground_z - 0.5:   # 兜底：穿地则解析 reseat
        copy_transform(old, new_obj)
        validity.reseat(new_obj)

    ctx.subject = new_obj
    ctx.extras["subject_origin"] = "replaced"     # 溯源：外部物体替换进场景槽位
    return new_obj
