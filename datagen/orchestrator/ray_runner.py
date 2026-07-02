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
from common.ray_exec import run_stream, run_stream_incremental
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

    modules = ["datagen.orchestrator.tasks.blender_render"]
    if ray_cfg.get("streaming"):                          # 增量流式：边产边消费每一对
        def on_pair(task, pair):
            if isinstance(pair, dict) and "__error__" in pair:
                print(f"[FAIL] {pair['__error__']}")
            # 这里可实时入库/打包/喂训练；默认只统计（run_stream_incremental 已打进度）
        stats = run_stream_incremental(tasks, resources=resources, max_in_flight=max_in_flight,
                                       task_modules=modules, on_pair=on_pair)
        print(f"\n全部完成（流式）。任务 {stats['tasks']}，实时消费 {stats['pairs']} 对，失败 {stats['fail']}。")
        print(f"原始产物在: {cfg['run']['output_dir']}")
        ray.shutdown()
        return

    def on_result(task, ok, res):
        jid = res.get("job_id", "?") if isinstance(res, dict) else "?"
        job_ok = ok and (not isinstance(res, dict) or res.get("ok", True))
        if not job_ok:
            tail = res.get("tail", res) if isinstance(res, dict) else res
            print(f"[FAIL] {jid}\n{tail}\n{'-'*60}")

    stats = run_stream(tasks, resources=resources, max_in_flight=max_in_flight,
                       task_modules=modules, on_result=on_result)
    print(f"\n全部完成。成功 {stats['ok']} / 失败 {stats['fail']} / 共 {stats['done']}。")
    print(f"原始产物在: {cfg['run']['output_dir']}")
    print("下一步: python -m orchestrator.export_hf --raw-dir", cfg["run"]["output_dir"], "--dry-run")
    ray.shutdown()


if __name__ == "__main__":
    main()
