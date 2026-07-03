"""用 SGLang Diffusion 跑 Qwen-Image-Edit 推理，试基座模型在我们数据上的编辑效果。

SGLang(不是 vLLM)从 2025-11 起有 SGLang-Diffusion，day-0 支持 Qwen-Image-Edit(扩散 20B)。
本脚本是**客户端**：连 SGLang 起的 OpenAI 兼容服务，调 images.edit。

────────────────────────────────────────────────────────────────────────
① 在 8×H100 服务器上先起服务（另开一个终端/ tmux）：
    pip install "sglang[diffusion]"          # 按官方 SGLang-Diffusion 安装指引为准
    sglang serve --model-path Qwen/Qwen-Image-Edit-2511 --num-gpus 8 --tp-size 8
    # 显存紧张可加： --dit-cpu-offload --text-encoder-cpu-offload --vae-cpu-offload
    # 想更快可开 Cache-DiT： SGLANG_CACHE_DIT_ENABLED=true sglang serve ...
    # 默认端口 3000 → base_url http://localhost:3000/v1

② 跑推理（本脚本）：
    # 单图测：
    python eval/qwen_edit_sglang.py --image before.png --prompt "把桌上的花瓶移到左边" --out out.png
    # 批量跑我们渲的数据(读 sample.json 的 caption/instruction + before 图)，出三栏对比 HTML：
    python eval/qwen_edit_sglang.py --dataset-dir ./out/gallery_v5 --out-dir ./out/qwen_eval --limit 40
────────────────────────────────────────────────────────────────────────
依赖(客户端)： pip install openai pillow
"""
from __future__ import annotations
import argparse
import base64
import glob
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI
from PIL import Image


def make_client(base_url, api_key="EMPTY"):
    return OpenAI(api_key=api_key, base_url=base_url)


def edit_image(client, model, image_path, prompt, extra=None):
    """调 Qwen-Image-Edit 编辑一张图，返回 PIL.Image。extra 里可放 steps/cfg/seed 等(经 extra_body 透传)。"""
    resp = client.images.edit(
        model=model,
        image=open(image_path, "rb"),
        prompt=prompt,
        n=1,
        response_format="b64_json",
        # steps/guidance/seed 若服务支持，走 extra_body 透传（键名以 SGLang 版本为准，见文档）
        extra_body=extra or {},
    )
    return Image.open(io.BytesIO(base64.b64decode(resp.data[0].b64_json))).convert("RGB")


# ---------------- 单图 ----------------
def run_single(args):
    client = make_client(args.base_url)
    extra = _extra_from_args(args)
    img = edit_image(client, args.model, args.image, args.prompt, extra)
    img.save(args.out)
    print(f"[qwen-edit] {args.image} + '{args.prompt}' → {args.out}")


# ---------------- 批量跑我们的数据 ----------------
def _first(paths):
    return sorted(paths)[0] if paths else None


def run_dataset(args):
    client = make_client(args.base_url)
    extra = _extra_from_args(args)
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
        target = _first(glob.glob(os.path.join(d, "after_*.png")))   # 我们的 GT after，供对比
        jobs.append({"key": os.path.basename(d), "before": before, "prompt": prompt,
                     "op": (rec.get("edit") or {}).get("op"), "target": target})
    if args.limit:
        jobs = jobs[: args.limit]

    results = []

    def work(job):
        out_path = os.path.join(args.out_dir, f"{job['key']}_qwen.png")
        try:
            edit_image(client, args.model, job["before"], job["prompt"], extra).save(out_path)
            job["result"] = out_path
        except Exception as e:
            job["result"] = None
            job["error"] = repr(e)
        return job

    print(f"[qwen-edit] 批量 {len(jobs)} 张，并发 {args.concurrency} …")
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for i, job in enumerate(as_completed([ex.submit(work, j) for j in jobs]), 1):
            r = job.result()
            results.append(r)
            if i % 10 == 0:
                print(f"  完成 {i}/{len(jobs)}")

    ok = sum(1 for r in results if r.get("result"))
    print(f"[qwen-edit] 成功 {ok}/{len(results)}")
    html = os.path.join(args.out_dir, "compare.html")
    _write_compare_html(results, html)
    print(f"[qwen-edit] 对比页(before | Qwen结果 | 我们的target) → {html}")


def _b64(path, mx=384, q=82):
    im = Image.open(path).convert("RGB")
    im.thumbnail((mx, mx))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=q)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _cell(path, label):
    if not path or not os.path.exists(path):
        return f'<figure class="miss"><figcaption>{label}(无)</figcaption></figure>'
    return f'<figure><img src="{_b64(path)}"><figcaption>{label}</figcaption></figure>'


def _write_compare_html(results, out):
    cards = []
    for r in results:
        instr = str(r.get("prompt", "")).replace("<", "&lt;")
        cards.append(f"""<div class="c"><div class="h"><span class="op">{(r.get('op') or '')[7:]}</span>
          <span class="i">{instr}</span></div><div class="row">
          {_cell(r.get('before'), 'before')}{_cell(r.get('result'), 'Qwen-Edit')}{_cell(r.get('target'), '我们的 target')}
          </div></div>""")
    html = f"""<!doctype html><meta charset="utf-8"><title>Qwen-Image-Edit 效果对比</title>
<style>:root{{color-scheme:dark}}body{{margin:0;background:#111418;color:#e6e8ea;font:14px/1.5 -apple-system,'PingFang SC',sans-serif}}
h1{{padding:16px 24px;margin:0;font-size:18px;border-bottom:1px solid #262b31}}
.grid{{display:grid;grid-template-columns:1fr;gap:14px;padding:18px 24px}}
.c{{background:#181c22;border:1px solid #262b31;border-radius:12px;overflow:hidden}}
.h{{padding:9px 12px;display:flex;gap:8px;align-items:baseline}}
.op{{background:#5a636e;color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:6px}}
.row{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:2px;background:#0c0e11}}
figure{{margin:0;position:relative;min-height:60px}}figure img{{width:100%;display:block}}
figcaption{{position:absolute;left:6px;top:6px;background:#000a;color:#fff;font-size:10px;padding:1px 6px;border-radius:4px}}
.miss{{display:flex;align-items:center;justify-content:center;color:#5f6b76}}</style>
<h1>Qwen-Image-Edit 基座效果 · {len(results)} 张（before ｜ Qwen 编辑结果 ｜ 我们渲的 target）</h1>
<div class="grid">{''.join(cards)}</div>"""
    open(out, "w", encoding="utf-8").write(html)


def _extra_from_args(args):
    extra = {}
    if args.steps is not None:
        extra["num_inference_steps"] = args.steps
    if args.cfg is not None:
        extra["guidance_scale"] = args.cfg      # 键名以 SGLang 版本为准，不支持就忽略
    if args.seed is not None:
        extra["seed"] = args.seed
    return extra


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:3000/v1")
    ap.add_argument("--model", default="Qwen/Qwen-Image-Edit-2511")
    ap.add_argument("--image"); ap.add_argument("--prompt"); ap.add_argument("--out", default="qwen_out.png")
    ap.add_argument("--dataset-dir"); ap.add_argument("--out-dir", default="./out/qwen_eval")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--steps", type=int, default=None)
    ap.add_argument("--cfg", type=float, default=None)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    if args.dataset_dir:
        run_dataset(args)
    elif args.image and args.prompt:
        run_single(args)
    else:
        ap.error("要么给 --image + --prompt(单图)，要么给 --dataset-dir(批量)")


if __name__ == "__main__":
    main()
