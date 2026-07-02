"""ingest 纯函数 + DB 往返单测（用临时 sqlite，不依赖 Postgres）。"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datagen.orchestrator.ingest import sample_row, used_uids, ingest_samples, ingest_asset_catalog
from datagen.orchestrator.db import get_engine, init_db, make_session, Sample, Asset


REC = {
    "job_id": "job_0", "view": 0, "seed": 7,
    "scene": {"name": "tabletop"},
    "instruction": "move the chair left",
    "edit": {"op": "object_move",
             "validity": {"collision_free": True, "reseated": True, "num_attempts": 2,
                          "penetration_depth": 0.0, "floating_gap": 0.001,
                          "pixel_change_ratio": 0.1,
                          "quality": {"change_ratio": 0.1, "sharpness": 40.0, "background_diff": 0.2},
                          "direction_check": {"consistent": True}}},
    "subject": {"category": "chair", "asset_uid": "uid_a", "description": "Cozy Armchair"},
    "provenance": {"objaverse_uids": ["uid_a", "uid_b"]},
}


def test_sample_row_extracts_fields():
    r = sample_row("job_0_v0", REC)
    assert r["key"] == "job_0_v0" and r["edit_op"] == "object_move"
    assert r["scene_name"] == "tabletop" and r["subject_category"] == "chair"
    assert r["sharpness"] == 40.0 and r["background_diff"] == 0.2
    assert r["change_ratio"] == 0.1 and r["floating_gap"] == 0.001
    assert r["direction_consistent"] is True and r["reseated"] is True
    assert r["meta"] is REC


def test_used_uids_union_subject_and_provenance():
    assert used_uids(REC) == {"uid_a", "uid_b"}
    assert used_uids({"subject": {}}) == set()


def _write_job(raw, job_id, rec):
    d = os.path.join(raw, job_id)
    os.makedirs(d, exist_ok=True)
    for w in ("before", "after"):
        open(os.path.join(d, f"{w}_v0.png"), "wb").write(b"PNG")
    meta = dict(rec)
    meta["job_id"] = job_id
    meta["views"] = [{"view": 0, "before": "before_v0.png", "after": "after_v0.png"}]
    json.dump(meta, open(os.path.join(d, "sample.json"), "w"))


def test_db_roundtrip(tmp_path):
    raw = str(tmp_path / "raw")
    _write_job(raw, "job_0", REC)
    engine = get_engine(f"sqlite:///{tmp_path}/t.db")
    init_db(engine)
    n = ingest_samples(engine, raw)
    assert n == 1
    with make_session(engine)() as s:
        row = s.get(Sample, "job_0_move_v0")             # key 带算子短名
        assert row.instruction == "move the chair left" and row.edit_op == "object_move"
        assert row.sharpness == 40.0
        # 用过的两个 uid 都进了 assets，used_count 记上
        a = s.get(Asset, "uid_a")
        assert a is not None and a.used_count == 1


def test_ingest_idempotent(tmp_path):
    raw = str(tmp_path / "raw")
    _write_job(raw, "job_0", REC)
    engine = get_engine(f"sqlite:///{tmp_path}/t.db")
    init_db(engine)
    ingest_samples(engine, raw)
    ingest_samples(engine, raw)                     # 再灌一次不应重复/报错
    with make_session(engine)() as s:
        assert s.query(Sample).count() == 1
        assert s.get(Asset, "uid_a").used_count == 1   # 不累加


def test_used_asset_uids(tmp_path):
    from datagen.orchestrator.ingest import used_asset_uids
    raw = str(tmp_path / "raw")
    _write_job(raw, "job_0", REC)
    engine = get_engine(f"sqlite:///{tmp_path}/t.db")
    init_db(engine)
    ingest_samples(engine, raw)
    assert used_asset_uids(engine) == {"uid_a", "uid_b"}


def test_asset_catalog(tmp_path):
    meta = {"uid_a": {"category": "chair", "name": "Armchair", "tags": ["wood"], "license": "by"}}
    p = tmp_path / "meta.json"
    p.write_text(json.dumps(meta))
    engine = get_engine(f"sqlite:///{tmp_path}/t.db")
    init_db(engine)
    assert ingest_asset_catalog(engine, str(p)) == 1
    with make_session(engine)() as s:
        a = s.get(Asset, "uid_a")
        assert a.category == "chair" and a.license == "by" and a.tags == ["wood"]
