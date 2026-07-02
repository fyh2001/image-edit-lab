"""collector.iter_samples 单测（纯 Python，不依赖 webdataset）。"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datagen.orchestrator.collector import iter_samples


def _make_job(raw, job_id, views, instruction="move the cube left"):
    d = os.path.join(raw, job_id)
    os.makedirs(d, exist_ok=True)
    view_entries = []
    for v in views:
        for which in ("before", "after"):
            open(os.path.join(d, f"{which}_v{v}.png"), "wb").write(b"PNG")
        view_entries.append({"view": v, "before": f"before_v{v}.png", "after": f"after_v{v}.png"})
    meta = {"job_id": job_id, "seed": 1, "instruction": instruction,
            "edit": {"op": "object_move", "validity": {"quality": {"sharpness": 40}}},
            "subject": {"category": "cube"}, "views": view_entries}
    json.dump(meta, open(os.path.join(d, "sample.json"), "w"))


def test_single_view(tmp_path):
    raw = str(tmp_path)
    _make_job(raw, "job_0", [0])
    got = list(iter_samples(raw))
    assert len(got) == 1
    key, before, after, rec = got[0]
    assert key == "job_0_move_v0"                 # key 里带算子短名
    assert os.path.exists(before) and os.path.exists(after)
    assert rec["instruction"] == "move the cube left"
    assert rec["view"] == 0
    assert "views" not in rec                    # 冗余文件清单已剔除
    assert rec["edit"]["validity"]["quality"]["sharpness"] == 40   # 富 metadata 保留


def test_multi_view_becomes_multiple_samples(tmp_path):
    raw = str(tmp_path)
    _make_job(raw, "job_0", [0, 1])
    keys = [k for k, *_ in iter_samples(raw)]
    assert keys == ["job_0_move_v0", "job_0_move_v1"]


def test_missing_image_skipped(tmp_path):
    raw = str(tmp_path)
    _make_job(raw, "job_0", [0])
    os.remove(os.path.join(raw, "job_0", "after_v0.png"))   # 删掉一张 → 该样本跳过
    assert list(iter_samples(raw)) == []


def test_multiple_jobs_sorted(tmp_path):
    raw = str(tmp_path)
    _make_job(raw, "job_b", [0])
    _make_job(raw, "job_a", [0])
    keys = [k for k, *_ in iter_samples(raw)]
    assert keys == ["job_a_move_v0", "job_b_move_v0"]   # 按 job 目录排序，可复现


def test_pack_mapping_and_backfill(tmp_path):
    import pytest
    pytest.importorskip("webdataset")
    from datagen.orchestrator.collector import pack, backfill_shard_paths
    from datagen.orchestrator.db import get_engine, init_db, make_session, Sample
    from datagen.orchestrator.ingest import ingest_samples

    raw = str(tmp_path / "raw")
    _make_job(raw, "job_0", [0, 1])
    mapping = pack(raw, str(tmp_path / "shards"), shard_size=1)   # 1/分片 → 两个分片
    assert set(mapping) == {"job_0_move_v0", "job_0_move_v1"}
    assert mapping["job_0_move_v0"] != mapping["job_0_move_v1"]  # 落在不同分片

    dburl = f"sqlite:///{tmp_path}/t.db"
    engine = get_engine(dburl)
    init_db(engine)
    ingest_samples(engine, raw)                                 # 先建样本行
    assert backfill_shard_paths(dburl, mapping) == 2
    with make_session(engine)() as s:
        assert s.get(Sample, "job_0_move_v0").shard_path == mapping["job_0_move_v0"]
