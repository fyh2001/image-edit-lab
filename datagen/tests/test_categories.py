"""assets/categories 纯函数单测（uid→类别/名词，不依赖 Blender）。"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datagen.worker.assets import categories


def test_display_noun_underscore_to_space():
    assert categories.display_noun("coffee_table") == "coffee table"
    assert categories.display_noun("hot-dog") == "hot dog"


def test_display_noun_fallback():
    assert categories.display_noun("") == "object"
    assert categories.display_noun(None) == "object"


def test_display_noun_strips_parentheticals():
    assert categories.display_noun("date_(fruit)") == "date"
    assert categories.display_noun("mouse_(animal)") == "mouse"
    assert categories.display_noun("(only)") == "object"   # 全是括号 → 回退


def test_best_noun_keeps_category_when_echoed():
    # category 在 tags/name 里有呼应 → 信 category（即便 tags 有别的词）
    assert categories.best_noun("dalmatian", "101 Dalmatians Puppy",
                                ["dalmatians", "disney", "3d"]) == "dalmatian"


def test_best_noun_overrides_mislabel_with_clean_tag():
    # 被误标 sweatband，实为射灯；name 非英文 → 用干净 tag
    assert categories.best_noun("sweatband", "Светильник Maytoni",
                                ["3d", "model", "spotlights"]) == "spotlights"


def test_best_noun_skips_junk_tokens():
    # tags 全是工艺/工具噪声 → 跳过，最后回退 category
    assert categories.best_noun("chair", "Render Model",
                                ["3d", "model", "blender", "pbr"]) == "chair"


def test_best_noun_uses_name_when_no_clean_tag():
    assert categories.best_noun("turnip", "Walnut bagel", []) in ("walnut", "bagel")


def test_best_noun_fallback_object():
    assert categories.best_noun("", None, None) == "object"


def test_resolve_description():
    meta = {"u1": {"name": "Cozy Armchair", "tags": ["chair", "wood"], "license": "by"}}
    d = categories.resolve_description("u1", meta)
    assert d["description"] == "Cozy Armchair" and d["license"] == "by"
    assert d["tags"] == ["chair", "wood"]
    miss = categories.resolve_description("nope", meta)
    assert miss["description"] is None and miss["tags"] == []


def test_resolve_noun_hit():
    cmap = {"uid1": "wooden_chair"}
    cat, noun = categories.resolve_noun("uid1", cmap)
    assert cat == "wooden_chair" and noun == "wooden chair"


def test_resolve_noun_miss():
    cat, noun = categories.resolve_noun("unknown", {"uid1": "chair"})
    assert cat == "object" and noun == "object"


def test_load_category_map_missing(tmp_path):
    assert categories.load_category_map(str(tmp_path / "nope.json")) == {}


def test_load_category_map_ok(tmp_path):
    p = tmp_path / "cat.json"
    p.write_text(json.dumps({"a": "lamp", "b": "sofa"}), encoding="utf-8")
    m = categories.load_category_map(str(p))
    assert m == {"a": "lamp", "b": "sofa"}


def test_load_category_map_corrupt(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert categories.load_category_map(str(p)) == {}


def test_category_target_size_uniform_when_no_map():
    # sizes 为空 → 一律 default（tabletop 统一尺寸）
    assert categories.category_target_size("chair", None, default=0.8) == 0.8
    assert categories.category_target_size("anything", {}, default=1.0) == 1.0


def test_category_target_size_lookup_and_fallback():
    sizes = {"chair": 1.0, "cup": 0.1}
    assert categories.category_target_size("chair", sizes, default=0.5) == 1.0
    assert categories.category_target_size("cup", sizes, default=0.5) == 0.1
    assert categories.category_target_size("unknown", sizes, default=0.5) == 0.5


def test_default_category_sizes_populated():
    assert categories.DEFAULT_CATEGORY_SIZES["chair"] == 1.0
    assert categories.DEFAULT_CATEGORY_SIZES["cup"] < 0.2
