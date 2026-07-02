"""caption **一致性校验**（纯逻辑）：生成的自然指令不能与客观事实矛盾。

只抓**硬矛盾**（说反方向、算子说错、放大说成缩小）——这些会毒化训练。措辞/风格/front-back
命名这类主观的**不判**（front/back 交给看图的 VLM）。校验不过 → 让 captioner 重生成/丢弃。
返回 (ok: bool, reasons: list[str])。
"""
from __future__ import annotations
import re

# 每个算子期望出现的"动词族"，以及**绝不能**出现的对立动词族。
_OP_VERBS = {
    "object_delete":  (r"\b(remove|delete|erase|get rid of|take out|clear|gone|without)\b",
                       r"\b(add|insert|place a|put a)\b"),
    "object_add":     (r"\b(add|insert|place|put|drop|set)\b",
                       r"\b(remove|delete|erase|get rid of)\b"),
    "object_move":    (r"\b(move|shift|put|place|relocate|slide|onto|to the)\b",
                       r"\b(delete|remove|rotate|resize)\b"),
    "object_scale":   (r"\b(bigger|smaller|larger|enlarge|shrink|resize|scale|size|grow)\b",
                       None),
    "object_rotate":  (r"\b(rotate|turn|spin|face|facing|side|back|around|angle)\b",
                       r"\b(delete|remove|resize|move it)\b"),
    "object_replace": (r"\b(replace|swap|change .* (into|with|for)|instead of|turn .* into)\b",
                       r"\b(delete|remove|rotate)\b"),
}

_LEFT = re.compile(r"\bleft\b", re.I)
_RIGHT = re.compile(r"\bright\b", re.I)
_UP = re.compile(r"\b(up|higher|raise|above)\b", re.I)
_DOWN = re.compile(r"\b(down|lower|below|drop)\b", re.I)
_CLOSER = re.compile(r"\b(closer|nearer|toward|forward|front)\b", re.I)
_FARTHER = re.compile(r"\b(farther|further|back|away|behind)\b", re.I)


def _contradict(text, want_re, opp_re):
    """text 提到对立方向、且没提到期望方向 → 矛盾。"""
    return bool(opp_re.search(text)) and not bool(want_re.search(text))


def check_consistency(caption, facts):
    cap = (caption or "").strip()
    reasons = []
    if len(cap) < 3:
        return False, ["empty caption"]

    low = cap.lower()
    op = facts.get("op")

    # 1) 算子动词族：出现对立动词 = 硬错。
    verbs = _OP_VERBS.get(op)
    if verbs:
        want, forbid = verbs
        if forbid and re.search(forbid, low):
            reasons.append(f"{op}: caption 用了对立动词")

    # 2) move 方向：语义方向词说反了。
    if op == "object_move":
        dirs = set(facts.get("direction") or [])
        if "left" in dirs and _contradict(low, _LEFT, _RIGHT):
            reasons.append("move: 应向左却说右")
        if "right" in dirs and _contradict(low, _RIGHT, _LEFT):
            reasons.append("move: 应向右却说左")
        if "up" in dirs and _contradict(low, _UP, _DOWN):
            reasons.append("move: 应向上却说下")
        if "down" in dirs and _contradict(low, _DOWN, _UP):
            reasons.append("move: 应向下却说上")
        if "closer" in dirs and _contradict(low, _CLOSER, _FARTHER):
            reasons.append("move: 应更近却说远")
        if "farther" in dirs and _contradict(low, _FARTHER, _CLOSER):
            reasons.append("move: 应更远却说近")

    # 3) scale 方向：放大/缩小说反。
    if op == "object_scale":
        sd = facts.get("scale_dir")
        big = re.search(r"\b(bigger|larger|enlarge|grow|scale up)\b", low)
        small = re.search(r"\b(smaller|shrink|scale down|tinier)\b", low)
        if sd == "bigger" and small and not big:
            reasons.append("scale: 应放大却说缩小")
        if sd == "smaller" and big and not small:
            reasons.append("scale: 应缩小却说放大")

    # 4) replace：不能把新旧物体名说反（把 to 当成被替换掉的）。
    if op == "object_replace":
        to = (facts.get("to_noun") or "").lower()
        if to and re.search(r"\b(remove|delete|get rid of)\b.*" + re.escape(to), low):
            reasons.append("replace: 把新物体说成被删掉")

    return (len(reasons) == 0), reasons
