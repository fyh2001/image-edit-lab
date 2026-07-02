"""
从 config 生成 JobSpec —— 支持**多场景数据集 + 多物体数据集组合** + **任务粒度（摊销/逐算子）**。

可复现：每个 job 的 seed = base_seed + job_index，固定 config + seed 就能完全重放。

两种消费方式：
  - `generate_jobspecs(cfg)` → JobSpec 列表（run_local 本地顺序/线程跑用）。
  - `iter_tasks(cfg, proj_root)` → **task 流 generator**（通用 Ray 流式执行器 ray_exec 用），
    每个 task = {type: "blender_render", profile, payload}，profile 决定资源档。

多数据集组合（config）：
  scenes:        [{name, weight, params}, ...]   # 场景多选，按权重；或 legacy 单个 `scene`
  object_sources:[{provider, weight, params}, ...]# 物体多选，注入 add/replace/spawn（composite provider）
任务粒度：
  run.granularity: amortized（默认，一 job=一场景产多对，资源按场景档）
                 | per_edit（一 job=一个算子一对，资源可按算子档）
"""
from __future__ import annotations
import os
import sys
import copy
from dataclasses import asdict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from datagen.worker.context import JobSpec


def _sample_edit(rng, edits_cfg):
    weights = edits_cfg["sampling_weights"]
    names = list(weights.keys())
    probs = np.array([weights[n] for n in names], dtype=float)
    probs /= probs.sum()
    name = str(rng.choice(names, p=probs))
    return {"name": name, "params": edits_cfg.get("params", {}).get(name, {})}


def _pick_scene(cfg, rng):
    """多场景数据集按权重挑一个；没有 `scenes` 就用 legacy 单 `scene`。"""
    scenes = cfg.get("scenes")
    if scenes:
        w = np.array([float(s.get("weight", 1.0)) for s in scenes], dtype=float)
        w /= w.sum()
        s = scenes[int(rng.choice(len(scenes), p=w))]
        return {"name": s["name"], "params": s.get("params", {})}
    return cfg["scene"]


def _object_source_params(cfg):
    """top-level object_sources → composite provider 的 provider_params（{sources:[...]}）。无则 None。"""
    srcs = cfg.get("object_sources")
    if not srcs:
        return None
    return {"sources": srcs}


def _inject_objects(edits_cfg, obj_params):
    """把组合物体源注入到需要物体的算子（add-spawn / replace / 变换的 spawn_params）。"""
    if not obj_params:
        return edits_cfg
    e = copy.deepcopy(edits_cfg)
    params = e.setdefault("params", {})
    add = params.setdefault("object_add", {})
    if add.get("spawn"):
        add["provider"], add["provider_params"] = "composite", obj_params
    rep = params.setdefault("object_replace", {})
    rep["provider"], rep["provider_params"] = "composite", obj_params
    for op in ("object_move", "object_scale", "object_rotate"):
        p = params.get(op)
        if p and p.get("subject_source"):
            sp = p.setdefault("spawn_params", {})
            sp["provider"], sp["provider_params"] = "composite", obj_params
    return e


def _profile(profile_by, scene_name, op):
    if profile_by == "edit":
        return op or "default"
    if profile_by == "scene":
        return scene_name or "default"
    return "default"


def _build_spec(cfg, i, edits_cfg, obj_params):
    """构造第 i 个 job 的 (JobSpec, scene, op)。granularity 决定摊销/逐算子。"""
    run = cfg["run"]
    seed = run["base_seed"] + i
    rng = np.random.default_rng(seed)
    scene = _pick_scene(cfg, rng)
    granularity = run.get("granularity", "amortized")
    amortized = granularity != "per_edit" and int(cfg.get("render", {}).get("pairs_per_scene", 1)) > 1
    edit = _sample_edit(rng, edits_cfg)
    render = cfg["render"]
    if not amortized:
        render = {**cfg["render"], "pairs_per_scene": 1}      # 逐算子：一对
    spec = JobSpec(
        job_id=f"job_{i:07d}", seed=seed, scene=scene, assets=cfg.get("assets", {}),
        edit=edit, render=render, output_dir=run["output_dir"],
        instruction=cfg.get("instruction", {}),
        edits_config=(edits_cfg if amortized else None),
    )
    return spec, scene, edit["name"]


def generate_jobspecs(cfg):
    """JobSpec 列表（本地 run_local 用）。多场景/多物体/粒度都支持。"""
    obj_params = _object_source_params(cfg)
    edits_cfg = _inject_objects(cfg["edits"], obj_params)
    return [_build_spec(cfg, i, edits_cfg, obj_params)[0] for i in range(cfg["run"]["num_jobs"])]


def iter_tasks(cfg, proj_root):
    """**task 流 generator**（Ray 流式执行器用）。逐个 yield，不 materialize → 可超大/无限流。"""
    obj_params = _object_source_params(cfg)
    edits_cfg = _inject_objects(cfg["edits"], obj_params)
    ray_cfg = cfg.get("ray", {})
    profile_by = ray_cfg.get("profile_by", "scene")
    timeout = int(ray_cfg.get("task_timeout", 1800))
    # streaming=true → 用流式任务类型（边产边 yield 每一对），配 run_stream_incremental 实时消费
    task_type = "blender_render_stream" if ray_cfg.get("streaming") else "blender_render"
    for i in range(cfg["run"]["num_jobs"]):
        spec, scene, op = _build_spec(cfg, i, edits_cfg, obj_params)
        yield {
            "type": task_type,
            "profile": _profile(profile_by, scene["name"], op),
            "payload": {"spec": asdict(spec), "proj_root": proj_root, "timeout": timeout},
        }
