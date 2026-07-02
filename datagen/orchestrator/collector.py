"""
把 worker 落地的「每对一文件夹」原始产物打包成 WebDataset tar 分片，
便于上传 Hugging Face / 对象存储，并在训练时流式读取。

每个训练样本一个 webdataset key，包含：
    <key>.before.png  <key>.after.png  <key>.json
`.json` 里带**完整 metadata**（instruction/edit/validity+quality/subject/cameras/...），
下游可据此做二次筛选（如按 quality 分数、按算子类型）。一个 tar 分片放 shard_size 个样本。

用法：
    python -m orchestrator.collector --config configs/default.yaml
    python -m orchestrator.collector --raw-dir out/smoke_raw --shard-dir out/smoke_shards
"""
from __future__ import annotations
import os
import json
import glob
import argparse

import yaml


def iter_samples(raw_dir):
    """遍历原始产物，每个机位产出一个训练样本。

    纯 Python（不依赖 webdataset），便于单测。
    Yields: (key, before_path, after_path, record_dict)
    record_dict = 完整 sample.json（去掉冗余的 views 文件清单）+ 当前 view 索引。
    """
    for sample_json in sorted(glob.glob(os.path.join(raw_dir, "*", "sample.json"))):
        job_dir = os.path.dirname(sample_json)
        try:
            with open(sample_json, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        base = {k: v for k, v in meta.items() if k != "views"}
        # 算子短名进 key → 分片文件名、DB 主键都能一眼看出任务类型
        op_short = str(meta.get("edit", {}).get("op", "")).replace("object_", "")
        for view in meta.get("views", []):
            before = os.path.join(job_dir, view["before"])
            after = os.path.join(job_dir, view["after"])
            if not (os.path.exists(before) and os.path.exists(after)):
                continue
            key = (f"{meta['job_id']}_{op_short}_v{view['view']}" if op_short
                   else f"{meta['job_id']}_v{view['view']}")
            record = dict(base)
            record["view"] = view["view"]
            yield key, before, after, record


def pack(raw_dir, shard_dir, shard_size):
    """把 raw_dir 打成 WebDataset 分片，返回 {key: 分片文件名}（供回填 shard_path）。"""
    import webdataset as wds
    os.makedirs(shard_dir, exist_ok=True)
    pattern = os.path.join(shard_dir, "edit-%06d.tar")
    mapping = {}
    with wds.ShardWriter(pattern, maxcount=shard_size) as sink:
        for key, before, after, record in iter_samples(raw_dir):
            with open(before, "rb") as f:
                before_bytes = f.read()
            with open(after, "rb") as f:
                after_bytes = f.read()
            sink.write({
                "__key__": key,
                "before.png": before_bytes,
                "after.png": after_bytes,
                "json": json.dumps(record, ensure_ascii=False).encode("utf-8"),
            })
            mapping[key] = os.path.basename(sink.fname)   # 当前样本落到的分片
    return mapping


def backfill_shard_paths(db_url, mapping):
    """把每个样本落到的分片文件名写回 samples.shard_path（样本行需已由 ingest 建好）。"""
    from datagen.orchestrator.db import get_engine, Sample, make_session
    engine = get_engine(db_url)
    updated = 0
    with make_session(engine)() as s:
        for key, shard in mapping.items():
            row = s.get(Sample, key)
            if row is not None:
                row.shard_path = shard
                updated += 1
        s.commit()
    return updated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--raw-dir", default=None, help="覆盖 run.output_dir")
    ap.add_argument("--shard-dir", default=None, help="覆盖 run.shard_dir")
    ap.add_argument("--shard-size", type=int, default=None, help="覆盖 run.shard_size")
    ap.add_argument("--db-url", default=None,
                    help="给了就把每个样本的分片路径回填进 samples.shard_path（需先 ingest）")
    args = ap.parse_args()

    raw_dir, shard_dir, shard_size = args.raw_dir, args.shard_dir, args.shard_size
    if raw_dir is None or shard_dir is None or shard_size is None:
        with open(args.config, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        raw_dir = raw_dir or cfg["run"]["output_dir"]
        shard_dir = shard_dir or cfg["run"]["shard_dir"]
        shard_size = shard_size or cfg["run"]["shard_size"]

    mapping = pack(raw_dir, shard_dir, int(shard_size))
    print(f"打包完成：{len(mapping)} 个样本 -> {shard_dir}/edit-*.tar")
    if args.db_url or os.environ.get("DATABASE_URL"):
        updated = backfill_shard_paths(args.db_url, mapping)
        print(f"已回填 shard_path：{updated} 行")
    print(f"上传到 Hugging Face： hf upload <repo> {shard_dir} --repo-type dataset")


if __name__ == "__main__":
    main()
