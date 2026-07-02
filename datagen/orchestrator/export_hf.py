"""把原始产物导出为 **HuggingFace `datasets` 原生格式**（Parquet + Image 特征），
带**场景级 train/val/test split**，推到 HF 后自动出 Dataset Viewer（网页可翻图看指令）+
`load_dataset()` 一行可读。

相比 WebDataset：可浏览、可按列筛（算子/场景/质量/split）、schema 对齐标准编辑数据集
（source_image / edit_instruction / target_image）。

用法：
  # 本地生成 parquet + 看统计，不联网（先核对）
  python -m orchestrator.export_hf --raw-dir out/hssd_raw --out out/hf_parquet --dry-run
  # 直接推到 HF（需 hf auth login）
  python -m orchestrator.export_hf --raw-dir out/hssd_raw --repo-id fyh2001/obj-edits-hssd-v1 --private

**默认全 `train`**（合成训练数据 → 全用来训，真评测在外部真实 benchmark 上做）。
想要训练时的过拟合探针，用 `--holdout-scenes <id...>` 把整间房留作 validation（按房间分、无泄漏）。
"""
from __future__ import annotations
import os
import re
import json
import argparse
import collections

from datagen.orchestrator.collector import iter_samples

_DISAMBIG = re.compile(r"\b(on the (left|right)|nearest|farthest)\b")


def build_rows(raw_dir, holdout_scenes=None):
    """遍历原始产物 → 每条一行（图为文件路径，datasets Image() 会自动载入/编码）。

    **默认全进 `train`**（合成训练数据 → 全用来训，真评测在外部真实 benchmark 上做）。
    只有显式 `holdout_scenes`（一组 scene_id）时，那些**整间房**进 `validation`（按房间分、无泄漏），
    做个廉价的过拟合探针。
    """
    holdout = set(str(s) for s in (holdout_scenes or []))
    by_split = collections.defaultdict(list)
    for key, before, after, rec in iter_samples(raw_dir):
        edit = rec.get("edit") or {}
        v = edit.get("validity") or {}
        q = v.get("quality") or {}
        subj = rec.get("subject") or {}
        prov = rec.get("provenance") or {}
        src = prov.get("scene_source") or {}
        scene_id = src.get("scene_id")
        # 优先用 captioner 产的自然指令（caption），没打标就退回基线模板指令。
        instr = rec.get("caption") or rec.get("instruction") or ""
        split = "validation" if str(scene_id) in holdout else "train"
        by_split[split].append({
            "key": key,
            "source_image": before,                 # 路径 → Image() 载入
            "target_image": after,
            "edit_instruction": instr,
            "caption_style": rec.get("caption_style"),
            "edit_op": edit.get("op"),
            "scene_id": scene_id,
            "source_dataset": src.get("dataset"),
            "subject_category": subj.get("category"),
            "subject_origin": subj.get("origin"),     # scene / spawned / replaced
            "sharpness": _f(q.get("sharpness")),
            "change_ratio": _f(q.get("change_ratio", v.get("pixel_change_ratio"))),
            "disambiguated": bool(_DISAMBIG.search(instr)),
            "meta_json": json.dumps(rec, ensure_ascii=False),   # 完整溯源/参数，需要就解
        })
    return by_split


def _f(x):
    try:
        return float(x)
    except Exception:
        return None


def _features():
    from datasets import Features, Image, Value
    return Features({
        "key": Value("string"),
        "source_image": Image(),                    # HF Viewer 会渲染缩略图
        "target_image": Image(),
        "edit_instruction": Value("string"),
        "caption_style": Value("string"),
        "edit_op": Value("string"),
        "scene_id": Value("string"),
        "source_dataset": Value("string"),
        "subject_category": Value("string"),
        "subject_origin": Value("string"),
        "sharpness": Value("float32"),
        "change_ratio": Value("float32"),
        "disambiguated": Value("bool"),
        "meta_json": Value("string"),
    })


def build_dataset(raw_dir, holdout_scenes=None):
    """原始产物 → HF DatasetDict（带 Image 特征 + 场景级 split）。CLI 与 pack 算子共用。"""
    from datasets import Dataset, DatasetDict
    by_split = build_rows(raw_dir, holdout_scenes)
    feats = _features()
    dd = DatasetDict({s: Dataset.from_list(rows, features=feats)
                      for s, rows in by_split.items() if rows})
    return dd


def save_parquet(dd, out_dir):
    """DatasetDict → 本地 parquet（每 split 一个文件）。返回 {split: path}。"""
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    for s, ds in dd.items():
        p = os.path.join(out_dir, f"{s}.parquet")
        ds.to_parquet(p)
        paths[s] = p
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default="out/hssd_raw")
    ap.add_argument("--out", default="out/hf_parquet", help="dry-run 时本地落地目录")
    ap.add_argument("--repo-id", default=None, help="<user>/<dataset>；给了就 push_to_hub")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--holdout-scenes", nargs="+", default=None,
                    help="可选：把这些 scene_id 整间房留作 validation（过拟合探针）。不给则**全 train**。")
    ap.add_argument("--dry-run", action="store_true", help="只本地生成 parquet，不联网")
    args = ap.parse_args()

    dd = build_dataset(args.raw_dir, args.holdout_scenes)
    print("[export] split / 样本数：", {s: len(ds) for s, ds in dd.items()})
    for s, ds in dd.items():
        print(f"  {s}: {len(ds)} | 算子 {dict(collections.Counter(ds['edit_op']))}")

    if args.repo_id and not args.dry_run:
        dd.push_to_hub(args.repo_id, private=args.private)
        print(f"[export] 已推送 → https://huggingface.co/datasets/{args.repo_id}")
    else:
        for s, p in save_parquet(dd, args.out).items():
            print(f"[export] {s} → {p}")
        print("[export] dry-run（未上传）。推送：--repo-id <user>/<dataset>（需 hf auth login）")


if __name__ == "__main__":
    main()
