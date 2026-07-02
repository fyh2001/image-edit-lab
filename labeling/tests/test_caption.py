"""captioner 纯 Python 单测（不依赖 Blender / VLM / 网络）。"""
import copy

import numpy as np
import pytest

from labeling.caption import styles, facts, verify
from labeling.caption.providers import StubProvider, get_provider
from labeling.caption.task import caption_sample


# ---- 样例 sample.json（各算子最小可用子集，字段对齐真实产出）----
S_MOVE = {"instruction": "move the nearest toilet onto the floor",
          "edit": {"op": "object_move", "noun": "toilet",
                   "semantic_direction": ["left", "closer"], "placement_mode": "support_surface"}}
S_ROT = {"instruction": "rotate the painting",
         "edit": {"op": "object_rotate", "noun": "painting", "axis": "Z", "degrees": -170,
                  "view_change": {"kind": "opposite_side", "about": "vertical"}}}
S_SCALE = {"instruction": "shrink the nearest picture",
           "edit": {"op": "object_scale", "noun": "picture", "factor": 0.67}}
S_DEL = {"instruction": "remove the nearest table",
         "edit": {"op": "object_delete", "noun": "table"}}
S_ADD = {"instruction": "add a casserole on top of the couch",
         "edit": {"op": "object_add", "noun": "casserole"}}
S_REP = {"instruction": "change the bowl into a solar array",
         "edit": {"op": "object_replace", "from": {"category": "bowl"}, "to": {"category": "solar array"}}}
ALL = [S_MOVE, S_ROT, S_SCALE, S_DEL, S_ADD, S_REP]


# ---------------- styles ----------------
def test_style_deficit_sampling_covers_all():
    rng = np.random.default_rng(0)
    counts = {}
    for _ in range(500):
        s = styles.sample_style(rng, counts, op="object_delete")
        counts[s] = counts.get(s, 0) + 1        # 调用方负责累加（同 caption_sample）
    # delete 允许全部 5 风格，亏空采样应让每种都拿到可观份额（无 0）。
    assert set(counts) == set(styles.STYLES)
    assert min(counts.values()) > 30


def test_op_style_block_respected():
    rng = np.random.default_rng(1)
    counts = {}
    for _ in range(200):
        s = styles.sample_style(rng, counts, op="object_scale")
        assert s != "goal"                      # scale 禁 goal


def test_sample_style_deterministic():
    a = [styles.sample_style(np.random.default_rng(7), {}, op="object_move") for _ in range(3)]
    b = [styles.sample_style(np.random.default_rng(7), {}, op="object_move") for _ in range(3)]
    assert a == b


# ---------------- facts ----------------
def test_facts_reference_and_direction():
    f = facts.extract_facts(S_MOVE)
    assert f["op"] == "object_move"
    assert "toilet" in f["reference"]
    assert f["direction"] == ["left", "closer"]


def test_facts_replace_nouns():
    f = facts.extract_facts(S_REP)
    assert f["from_noun"] == "bowl" and f["to_noun"] == "solar array"


def test_facts_scale_dir():
    assert facts.extract_facts(S_SCALE)["scale_dir"] == "smaller"


def test_facts_rotate_view_change():
    assert facts.extract_facts(S_ROT)["view_change"]["kind"] == "opposite_side"


# ---------------- verify ----------------
def test_verify_catches_reversed_direction():
    f = facts.extract_facts(S_MOVE)             # 应向左
    ok, reasons = verify.check_consistency("Move the toilet to the right.", f)
    assert not ok and any("左" in r or "右" in r for r in reasons)


def test_verify_catches_reversed_scale():
    f = facts.extract_facts(S_SCALE)            # 应缩小
    ok, _ = verify.check_consistency("Make the picture much bigger.", f)
    assert not ok


def test_verify_catches_wrong_op_verb():
    f = facts.extract_facts(S_DEL)              # 应删除
    ok, _ = verify.check_consistency("Add a table here.", f)
    assert not ok


def test_verify_passes_consistent():
    f = facts.extract_facts(S_DEL)
    ok, _ = verify.check_consistency("I don't want to see the table.", f)
    assert ok


# ---------------- stub provider end-to-end ----------------
def test_stub_all_ops_all_styles_consistent():
    stub = StubProvider()
    rng = np.random.default_rng(0)
    differ = 0
    total = 0
    for sample in ALL:
        f = facts.extract_facts(sample)
        for style in styles.allowed_styles(f["op"]):
            cap = stub.caption(f, style, rng=rng)
            assert isinstance(cap, str) and len(cap) > 3
            ok, reasons = verify.check_consistency(cap, f)
            assert ok, f"{f['op']}/{style}: {cap!r} -> {reasons}"
            total += 1
            differ += int(cap.strip().lower() != (f["base_instruction"] or "").strip().lower())
    # 防回归：stub 不能全部退回基线（曾因方法名分发 bug 导致 caption==base）。
    assert differ >= total * 0.7, f"stub 产出与基线雷同过多 ({differ}/{total})"


def test_caption_sample_writes_back_and_verifies():
    stub = StubProvider()
    rng = np.random.default_rng(0)
    counts = {}
    sample = copy.deepcopy(S_MOVE)
    cap, style, ok = caption_sample(sample, stub, rng, counts)
    assert ok and cap
    assert sample["caption"] == cap
    assert sample["caption_style"] == style
    assert sample["caption_meta"]["provider"] == "stub"
    assert counts[style] == 1


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError):
        get_provider("nope")


def test_fallback_when_provider_always_contradicts():
    class BadProvider(StubProvider):
        name = "bad"
        def caption(self, facts, style, images=None, rng=None):
            return "Add a brand new object here."     # 对 delete 永远矛盾
    rng = np.random.default_rng(0)
    counts = {}
    sample = copy.deepcopy(S_DEL)
    cap, style, ok = caption_sample(sample, BadProvider(), rng, counts, max_retries=2)
    assert not ok
    assert sample["caption_meta"]["provider"] == "fallback_base"
    assert cap == S_DEL["instruction"]                 # 回退到基线模板
