"""caption **一致性校验**（纯逻辑，**双语 zh/en**）：生成的自然指令不能与客观事实矛盾。

只抓**硬矛盾**（说反方向、算子说错、放大说成缩小）——这些会毒化训练。措辞/风格/front-back
命名这类主观的**不判**（front/back 交给看图的 VLM）。校验不过 → 让 captioner 重生成/丢弃。
返回 (ok: bool, reasons: list[str])。中文无词边界，故 zh 词用多字模式降低误判。
"""
from __future__ import annotations
import re

# 每个算子期望出现的"动词族"，以及**绝不能**出现的对立动词族（含中文）。
_OP_VERBS = {
    "object_delete":  (r"\b(remove|delete|erase|get rid of|take out|clear)\b|删|移除|去掉|弄走|清掉",
                       r"\b(add|insert|place a|put a)\b|添加|加一个|加个|放一个|放个|新增"),
    "object_add":     (r"\b(add|insert|place|put|drop|set)\b|添加|加一个|加个|放一个|放个|摆",
                       r"\b(remove|delete|erase|get rid of)\b|删掉|移除|去掉|弄走"),
    "object_move":    (r"\b(move|shift|put|place|relocate|slide|onto|to the)\b|移到|挪|放到|移动",
                       r"\b(delete|rotate|resize)\b|删掉|旋转|缩放"),
    "object_scale":   (r"\b(bigger|smaller|larger|enlarge|shrink|resize|scale|size|grow)\b|放大|缩小|变大|变小|调大|调小",
                       None),
    "object_rotate":  (r"\b(rotate|turn|spin|face|facing|side|back|around|angle)\b|旋转|转|顺时针|逆时针|侧面|背面|角度",
                       r"\b(delete|remove|resize)\b|删掉|移除|缩放"),
    "object_replace": (r"\b(replace|swap|change .* (into|with|for)|instead of|turn .* into)\b|换成|替换|变成",
                       r"\b(delete|remove)\b|删掉|移除"),
}

_LEFT = re.compile(r"\bleft\b|左", re.I)
_RIGHT = re.compile(r"\bright\b|右", re.I)
_UP = re.compile(r"\b(up|higher|raise|above)\b|往上|向上|上方|抬高", re.I)
_DOWN = re.compile(r"\b(down|lower|below)\b|往下|向下|下方|放低", re.I)
_CLOSER = re.compile(r"\b(closer|nearer|toward|forward)\b|靠近|往前|向前|近处", re.I)
_FARTHER = re.compile(r"\b(farther|further|away|behind)\b|远处|后退|往后|向后", re.I)

_BIG = re.compile(r"\b(bigger|larger|enlarge|grow|scale up)\b|放大|变大|调大", re.I)
_SMALL = re.compile(r"\b(smaller|shrink|scale down|tinier)\b|缩小|变小|调小", re.I)


def _contradict(text, want_re, opp_re):
    """text 提到对立方向、且没提到期望方向 → 矛盾。"""
    return bool(opp_re.search(text)) and not bool(want_re.search(text))


def check_consistency(caption, facts):
    cap = (caption or "").strip()
    reasons = []
    if len(cap) < 2:
        return False, ["empty caption"]

    low = cap.lower()
    op = facts.get("op")

    # 1) 算子动词族：出现对立动词 = 硬错。
    verbs = _OP_VERBS.get(op)
    if verbs:
        _want, forbid = verbs
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
        big, small = _BIG.search(low), _SMALL.search(low)
        if sd == "bigger" and small and not big:
            reasons.append("scale: 应放大却说缩小")
        if sd == "smaller" and big and not small:
            reasons.append("scale: 应缩小却说放大")

    # 4) rotate 顺逆：有相机相对 turn_direction 时不能说反。
    if op == "object_rotate":
        turn = facts.get("turn_direction")
        cw = re.search(r"\bclockwise\b|顺时针", low)
        ccw = re.search(r"\bcounter[- ]?clockwise\b|逆时针", low)
        if turn == "clockwise" and ccw and not cw:
            reasons.append("rotate: 应顺时针却说逆时针")
        if turn == "counterclockwise" and cw and not ccw:
            reasons.append("rotate: 应逆时针却说顺时针")

    # 5) replace：不能把新物体说成被删掉。
    if op == "object_replace":
        to = (facts.get("to_noun") or "").lower()
        if to and re.search(r"(remove|delete|get rid of|删掉|移除).*" + re.escape(to), low):
            reasons.append("replace: 把新物体说成被删掉")

    return (len(reasons) == 0), reasons
