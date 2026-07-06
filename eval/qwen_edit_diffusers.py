"""直接加载 Qwen-Image-Edit 跑推理（diffusers，**不用起 serve**）。

加载一次 pipeline，循环编辑。适合"直接试效果"、离线批量。要高吞吐再上多卡数据并行(见文末)。

────────────────────────────────────────────────────────────────────────
安装(在有 CUDA 的机器上，如 8×H100；本地 Mac 跑不动 20B 扩散模型)：
    pip install -U diffusers transformers accelerate torch pillow

单图试：
    python eval/qwen_edit_diffusers.py --image before.png --prompt "把桌上的花瓶移到左边" --out out.png

批量跑我们渲的数据(读 sample.json 的 caption/instruction + before 图) + 三栏对比 HTML：
    python eval/qwen_edit_diffusers.py --dataset-dir ./out/gallery_v5 --out-dir ./out/qwen_eval --limit 40

模型变体：
    --model Qwen/Qwen-Image-Edit          默认，用 QwenImageEditPipeline（单图编辑）
    --model Qwen/Qwen-Image-Edit-2509 --plus   用 QwenImageEditPlusPipeline（2509，改进版）
显存：20B 模型 bf16 约 ~40GB+，单张 H100(80G) 够；紧张就加 --cpu-offload。
────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import argparse
import glob
import json
import os

from eval._compare_html import write_compare_html, _first     # 共享对比 HTML(无额外依赖)


def load_pipe(model, dtype="bf16", device="cuda", cpu_offload=False, use_plus=False):
    import torch
    from diffusers import QwenImageEditPipeline
    torch_dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[dtype]
    if use_plus:
        from diffusers import QwenImageEditPlusPipeline as Pipe   # 2509 版
    else:
        Pipe = QwenImageEditPipeline
    pipe = Pipe.from_pretrained(model, torch_dtype=torch_dtype)
    if cpu_offload:
        pipe.enable_model_cpu_offload()      # 省显存(慢一点)：各子模块按需搬上 GPU
    else:
        pipe.to(device)
    try:
        pipe.set_progress_bar_config(disable=True)
    except Exception:
        pass
    return pipe


def edit_image(pipe, image_path, prompt, steps=50, cfg=4.0, seed=0, negative=" "):
    import torch
    from PIL import Image
    image = Image.open(image_path).convert("RGB")
    gen = torch.manual_seed(int(seed))       # 固定种子可复现
    with torch.inference_mode():
        out = pipe(
            image=image,
            prompt=prompt,
            negative_prompt=negative,
            num_inference_steps=int(steps),
            true_cfg_scale=float(cfg),        # Qwen-Image 用 true CFG
            generator=gen,
        )
    return out.images[0]


def run_single(pipe, args):
    img = edit_image(pipe, args.image, args.prompt, args.steps, args.cfg, args.seed)
    img.save(args.out)
    print(f"[qwen-edit] {args.image} + '{args.prompt}' → {args.out}")


def run_dataset(pipe, args):
    os.makedirs(args.out_dir, exist_ok=True)
    jobs = []
    for sj in sorted(glob.glob(os.path.join(args.dataset_dir, "*", "sample.json"))):
        d = os.path.dirname(sj)
        before = _first(glob.glob(os.path.join(d, "before_*.png")))
        if not before:
            continue
        try:
            rec = json.load(open(sj))
        except Exception:
            continue
        prompt = rec.get("caption") or rec.get("instruction")
        if not prompt:
            continue
        jobs.append({"key": os.path.basename(d), "before": before, "prompt": prompt,
                     "op": (rec.get("edit") or {}).get("op"),
                     "target": _first(glob.glob(os.path.join(d, "after_*.png")))})
    # 分片(多卡数据并行时用)：--shard i/n 只跑属于本片的
    if args.shard:
        i, n = (int(x) for x in args.shard.split("/"))
        jobs = [j for k, j in enumerate(jobs) if k % n == i]
    if args.limit:
        jobs = jobs[: args.limit]

    print(f"[qwen-edit] 批量 {len(jobs)} 张 …")
    results = []
    for k, job in enumerate(jobs, 1):
        out_path = os.path.join(args.out_dir, f"{job['key']}_qwen.png")
        try:
            edit_image(pipe, job["before"], job["prompt"], args.steps, args.cfg, args.seed).save(out_path)
            job["result"] = out_path
        except Exception as e:
            job["result"] = None
            job["error"] = repr(e)
            print(f"  ✗ {job['key']}: {e}")
        results.append(job)
        if k % 5 == 0:
            print(f"  完成 {k}/{len(jobs)}")

    ok = sum(1 for r in results if r.get("result"))
    print(f"[qwen-edit] 成功 {ok}/{len(results)}")
    html = os.path.join(args.out_dir, "compare.html")
    write_compare_html(results, html)
    print(f"[qwen-edit] 对比页(before | Qwen结果 | 我们的target) → {html}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen-Image-Edit")
    ap.add_argument("--plus", action="store_true", help="用 QwenImageEditPlusPipeline(2509)")
    ap.add_argument("--dtype", default="bf16", choices=["bf16", "fp16", "fp32"])
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--cpu-offload", action="store_true", help="省显存(慢)")
    ap.add_argument("--image"); ap.add_argument("--prompt"); ap.add_argument("--out", default="qwen_out.png")
    ap.add_argument("--dataset-dir"); ap.add_argument("--out-dir", default="./out/qwen_eval")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--shard", default=None, help="多卡数据并行: 'i/n'，只跑第 i 片(共 n 片)")
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--cfg", type=float, default=4.0)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if not args.dataset_dir and not (args.image and args.prompt):
        ap.error("要么给 --image + --prompt(单图)，要么给 --dataset-dir(批量)")

    pipe = load_pipe(args.model, args.dtype, args.device, args.cpu_offload, args.plus)
    if args.dataset_dir:
        run_dataset(pipe, args)
    else:
        run_single(pipe, args)


if __name__ == "__main__":
    main()

# 多卡数据并行(不用 serve，8 卡各跑一片)：
#   for i in $(seq 0 7); do
#     CUDA_VISIBLE_DEVICES=$i python eval/qwen_edit_diffusers.py --dataset-dir ./out/xxx \
#       --out-dir ./out/qwen_eval --shard $i/8 &
#   done; wait
