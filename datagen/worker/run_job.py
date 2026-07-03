# Worker 入口 —— 由 orchestrator 以子进程方式调用：
#
#     blenderproc run worker/run_job.py -- <jobspec.json>
#
# 它在 Blender 的 Python 里跑一个完整的 (before, after) 编辑配对：
#     建场景 -> editor.prepare -> 渲 before -> editor.apply -> 渲 after -> 落地
#
# 每个 job 一个独立进程（BlenderProc 推荐用法），Ray 负责在 8×H100 上并发调度。
#
# 注意：BlenderProc 要求 `import blenderproc as bproc` 必须是脚本里第一条 import，
# 它会改写 import 机制，把第三方包重定向到 Blender 自带 Python。因此本文件顶部
# 不能有模块 docstring / `from __future__` / 其它 import 抢在它前面。
import blenderproc as bproc  # noqa: E402  —— 必须第一个导入

import sys
import os
import json

import numpy as np

# 让 `import datagen.worker.*` 能找到项目根目录
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from datagen.worker.context import JobSpec, SceneContext
from datagen.worker.registry import build
import datagen.worker.plugins  # noqa: F401  —— 触发所有插件注册（assets/edits/scene/backends）
from datagen.worker.export.pair_writer import write_pair
from datagen.worker.edits.base import EditInvalid
from datagen.worker.edits._common import transform_dict
from datagen.worker.physics import validity
from datagen.worker.quality.filter import QualityFilter
from datagen.worker.geometry.frames import camera_basis_from_matrix, direction_consistency


def _matrix_to_euler(cam2world):
    """4x4 外参 → rotation_euler（Blender XYZ 约定），取不到时返回 None。"""
    try:
        import mathutils
        eul = mathutils.Matrix(np.asarray(cam2world).tolist()).to_euler("XYZ")
        return [round(float(a), 6) for a in eul]
    except Exception:
        return None


def _camera_intrinsics(resolution):
    """相机内参 K → {fx, fy, cx, cy, resolution}。取不到返回 None（不致命）。"""
    try:
        K = np.asarray(bproc.camera.get_intrinsics_as_K_matrix())
        return {"fx": round(float(K[0, 0]), 4), "fy": round(float(K[1, 1]), 4),
                "cx": round(float(K[0, 2]), 4), "cy": round(float(K[1, 2]), 4),
                "resolution": [int(resolution[0]), int(resolution[1])]}
    except Exception:
        return None


def _snapshot_subject(ctx):
    """在编辑前快照主体信息（init_transform / 类别 / 支撑面），供 sample 级 subject 用。

    必须在 editor.apply 之前调用：replace 会把 ctx.subject 换成新物体，move/scale/rotate
    会改变主体位姿——编辑后就拿不到"before 场景里的初始位姿"了。
    """
    subj = ctx.subject
    if subj is None:
        return {}
    try:
        bb = np.asarray(subj.get_bound_box())
        info = {
            "category": _safe_cp(subj, "category") or _safe_cp(subj, "noun"),
            "asset_uid": _safe_cp(subj, "asset_uid"),
            "init_transform": transform_dict(subj),
            "bbox_dims": [round(float(x), 4) for x in (bb.max(axis=0) - bb.min(axis=0))],
            "support_before": ctx.extras.get("subject_support", "ground"),
        }
        # 物体描述/标签/许可（有就带上，便于指令生成 / 审计 / 法务溯源）
        desc = _safe_cp(subj, "description")
        if desc:
            info["description"] = desc
        lic = _safe_cp(subj, "license")
        if lic:
            info["license"] = lic
        tags = _safe_cp(subj, "tags")
        if tags:
            info["tags"] = [t for t in str(tags).split(",") if t]
        return info
    except Exception:
        return {}


def _direction_check(ctx, meta):
    """object_move 专属：把主体编辑前后的世界坐标投影到画面，核对实际像素位移方向
    是否与指令的语义方向词（左右上下）一致。其它算子返回 None。"""
    if meta.get("op") != "object_move":
        return None
    semantic = meta.get("semantic_direction") or []
    if not semantic:
        return None
    try:
        init = ctx.extras.get("subject_init", {}).get("init_transform", {})
        p0 = np.asarray(init.get("location"), dtype=float).reshape(1, 3)
        p1 = np.asarray(meta.get("final_location"), dtype=float).reshape(1, 3)
        px0 = np.asarray(bproc.camera.project_points(p0, frame=0))[0]
        px1 = np.asarray(bproc.camera.project_points(p1, frame=0))[0]
        d_px, d_py = float(px1[0] - px0[0]), float(px1[1] - px0[1])
        res = direction_consistency(semantic, d_px, d_py)
        res["pixel_delta"] = [round(d_px, 1), round(d_py, 1)]
        return res
    except Exception as e:
        print(f"[run_job] 方向校验跳过: {e}")
        return None


def _frame_meta(ctx, before_imgs):
    """组装样本级 metadata：坐标系约定 + 各机位外参/内参 + 主体信息。"""
    resolution = ctx.spec.render.get("resolution", [768, 768])
    intrinsics = _camera_intrinsics(resolution)
    cams = []
    for v in range(len(before_imgs)):
        try:
            cam2world = bproc.camera.get_camera_pose(frame=v)
            right, up, fwd = camera_basis_from_matrix(cam2world)
            loc = [float(x) for x in np.asarray(cam2world)[:3, 3]]
            cam = {"view": v, "location": loc,
                   "rotation_euler": _matrix_to_euler(cam2world),
                   "right": [float(x) for x in right],
                   "up": [float(x) for x in up],
                   "forward": [float(x) for x in fwd]}
            if intrinsics is not None:
                cam["intrinsics"] = intrinsics
            cams.append(cam)
        except Exception:
            cams.append({"view": v})
    return {
        "coordinate_frame": {
            "world": {"up_axis": "Z", "units": "meter",
                      "ground_z": ctx.extras.get("scene_geom", {}).get("ground_z", 0.0)},
            "convention": "blender_world_right_handed",
        },
        "cameras": cams,
        # 主体信息 + origin：scene=直接编辑场景已有物体 / spawned=先加进去再操作
        "subject": {**ctx.extras.get("subject_init", {}),
                    "origin": ctx.extras.get("subject_origin", "scene")},
        # 场景里其余物体的类别——消歧校验用：主体类别不应出现在这里
        "distractor_categories": ctx.extras.get("distractor_categories", []),
    }


def _safe_cp(obj, key):
    try:
        return obj.get_cp(key)
    except Exception:
        return None


PIPELINE_VERSION = "0.4.0"          # 产出格式/语义变了就 +1，供溯源对齐代码版本

_SCENE_LICENSE = {
    "hssd": "HSSD (hssd/hssd-hab) · CC-BY-NC 4.0 · 研究用途",
    "front3d": "3D-FRONT · 学术免费(gated)",
    "tabletop": "synthetic(primitives/objaverse)",
    "room": "synthetic composite",
}


def _tooling():
    """记录渲染工具版本，溯源时对齐环境。"""
    tv = {}
    try:
        import bpy
        tv["blender"] = bpy.app.version_string
    except Exception:
        pass
    try:
        from importlib.metadata import version
        tv["blenderproc"] = version("blenderproc")
    except Exception:
        try:
            tv["blenderproc"] = getattr(bproc, "__version__", None)
        except Exception:
            pass
    tv["pipeline"] = PIPELINE_VERSION
    return tv


def _provenance(ctx, spec):
    """完整溯源块：场景来源(数据集+解析后的真实 id)、资产 uid+license、
    完整生成配置、工具/管线版本——配 seed 可还原现场。"""
    scene_name = spec.scene.get("name")
    scene_id = ctx.extras.get("used_hssd_scene") or ctx.extras.get("used_front3d_scene")
    subj = ctx.subject
    return {
        "seed": spec.seed,
        "pipeline_version": PIPELINE_VERSION,
        "tooling": _tooling(),
        "scene_source": {
            "dataset": scene_name,
            "scene_id": scene_id,                        # 解析后的真实房间 id（config 里可能是 null=随机）
            "data_dir": spec.scene.get("params", {}).get("data_dir"),
            "license": _SCENE_LICENSE.get(scene_name),
        },
        "assets": {
            "objaverse_uids": sorted(set(ctx.extras.get("used_objaverse_uids", []))),
            "subject_uid": _safe_cp(subj, "asset_uid"),  # 被编辑主体的资产 id（hssd:模板名 或 objaverse uid）
            "subject_license": _safe_cp(subj, "license"),
            "hdri": ctx.extras.get("hdri"),
        },
        # 完整生成配置：配 seed 可复现（scene_id 若为 null=随机，真实值见 scene_source）
        "config": {
            "scene": spec.scene,
            "render": spec.render,
            "edits": spec.edits_config,
        },
        "front3d_scene": ctx.extras.get("used_front3d_scene"),
    }


def _sample_edit(rng, edits_cfg, produced_by_op=None):
    """采一个算子。`sampling_weights` 现在是**目标产出占比**，用**亏空采样**去命中——
    每次偏向当前"最欠目标"的算子，自动补偿各算子 yield 差异（add 易产、move 难产）。

    亏空 = 目标占比 × (已产总数+1) − 该算子已产；clamp≥0 再 +eps（eps 保证已达标/难产算子仍有
    小概率被选，让 retry 有多样性、不死磕一个算子）。摊销模式每对重采。
    """
    weights = edits_cfg["sampling_weights"]
    names = [n for n in weights if float(weights[n]) > 0]
    targets = np.array([float(weights[n]) for n in names], dtype=float)
    targets /= targets.sum()
    produced_by_op = produced_by_op or {}
    total = sum(produced_by_op.get(n, 0) for n in names)
    deficit = np.array([max(0.0, targets[i] * (total + 1) - produced_by_op.get(n, 0))
                        for i, n in enumerate(names)], dtype=float)
    w = deficit + 0.05
    w /= w.sum()
    name = str(rng.choice(names, p=w))
    return {"name": name, "params": edits_cfg.get("params", {}).get(name, {})}


def _snapshot_scene():
    """快照所有网格/灯/空节点的位姿+可见性 + 现有对象名集合（相机由 reframe 负责，不快照）。"""
    import bpy
    objs = {}
    for o in bpy.data.objects:
        if o.type in ("MESH", "LIGHT", "EMPTY"):
            objs[o.name] = (tuple(o.location), tuple(o.rotation_euler), tuple(o.scale),
                            bool(o.hide_render), bool(o.hide_viewport))
    return {"objs": objs, "names": {o.name for o in bpy.data.objects}}


def _restore_scene(snap):
    """恢复到快照：删新增对象、还原位姿/可见性、清 rigidbody（供一场景多对之间复位）。"""
    import bpy
    for o in list(bpy.data.objects):                       # 删编辑中新增的（如 replace 的新物体）
        if o.name not in snap["names"]:
            try:
                bpy.data.objects.remove(o, do_unlink=True)
            except Exception:
                pass
    for name, (loc, rot, scl, hr, hv) in snap["objs"].items():
        o = bpy.data.objects.get(name)
        if o is None:
            continue
        o.location, o.rotation_euler, o.scale = loc, rot, scl
        o.hide_render, o.hide_viewport = hr, hv
    for o in list(bpy.data.objects):                       # 清物理沉降残留（原始场景无 rigidbody）
        if getattr(o, "rigid_body", None) is not None:
            try:
                bpy.context.view_layer.objects.active = o
                bpy.ops.rigidbody.object_remove()
            except Exception:
                pass
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass


def _reselect(ctx):
    """换一个主体 + 重新框相机（不重载资产），靠 build 时挂在 extras 的候选/闭包。"""
    cands = ctx.extras.get("editable_subjects")
    reframe = ctx.extras.get("reframe_camera")
    if not cands or reframe is None:
        return False
    subj = cands[int(ctx.rng.integers(0, len(cands)))]
    ctx.subject = subj
    ctx.distractors = [o for o in cands if o is not subj]
    ctx.extras["distractor_categories"] = [
        (_safe_cp(o, "category") or "object") for o in ctx.distractors]
    try:
        reframe(subj)
    except Exception as e:
        print(f"[run_job] reframe 失败: {e}")
        return False
    return True


def _produce_pair(ctx, spec, backend, editor, job_id, op_name=None, seen=None):
    """跑一对 before→编辑→after + 全部过滤 + 落地。返回是否产出。"""
    ctx.extras["subject_origin"] = "scene"                 # 默认直接编辑场景已有物体；spawn 会改成 spawned
    try:
        editor.prepare(ctx)                                # add-spawn / 先加再变换 可能在此判无效
    except EditInvalid as e:
        print(f"[run_job] DISCARD(prepare 无效) {job_id}: {e}")
        return False
    # 场景内去重：同一个已有物体 + 同一个算子只产一次（换任务/换物体都欢迎，就是别重复同一组合）。
    # spawn/replace 模式主体是新的外部物体，天然不重复，不参与去重。
    dedup_key = None
    if seen is not None and ctx.extras.get("subject_origin") == "scene" and op_name:
        sname = _subject_name(ctx.subject)
        if sname is not None:
            dedup_key = (op_name, sname)
            if dedup_key in seen:
                print(f"[run_job] DISCARD(重复:同物体+同算子 {op_name}/{sname}) {job_id}")
                return False
    # 主体快照放在 prepare 之后、apply 之前：surface-add 的真主体是 prepare 里现场 spawn 的，
    # 快照晚一步才能拍到它（而非重选来取景的那个物体）；move/scale/rotate 的 prepare 是空操作，
    # 仍是编辑前状态，不受影响。
    ctx.extras["subject_init"] = _snapshot_subject(ctx)
    before = backend.render()
    try:
        instruction, meta = editor.apply(ctx)
    except EditInvalid as e:
        print(f"[run_job] DISCARD(物理无效) {job_id}: {e}")
        return False
    after = backend.render()

    vis_cfg = spec.render.get("min_pixel_change_ratio", 0.01)
    visible, ratio = validity.change_is_visible(before[0], after[0], min_ratio=vis_cfg)
    if not visible:
        print(f"[run_job] DISCARD(变化不可见 ratio={ratio:.4f}) {job_id}")
        return False
    v = meta.setdefault("validity", {})
    v["change_visible"] = True
    v["pixel_change_ratio"] = round(float(ratio), 4)
    v.setdefault("penetration_depth", 0.0)
    # delete 后主体已隐藏，几何检查无意义；其余算子对最终主体做穿地/悬空检查。
    if ctx.subject is not None and meta.get("op") != "object_delete":
        gz = float(ctx.extras.get("scene_geom", {}).get("ground_z", 0.0))
        max_pen = float((spec.render.get("quality") or {}).get("max_penetration", 0.02))
        # 穿地：最低点低于地面多少（不管贴地还是在桌上都对；接触≈0 不误判）
        pen_floor = validity.floor_penetration(ctx.subject, gz)
        # 穿支撑/邻物：主体陷进某物体体内多深（casserole 盖住台灯座那类）
        pen_obj = validity.support_penetration(ctx.subject, ctx.all_objects)
        pen = max(pen_floor, pen_obj)
        v["penetration_depth"] = round(pen, 4)
        if pen > max_pen:
            where = "地面" if pen_floor >= pen_obj else "支撑物"
            print(f"[run_job] DISCARD(穿模{where} {pen:.3f}m>{max_pen}) {job_id}")
            return False
        # 悬空：优先用射线到真实支撑面的间隙；射线失败退回"最低点距地面"（仅当支撑是地面时才准）
        gap = None
        try:
            gap = validity.support_gap(ctx.subject)
        except Exception:
            pass
        if gap is None:
            try:
                bb = np.asarray(ctx.subject.get_bound_box())
                gap = float(bb.min(axis=0)[2]) - gz
            except Exception:
                pass
        if gap is not None:
            v["floating_gap"] = round(max(0.0, float(gap)), 4)

    qf = QualityFilter(spec.render.get("quality"))
    passed, qscores, qreason = qf.evaluate(before[0], after[0])
    v["quality"] = qscores
    if not passed:
        print(f"[run_job] DISCARD(质量不过 {qreason}) {job_id}: {qscores}")
        return False

    dchk = _direction_check(ctx, meta)
    if dchk is not None:
        v["direction_check"] = dchk

    spec._hdri = ctx.extras.get("hdri")
    out = write_pair(spec.output_dir, spec, before, after, instruction, meta,
                     provenance=_provenance(ctx, spec),
                     frame_meta=_frame_meta(ctx, before), job_id=job_id)
    if dedup_key is not None and seen is not None:
        seen.add(dedup_key)                                # 记下"这物体+这算子"已产
    # 逐对流式标记：Ray 流式任务读 stdout 这行 → 实时把这一对 yield 给 driver（边产边消费）
    print("##PAIR## " + json.dumps(
        {"dir": out, "op": meta.get("op"), "instruction": instruction,
         "sample_json": os.path.join(out, "sample.json")}, ensure_ascii=False), flush=True)
    print(f"[run_job] done: {out}  |  edit={meta.get('op')}  |  '{instruction}'")
    return True


def _subject_name(obj):
    try:
        return obj.get_name()
    except Exception:
        try:
            return obj.blender_obj.name
        except Exception:
            return None


def main():
    # 解析参数：blenderproc 会把 `--` 之后的参数原样传入
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    if not args:
        raise SystemExit("用法: blenderproc run worker/run_job.py -- <jobspec.json>")
    spec = JobSpec.from_file(args[0])

    bproc.init()
    rng = np.random.default_rng(spec.seed)
    ctx = SceneContext(spec, rng)

    backend = build("backend", spec.render["backend"])
    backend.setup(spec.render)

    scene_builder = build("scene", spec.scene["name"], **spec.scene.get("params", {}))
    scene_builder.build(ctx)                                # 只建一次（重活）

    # 摊销加载：一次建场景产 pairs_per_scene 对，每对复位场景 + 换主体 + 重新取景 + 重采算子
    pairs = int(spec.render.get("pairs_per_scene", 1))
    snap = _snapshot_scene() if pairs > 1 else None
    # 每个"对"最多重试几次：被丢弃(碰撞死局/变化不可见/放不下)时换个主体+算子重采，
    # 而不是白白浪费这个 slot。只在摊销(pairs>1，有快照可复位)时启用。
    max_tries = int(spec.render.get("pair_max_tries", 4)) if pairs > 1 else 1

    produced = 0
    stop = False
    seen = set()                                        # 场景内 (算子, 物体) 去重
    produced_by_op = {}                                 # 各算子已产数，供亏空采样均衡分布
    for i in range(pairs):
        if stop:
            break
        job_id = spec.job_id if pairs == 1 else f"{spec.job_id}_p{i:02d}"
        for attempt in range(max_tries):
            if i > 0 or attempt > 0:
                _restore_scene(snap)                    # 复位（上次尝试可能挪了主体/加了物体）
                if not _reselect(ctx):
                    print("[run_job] 该场景不支持重选主体，停止摊销")
                    stop = True
                    break
            if spec.edits_config and pairs > 1:
                e = _sample_edit(rng, spec.edits_config, produced_by_op)
            else:
                e = spec.edit
            editor = build("edit", e["name"], **e.get("params", {}))
            if _produce_pair(ctx, spec, backend, editor, job_id,
                             op_name=e["name"], seen=seen):
                produced += 1
                produced_by_op[e["name"]] = produced_by_op.get(e["name"], 0) + 1
                break                                   # 这个 slot 成功了，进入下一个
            # 否则：被丢弃 → 复位换主体/算子重采（下一轮 attempt），不浪费 slot

    if pairs > 1:
        print(f"[run_job] 场景 {spec.job_id}: 产出 {produced}/{pairs} 对")


if __name__ == "__main__":
    main()
