"""渲染后的**后处理算子**（打包 / 上传），注册为 ray_exec 任务类型，可用 pipeline 编排。

这些是 **reduce/sink** 型（对整批产物操作一次，不是逐对），所以在 render 全跑完后作为
pipeline 的后续阶段跑：`render(扇出) → pack_parquet(1个) → upload_hf(1个)`。
每个算子吃上一步的输出 + config 里的 params，产出喂下一步（parquet_dir → 上传）。
"""
from __future__ import annotations
import os

from common.ray_exec import register_task


@register_task("pack_parquet")
def pack_parquet(payload):
    """原始产物目录 → HF 原生 Parquet（带 Image 特征 + 场景级 split）。

    payload: {raw_dir, out_dir?, holdout_scenes?}
    返回:    {parquet_dir, splits: {split: n}, n}
    """
    from datagen.orchestrator import export_hf
    raw_dir = payload["raw_dir"]
    out_dir = payload.get("out_dir") or os.path.join(os.path.dirname(raw_dir.rstrip("/")), "hf_parquet")
    dd = export_hf.build_dataset(raw_dir, payload.get("holdout_scenes"))
    export_hf.save_parquet(dd, out_dir)
    splits = {s: len(ds) for s, ds in dd.items()}
    print(f"[pack_parquet] {raw_dir} → {out_dir} | split {splits}")
    return {"parquet_dir": out_dir, "splits": splits, "n": sum(splits.values())}


@register_task("pack_webdataset")
def pack_webdataset(payload):
    """原始产物 → WebDataset tar 分片（流式训练用）。payload: {raw_dir, shard_dir?, shard_size?}"""
    from datagen.orchestrator.collector import pack
    raw_dir = payload["raw_dir"]
    shard_dir = payload.get("shard_dir") or os.path.join(os.path.dirname(raw_dir.rstrip("/")), "shards")
    mapping = pack(raw_dir, shard_dir, int(payload.get("shard_size", 1000)))
    print(f"[pack_webdataset] {raw_dir} → {shard_dir} | {len(mapping)} 样本")
    return {"shard_dir": shard_dir, "n": len(mapping)}


@register_task("upload_hf")
def upload_hf(payload):
    """把 pack_parquet 产出的 parquet 推到 HF（自动 Dataset Viewer + load_dataset）。

    payload: {parquet_dir, repo_id, private?}（parquet_dir 由上一步 pack_parquet 喂入）
    返回:    {url}
    """
    import glob
    from datasets import load_dataset
    repo_id = payload["repo_id"]
    parquet_dir = payload.get("parquet_dir") or payload.get("out_dir")
    private = bool(payload.get("private", False))
    files = {os.path.basename(p)[:-len(".parquet")]: p
             for p in glob.glob(os.path.join(parquet_dir, "*.parquet"))}
    if not files:
        raise RuntimeError(f"upload_hf: {parquet_dir} 下没有 parquet")
    dd = load_dataset("parquet", data_files=files)          # 按 split 读回
    dd.push_to_hub(repo_id, private=private, token=os.environ.get("HF_TOKEN"))
    url = f"https://huggingface.co/datasets/{repo_id}"
    print(f"[upload_hf] {parquet_dir} → {url}（split {list(files)}）")
    return {"url": url}
