"""Ray 编排入口 —— 用**通用流式执行器** `ray_exec` 跑 Blender 渲染任务流。

相比旧版：① 流式（有界窗口，一个完成搞下一个，吃 generator）；② 每档资源可配（profile → CPU/GPU）；
③ 任务类型可插拔（这里是 blender_render，未来训练/RL/VLM 打标同一执行器）。

config 的 `ray` 段：
    ray:
      address: auto              # 或 "local"
      max_in_flight: 8           # 有界在飞窗口（背压）
      profile_by: scene          # scene | edit | fixed —— 用什么给 task 选资源档
      task_timeout: 1800
      resources:                 # 每档资源
        default: {num_cpus: 2, num_gpus: 1}
        hssd:    {num_cpus: 4, num_gpus: 1}      # profile_by=scene 时按场景名
        object_move: {num_cpus: 2, num_gpus: 1}  # profile_by=edit 时按算子名

用法：
    python -m orchestrator.ray_runner --config configs/hssd.yaml
"""
from __future__ import annotations
import os
import sys
import argparse
import itertools

import yaml

PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJ_ROOT)
from common.ray_exec import run_stream, run_stream_incremental, run_one
from datagen.orchestrator.jobspec_gen import iter_tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 个（冒烟用）")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    os.makedirs(cfg["run"]["output_dir"], exist_ok=True)

    ray_cfg = cfg.get("ray", {})
    resources = ray_cfg.get("resources", {"default": {
        "num_cpus": ray_cfg.get("num_cpus_per_job", 1),      # 兼容旧 config 字段
        "num_gpus": ray_cfg.get("num_gpus_per_job", 0)}})
    max_in_flight = int(ray_cfg.get("max_in_flight", 8))

    import ray
    addr = ray_cfg.get("address", "auto")
    ray.init(address=(None if addr in ("local", None) else addr), ignore_reinit_error=True)

    tasks = iter_tasks(cfg, PROJ_ROOT)
    if args.limit:
        tasks = itertools.islice(tasks, args.limit)

    modules = ["datagen.orchestrator.tasks.blender_render",
               "datagen.orchestrator.tasks.postprocess"]

    # —— render 阶段（扇出，流式或任务级）——
    if ray_cfg.get("streaming"):                          # 增量流式：边产边消费每一对
        def on_pair(task, pair):
            if isinstance(pair, dict) and "__error__" in pair:
                print(f"[FAIL] {pair['__error__']}")
        rs = run_stream_incremental(tasks, resources=resources, max_in_flight=max_in_flight,
                                    task_modules=modules, on_pair=on_pair)
        print(f"\nrender 完成（流式）。任务 {rs['tasks']}，实时产出 {rs['pairs']} 对，失败 {rs['fail']}。")
    else:
        def on_result(task, ok, res):
            if not (ok and (not isinstance(res, dict) or res.get("ok", True))):
                print(f"[FAIL] {res.get('job_id','?') if isinstance(res,dict) else '?'}: "
                      f"{res.get('tail', res) if isinstance(res, dict) else res}")
        rs = run_stream(tasks, resources=resources, max_in_flight=max_in_flight,
                        task_modules=modules, on_result=on_result)
        print(f"\nrender 完成。成功 {rs['ok']} / 失败 {rs['fail']} / 共 {rs['done']}。")
    print(f"原始产物在: {cfg['run']['output_dir']}")

    _run_post_pipeline(cfg, resources, modules)
    ray.shutdown()


def _run_post_pipeline(cfg, resources, modules):
    """render 全跑完后的**后处理算子链**（reduce/sink：pack → upload），config.pipeline 声明。

    每个算子吃上一步输出 + 自己 params，产出并入 ctx 喂下一步（如 pack 的 parquet_dir → upload）。
    """
    stages = cfg.get("pipeline") or []
    if not stages:
        print("（未配 pipeline；手动打包：python -m datagen.orchestrator.export_hf --raw-dir "
              f"{cfg['run']['output_dir']} --dry-run）")
        return
    ctx = {"raw_dir": cfg["run"]["output_dir"]}
    for st in stages:
        task = {"type": st["type"], "profile": st.get("profile", "default"),
                "payload": {**ctx, **st.get("params", {})}}
        print(f"[pipeline] 算子 {st['type']} …")
        out = run_one(task, resources=resources, task_modules=modules)
        if isinstance(out, dict):
            ctx.update(out)
        print(f"[pipeline] {st['type']} → {out}")


if __name__ == "__main__":
    main()
