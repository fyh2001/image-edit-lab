"""
Objaverse uid → 类别/名词 解析。纯 Python（不依赖 Blender），可单测。

约定（与 primitives_provider 一致）：
- `category`：规范类别串，直接用 LVIS 标注（如 "coffee_table"），用于 metadata / 去重 / 统计。
- `noun`    ：给指令用的人类可读名词（下划线转空格，如 "coffee table"），让
              "move the {noun}" 读起来自然。
类别映射文件由 orchestrator/prefetch.py 产出：{uid: category}（JSON）。
"""
from __future__ import annotations
import os
import json
from typing import Dict, Tuple

FALLBACK = "object"


def load_category_map(path: str) -> Dict[str, str]:
    """读 {uid: category} 映射；文件不存在或损坏时返回空 dict（不致命）。"""
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def display_noun(category: str) -> str:
    """规范类别 → 指令名词：去掉 LVIS 括号注释、下划线/连字符转空格，空值回退 'object'。

    例： "date_(fruit)" → "date"； "wooden_chair" → "wooden chair"。
    """
    if not category:
        return FALLBACK
    import re
    noun = re.sub(r"\(.*?\)", "", str(category))      # 去掉 "(fruit)" 这类消歧注释
    noun = noun.replace("_", " ").replace("-", " ")
    noun = re.sub(r"\s+", " ", noun).strip()
    return noun or FALLBACK


def resolve_noun(uid: str, category_map: Dict[str, str]) -> Tuple[str, str]:
    """uid → (category, noun)。映射里没有就回退 ('object', 'object')。"""
    category = (category_map or {}).get(uid) or FALLBACK
    return category, display_noun(category)


# 3D 资产标签里的常见噪声（工具/工艺/泛词），选名词时跳过
_JUNK_TOKENS = {
    "3d", "model", "models", "scan", "3dscan", "scaniverse", "photogrammetry", "trnio",
    "lowpoly", "low", "high", "poly", "gameready", "game", "ready", "pbr", "textured",
    "texture", "blender", "maya", "zbrush", "substance", "render", "cg", "asset", "free",
    "download", "sketchfab", "mesh", "uv", "rigged", "animated", "realistic", "handpainted",
    "hand", "painted", "art", "decor", "decoration", "design", "vintage", "modern", "antique",
    "old", "new", "set", "collection", "prop", "object", "thing", "food", "animal", "furniture",
    "the", "and", "with", "for", "by",
}


def _tokens(text):
    import re
    return [t for t in re.split(r"[^a-zA-Z]+", str(text or "").lower()) if t]


def _good_noun_token(t):
    return t.isalpha() and len(t) >= 3 and t not in _JUNK_TOKENS


def best_noun(category: str, name=None, tags=None) -> str:
    """从 (category, name, tags) 里挑最可能正确的"物体名词"。

    保守策略：LVIS category 若在 name/tags 里有呼应 → 用 category（多半是对的）；
    否则 category 很可能是误标 → 退而用 tags/name 里第一个干净名词；都没有再回退 category。
    纯启发式（LLM 会更好），但对"被标 sweatband 实为 spotlight"这类明显错标能纠正。
    """
    cat_noun = display_noun(category)
    bag = set(_tokens(name)) | set(t for tag in (tags or []) for t in _tokens(tag))
    cat_toks = set(_tokens(cat_noun))

    def _echo(ct, bt):
        # 完全相同，或两者都够长(≥4)的子串包含（单复数/词根近似）；避免 "d"⊂"sweatband" 误命中
        return ct == bt or (len(ct) >= 4 and len(bt) >= 4 and (ct in bt or bt in ct))
    echoed = any(_echo(ct, bt) for ct in cat_toks for bt in bag)
    if cat_noun != FALLBACK and cat_toks and echoed:
        return cat_noun
    for tag in (tags or []):                              # 否则优先干净的 tag
        for t in _tokens(tag):
            if _good_noun_token(t):
                return t
    for t in _tokens(name):                              # 再退而用 name 里的词
        if _good_noun_token(t):
            return t
    return cat_noun


def load_meta_map(path: str) -> Dict[str, dict]:
    """读 {uid: {category,name,tags,license}} 富标注（由 prefetch 产出）。同 load_category_map。"""
    m = load_category_map(path)
    return {k: v for k, v in m.items() if isinstance(v, dict)}


def resolve_description(uid: str, meta_map: Dict[str, dict]) -> dict:
    """uid → {description, tags, license}（物体的人类可读名称/标签/许可，供 metadata）。"""
    m = (meta_map or {}).get(uid) or {}
    return {
        "description": m.get("name"),                  # Sketchfab 原始名称，当描述用
        "tags": list(m.get("tags") or [])[:10],
        "license": m.get("license"),
    }


# 少量常见类别的真实尺度（米，按最长边）。供 room 级场景（front3d）做"按类别归一到真实尺度"。
# tabletop 默认不用它（统一尺寸更好取景）；要用时 provider 传 category_sizes="default"。
# 生产可扩充或外置成 JSON。
DEFAULT_CATEGORY_SIZES = {
    "chair": 1.0, "table": 1.2, "sofa": 2.0, "bed": 2.0, "couch": 2.0,
    "lamp": 0.5, "vase": 0.3, "bottle": 0.25, "cup": 0.1, "mug": 0.1,
    "bowl": 0.2, "plate": 0.25, "book": 0.25, "Bible": 0.25, "laptop": 0.35,
    "keyboard": 0.45, "Band_Aid": 0.07, "apple": 0.08, "banana": 0.2, "ball": 0.22,
}


def category_target_size(category: str, sizes: Dict[str, float], default: float) -> float:
    """类别 → 目标最长边（米）。sizes 为空则一律用 default（tabletop 统一尺寸）。"""
    if not sizes:
        return float(default)
    return float(sizes.get(category, default))
