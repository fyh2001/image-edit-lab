"""prefetch.sample_diverse_uids 单测（纯函数，不依赖 objaverse）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datagen.orchestrator.prefetch import sample_diverse_uids


def test_covers_multiple_categories():
    lvis = {"a": ["a1", "a2", "a3", "a4"], "b": ["b1", "b2", "b3", "b4"],
            "c": ["c1", "c2", "c3", "c4"]}
    uids, u2c = sample_diverse_uids(lvis, 6, seed=1)
    assert len(uids) == 6
    assert len(set(uids)) == 6                  # 无重复
    assert len(set(u2c.values())) == 3          # 三个类别都被采到（round-robin）


def test_exclude_skips_used():
    lvis = {"a": ["a1", "a2"], "b": ["b1", "b2"]}
    uids, _ = sample_diverse_uids(lvis, 4, exclude={"a1", "b1"}, seed=0)
    assert set(uids) == {"a2", "b2"}


def test_caps_at_available():
    lvis = {"a": ["a1"], "b": ["b1"]}
    uids, _ = sample_diverse_uids(lvis, 10, seed=0)
    assert set(uids) == {"a1", "b1"}


def test_reproducible_with_seed():
    lvis = {"a": ["a1", "a2", "a3"], "b": ["b1", "b2", "b3"]}
    assert sample_diverse_uids(lvis, 4, seed=42)[0] == sample_diverse_uids(lvis, 4, seed=42)[0]


def test_uid2cat_consistent():
    lvis = {"cat_x": ["u1", "u2"], "cat_y": ["u3"]}
    uids, u2c = sample_diverse_uids(lvis, 3, seed=0)
    for u in uids:
        assert u in lvis[u2c[u]]                # 映射回去类别里确实有这个 uid
