"""
把一个 job 的产物落地为「每对一个文件夹」的原始格式：
    <output_dir>/<job_id>/
        before_v0.png  after_v0.png      # 第 0 个机位的前后图
        before_v1.png  after_v1.png      # 多机位时
        sample.json                      # 指令 + 变更元数据 + spec

之所以先落原始文件、再由 orchestrator 的 collector 打包成 WebDataset，
是因为渲染是分布式的、打包是汇总步骤，两者解耦更清晰也更易重试。
"""
from __future__ import annotations
import os
import json
import numpy as np


def _save_png(path: str, arr: np.ndarray):
    try:
        import imageio.v2 as imageio
        imageio.imwrite(path, arr)
    except Exception:
        # 兜底：用 Blender 自带的 PIL/np 也行，这里再退到 PNG via numpy+png 不可得时报错
        from PIL import Image
        Image.fromarray(arr).save(path)


def write_pair(output_dir, spec, before_imgs, after_imgs, instruction, meta,
               provenance=None, frame_meta=None, job_id=None):
    job_id = job_id or spec.job_id                 # 摊销模式一场景多对 → 每对独立 id
    # 目录名带上算子短名（delete/add/move/scale/rotate/replace），一眼看出是什么任务
    op_short = str((meta or {}).get("op", "")).replace("object_", "")
    dir_name = f"{job_id}_{op_short}" if op_short else job_id
    job_dir = os.path.join(output_dir, dir_name)
    os.makedirs(job_dir, exist_ok=True)

    n = min(len(before_imgs), len(after_imgs))
    view_files = []
    for v in range(n):
        bf = os.path.join(job_dir, f"before_v{v}.png")
        af = os.path.join(job_dir, f"after_v{v}.png")
        _save_png(bf, before_imgs[v])
        _save_png(af, after_imgs[v])
        view_files.append({"view": v, "before": os.path.basename(bf),
                           "after": os.path.basename(af)})

    sample = {
        "job_id": job_id,
        "seed": spec.seed,
        "instruction": instruction,        # 训练用编辑指令
        "edit": meta,                       # 结构化变更（算子、名词、参数）
        "views": view_files,
        "scene": spec.scene,
        "render": spec.render,
        "hdri": getattr(spec, "_hdri", None),
        # provenance：本 job 实际消耗了哪些资产，供「已用账本」过滤
        "provenance": provenance or {},
    }
    if frame_meta:                              # 坐标系约定 + 相机外参 + 主体信息
        sample.update(frame_meta)
    with open(os.path.join(job_dir, "sample.json"), "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2)
    return job_dir
