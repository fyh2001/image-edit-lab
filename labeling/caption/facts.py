"""从 datagen 产出的 sample.json 里抽**客观事实**，给 caption 生成 + 一致性校验共用（纯逻辑）。

分工（见 CAPTION_DESIGN.md）：worker 侧已把"哪个物体/什么操作/方向/视角变化/支撑关系/消歧短语"
写进 metadata，这里只做**归一化**（不碰 Blender、不读图），产出一个紧凑 fact dict：
  op / noun / reference / direction / placement / support_phrase /
  view_change / degrees / scale_dir / scale_factor / from_noun / to_noun / base_instruction
"""
from __future__ import annotations
import re


def _ref_phrase(base_instruction, noun):
    """从基线模板指令里抠出主体的**消歧指代短语**（如 "the chair on the left" / "the nearest table"）。

    worker 已把消歧词烘进基线指令字符串，这里回捞出来复用；捞不到就退回 "the <noun>"。
    """
    if base_instruction and noun:
        m = re.search(r"(the\s+[^,.;]*?\b" + re.escape(noun) + r"\b(?:\s+on\s+the\s+(?:left|right))?)",
                      base_instruction, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return "the " + (noun or "object")


def _location_tail(base_instruction):
    """从基线指令里抠出落位/目标短语（"... on top of the couch" / "... onto the floor"）。"""
    if not base_instruction:
        return None
    m = re.search(r"\b((?:on top of|onto|into|in the|on the|next to|near|to the)\s+[^,.;]+)$",
                  base_instruction.strip(), re.IGNORECASE)
    return m.group(1).strip() if m else None


def extract_facts(sample):
    """sample: 解析后的 sample.json dict → 归一化 fact dict。对缺字段宽容（用 .get）。"""
    edit = sample.get("edit") or {}
    op = edit.get("op")
    base = sample.get("instruction") or ""
    noun = edit.get("noun")

    f = {
        "op": op,
        "noun": noun,
        "base_instruction": base,
        "reference": _ref_phrase(base, noun),
        "location_phrase": _location_tail(base),
    }

    if op == "object_move":
        f["direction"] = list(edit.get("semantic_direction") or [])
        f["placement"] = edit.get("placement_mode")
    elif op == "object_rotate":
        f["view_change"] = edit.get("view_change") or {}
        f["degrees"] = edit.get("degrees")
        f["axis"] = edit.get("axis")
    elif op == "object_scale":
        factor = edit.get("factor")
        f["scale_factor"] = factor
        if factor is not None:
            f["scale_dir"] = "bigger" if float(factor) > 1.0 else "smaller"
    elif op == "object_replace":
        frm = edit.get("from") or {}
        to = edit.get("to") or {}
        f["from_noun"] = frm.get("category")
        f["to_noun"] = to.get("category")
        f["noun"] = f["noun"] or frm.get("category")
        f["reference"] = _ref_phrase(base, f["noun"])
    # add / delete：noun + location_phrase 已够

    return f
