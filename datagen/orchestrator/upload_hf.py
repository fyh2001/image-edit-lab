"""把打包好的 WebDataset 分片上传到 Hugging Face（数据集仓库），并自动生成数据集卡片。

流程：
  1) 从 DB（或扫描分片）聚合统计：样本总数、各算子/场景/来源分布、涉及的 license；
  2) 生成 README.md 数据集卡片（YAML 头 + 说明 + 结构 + 加载示例 + license/溯源披露）；
  3) 上传分片目录 + 卡片到 HF（需 huggingface_hub + HF_TOKEN）。

用法：
  # 只生成卡片、看统计，不联网（推荐先跑这个核对）
  python -m orchestrator.upload_hf --shard-dir out/hssd_shards --dry-run
  # 真上传（需要先 `hf auth login` 或设 HF_TOKEN，仓库不存在会自动建）
  python -m orchestrator.upload_hf --repo-id <user>/<dataset> --shard-dir out/hssd_shards

手动只需一次：在 HF 上有账号 + 拿到 write token（或 hf auth login）。仓库 create_repo 会自动建。
"""
from __future__ import annotations
import os
import glob
import json
import tarfile
import argparse
import collections


def gather_stats(shard_dir: str, db_url: str = None) -> dict:
    """优先从 DB 聚合；DB 不可用则扫描分片里的 .json。返回统计 dict。"""
    stats = {"total": 0, "by_op": {}, "by_scene": {}, "by_source": {},
             "licenses": set(), "pipeline_versions": set()}
    try:
        if db_url or os.environ.get("DATABASE_URL"):
            return _stats_from_db(db_url)
    except Exception as e:
        print(f"[upload] 从 DB 统计失败，改扫描分片：{e}")
    # 回退：扫描分片 tar 里的 json
    op, scene, source = collections.Counter(), collections.Counter(), collections.Counter()
    for tar in sorted(glob.glob(os.path.join(shard_dir, "*.tar"))):
        with tarfile.open(tar) as tf:
            for m in tf.getmembers():
                if not m.name.endswith(".json"):
                    continue
                try:
                    rec = json.loads(tf.extractfile(m).read().decode("utf-8"))
                except Exception:
                    continue
                stats["total"] += 1
                op[(rec.get("edit") or {}).get("op", "?")] += 1
                prov = rec.get("provenance") or {}
                src = prov.get("scene_source") or {}
                scene[src.get("scene_id") or "?"] += 1
                source[src.get("dataset") or "?"] += 1
                if src.get("license"):
                    stats["licenses"].add(src["license"])
                if prov.get("pipeline_version"):
                    stats["pipeline_versions"].add(prov["pipeline_version"])
    stats["by_op"], stats["by_scene"], stats["by_source"] = dict(op), dict(scene), dict(source)
    return stats


def _stats_from_db(db_url: str = None) -> dict:
    from datagen.orchestrator.db import get_engine
    from sqlalchemy import text
    e = get_engine(db_url)
    out = {"total": 0, "by_op": {}, "by_scene": {}, "by_source": {},
           "licenses": set(), "pipeline_versions": set()}
    with e.connect() as c:
        out["total"] = c.execute(text("select count(*) from samples where shard_path is not null")).scalar()
        for col, key in (("edit_op", "by_op"), ("scene_id", "by_scene"), ("source_dataset", "by_source")):
            out[key] = {r[0]: r[1] for r in c.execute(text(
                f"select {col}, count(*) from samples where shard_path is not null "
                f"group by 1 order by 2 desc"))}
        for r in c.execute(text("select distinct pipeline_version from samples where pipeline_version is not null")):
            out["pipeline_versions"].add(r[0])
        # license 从 meta 溯源里取
        for r in c.execute(text(
                "select distinct meta->'provenance'->'scene_source'->>'license' from samples")):
            if r[0]:
                out["licenses"].add(r[0])
    return out


def dataset_card(stats: dict, repo_id: str = "<user>/<dataset>") -> str:
    """生成 HF 数据集卡片 README.md。"""
    total = stats["total"]
    size_cat = ("n<1K" if total < 1000 else "1K<n<10K" if total < 10000
                else "10K<n<100K" if total < 100000 else "100K<n<1M")
    ops = "\n".join(f"| {k} | {v} |" for k, v in sorted(stats["by_op"].items(),
                                                         key=lambda x: -x[1]))
    src = ", ".join(f"{k} ({v})" for k, v in stats["by_source"].items())
    lic = "\n".join(f"- {l}" for l in sorted(stats["licenses"])) or "- （见各资产 provenance）"
    n_scenes = len([s for s in stats["by_scene"] if s and s != "?"])
    return f"""---
license: other
license_name: mixed-source-see-below
task_categories:
- image-to-image
tags:
- image-editing
- instruction-guided
- synthetic
- pixel-aligned
size_categories:
- {size_cat}
---

# Object-Edit Pairs（像素级对齐的编辑三元组）

用 BlenderProc 在 3D 场景里**只改目标物体、其余不动**渲染出的
`(before, instruction, after)` 训练三元组，用于指令引导的图像编辑模型。
before/after 严格像素对齐（同一相机、同一光照），只有被编辑物体变化。

- 样本数：**{total}**  ·  场景数：约 {n_scenes}  ·  来源：{src}
- 生成管线版本：{", ".join(sorted(stats["pipeline_versions"])) or "n/a"}

## 结构（WebDataset）

分片 `edit-{{shard:06d}}.tar`，每条样本三个同 key 文件（key 含算子短名，如 `..._delete_v0`）：

```
<key>.before.png   # 编辑前 512x512
<key>.after.png    # 编辑后 512x512（与 before 像素对齐）
<key>.json         # instruction + edit + 相机内外参 + subject + provenance
```

## 加载

```python
import webdataset as wds
ds = wds.WebDataset("edit-{{000000..000000}}.tar").decode("pil")
for s in ds:
    before, after = s["before.png"], s["after.png"]
    meta = s["json"]; print(meta["instruction"])
```

## 算子分布

| edit_op | 数量 |
|---|---|
{ops}

## 字段（每条 json）

- `instruction`：训练指令（含多物体时的空间消歧，如 "delete the chair on the left"）
- `edit`：算子/名词/参数 + `validity`（碰撞/穿模/清晰度/背景稳定性分数）
- `cameras`：内参(fx/fy/cx/cy) + 外参(位置/朝向)
- `subject`：类别/描述/包围盒/`origin`(scene=编辑已有 / spawned=加入 / replaced=替换)
- `provenance`：**溯源**——场景来源(数据集+真实 scene_id) + 资产 uid/license + 完整生成配置 + 工具版本 + seed

## License / 溯源

本数据集由多来源资产渲染，各自 license 不同（详见每条样本的 `provenance`）：
{lic}

**请在使用前核对上述来源的许可条款**（如 HSSD 为 CC-BY-NC，仅限研究用途）。
每条样本都保留了完整 provenance，可精确追溯来源与生成参数。
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard-dir", default="out/hssd_shards")
    ap.add_argument("--repo-id", default=None, help="<user>/<dataset>；不给则只 dry-run")
    ap.add_argument("--db-url", default=None)
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--path-in-repo", default=None,
                    help="传到仓库内的子目录（如 data/batch-20260702），避免多批 edit-000000.tar 互相覆盖。"
                         "不给则传到根目录（会覆盖同名分片）。")
    ap.add_argument("--dry-run", action="store_true", help="只生成卡片+统计，不联网")
    args = ap.parse_args()

    stats = gather_stats(args.shard_dir, args.db_url)
    card = dataset_card(stats, args.repo_id or "<user>/<dataset>")
    card_path = os.path.join(args.shard_dir, "README.md")
    with open(card_path, "w", encoding="utf-8") as f:
        f.write(card)
    n_shards = len(glob.glob(os.path.join(args.shard_dir, "*.tar")))
    print(f"[upload] 统计：{stats['total']} 样本 / {n_shards} 分片 / 算子 {stats['by_op']}")
    print(f"[upload] 数据集卡片已写：{card_path}")

    if args.dry_run or not args.repo_id:
        print("[upload] dry-run（未上传）。真上传：--repo-id <user>/<dataset>（需 HF_TOKEN 或 hf auth login）")
        return
    try:
        from huggingface_hub import HfApi, create_repo
    except ImportError:
        raise SystemExit("需要 huggingface_hub：pip install huggingface_hub，然后 hf auth login")
    token = os.environ.get("HF_TOKEN")
    create_repo(args.repo_id, repo_type="dataset", private=args.private,
                exist_ok=True, token=token)                # 已存在则复用，不新建
    # 分片进子目录避免多批覆盖；README 始终放根目录（HF 数据集卡片要在根）
    HfApi().upload_folder(folder_path=args.shard_dir, repo_id=args.repo_id,
                          repo_type="dataset", token=token,
                          path_in_repo=args.path_in_repo or ".",
                          allow_patterns=["*.tar"] if args.path_in_repo else ["*.tar", "README.md"])
    if args.path_in_repo:                                   # 卡片单独传根目录
        HfApi().upload_file(path_or_fileobj=card_path, path_in_repo="README.md",
                            repo_id=args.repo_id, repo_type="dataset", token=token)
    where = f"{args.path_in_repo}/" if args.path_in_repo else "根目录"
    print(f"[upload] 已上传 {n_shards} 分片 → {where} + 卡片 → "
          f"https://huggingface.co/datasets/{args.repo_id}")


if __name__ == "__main__":
    main()
