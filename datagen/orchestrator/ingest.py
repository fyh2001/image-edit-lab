"""
把 worker 落地的 sample.json 灌进数据库（samples / assets / asset_usage）。
与渲染解耦：worker 只写文件，这一步离线读文件入库，可重复跑（幂等 upsert）。

    export DATABASE_URL='postgresql+psycopg://pipeline:***@localhost:5433/blender_pipeline'
    python -m orchestrator.ingest --raw-dir out/smoke_raw --asset-meta assets/objaverse_meta.json
    # 或 --config configs/default.yaml（读其中 run.output_dir）
"""
from __future__ import annotations
import os
import json
import argparse

from sqlalchemy import select, func

from datagen.orchestrator.collector import iter_samples
from datagen.orchestrator.db import (get_engine, init_db, make_session, Sample, Asset, AssetUsage)


# ----------------------- 纯函数（可单测） -----------------------

def _num(x):
    try:
        return float(x) if x is not None else None
    except Exception:
        return None


def sample_row(key: str, rec: dict) -> dict:
    """sample.json（单机位记录）→ samples 表的一行字段。纯函数。"""
    edit = rec.get("edit") or {}
    v = edit.get("validity") or {}
    q = v.get("quality") or {}
    subj = rec.get("subject") or {}
    dc = v.get("direction_check") or {}
    prov = rec.get("provenance") or {}
    src = prov.get("scene_source") or {}
    return {
        "key": key,
        "job_id": rec.get("job_id"),
        "view": rec.get("view", 0),
        "seed": rec.get("seed"),
        "scene_name": (rec.get("scene") or {}).get("name"),
        "scene_id": src.get("scene_id"),                 # 溯源：解析后的真实场景 id
        "source_dataset": src.get("dataset"),
        "pipeline_version": prov.get("pipeline_version"),
        "edit_op": edit.get("op"),
        "instruction": rec.get("instruction"),
        "subject_category": subj.get("category"),
        "subject_uid": subj.get("asset_uid"),
        "subject_description": subj.get("description"),
        "change_ratio": _num(q.get("change_ratio", v.get("pixel_change_ratio"))),
        "sharpness": _num(q.get("sharpness")),
        "background_diff": _num(q.get("background_diff")),
        "penetration_depth": _num(v.get("penetration_depth")),
        "floating_gap": _num(v.get("floating_gap")),
        "reseated": v.get("reseated"),
        "collision_free": v.get("collision_free"),
        "num_attempts": v.get("num_attempts"),
        "direction_consistent": dc.get("consistent"),
        "meta": rec,
    }


def used_uids(rec: dict) -> set:
    """本样本用到的资产 uid（主体 + provenance 里的 objaverse uid）。纯函数。"""
    uids = set()
    subj = rec.get("subject") or {}
    if subj.get("asset_uid"):
        uids.add(subj["asset_uid"])
    for u in (rec.get("provenance") or {}).get("objaverse_uids") or []:
        uids.add(u)
    return {u for u in uids if u}


# ----------------------- 入库 -----------------------

def ingest_asset_catalog(engine, meta_json_path: str, source: str = "objaverse") -> int:
    if not os.path.exists(meta_json_path):
        return 0
    meta = json.load(open(meta_json_path, encoding="utf-8"))
    Sess = make_session(engine)
    with Sess() as s:
        for uid, m in meta.items():
            s.merge(Asset(uid=uid, source=source, category=m.get("category"),
                          name=m.get("name"), license=m.get("license"),
                          tags=m.get("tags"), used_count=0))
        s.commit()
    return len(meta)


def used_asset_uids(engine) -> set:
    """DB 里已用过的资产 uid 集合（asset_usage 去重）——供 prefetch 跨批次排除。"""
    with make_session(engine)() as s:
        return {r[0] for r in s.execute(select(AssetUsage.asset_uid).distinct()).all()}


def _recount_assets(s):
    """按 asset_usage 重算每个资产的 used_count；用过但目录里没有的补一条 Asset。"""
    rows = s.execute(select(AssetUsage.asset_uid, func.count())
                     .group_by(AssetUsage.asset_uid)).all()
    for uid, cnt in rows:
        a = s.get(Asset, uid)
        if a is None:
            src = uid.split(":", 1)[0] if ":" in uid else (
                "primitive" if uid.startswith("prim_") else "objaverse")
            a = Asset(uid=uid, source=src)
            s.add(a)
        a.used_count = cnt


def ingest_samples(engine, raw_dir: str) -> int:
    Sess = make_session(engine)
    n = 0
    with Sess() as s:
        for key, _before, _after, rec in iter_samples(raw_dir):
            s.merge(Sample(**sample_row(key, rec)))
            s.query(AssetUsage).filter_by(sample_key=key).delete()   # 幂等：先清本样本旧账
            for uid in used_uids(rec):
                s.add(AssetUsage(asset_uid=uid, job_id=rec.get("job_id"), sample_key=key))
            n += 1
        s.commit()
        _recount_assets(s)
        s.commit()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--raw-dir", default=None)
    ap.add_argument("--asset-meta", default="assets/objaverse_meta.json")
    ap.add_argument("--db-url", default=None, help="缺省用 $DATABASE_URL 或本地 sqlite")
    args = ap.parse_args()

    raw_dir = args.raw_dir
    if raw_dir is None and args.config:
        import yaml
        raw_dir = yaml.safe_load(open(args.config))["run"]["output_dir"]
    if raw_dir is None:
        raise SystemExit("需要 --raw-dir 或 --config")

    engine = get_engine(args.db_url)
    init_db(engine)
    n_assets = ingest_asset_catalog(engine, args.asset_meta)
    n_samples = ingest_samples(engine, raw_dir)
    print(f"入库完成：samples {n_samples} 行，资产目录 {n_assets} 条 <- {raw_dir}")


if __name__ == "__main__":
    main()
