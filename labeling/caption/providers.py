"""VLM 后端**可插拔**。

现在提供 **StubProvider**（规则/模板版，不联网、确定性）——先把 render→vlm_caption→pack
整条 pipeline + 单测跑通；等有真数据再实现 QwenVLProvider / OpenAIProvider（看 before/after 图
生成更自然、能补 front/back 命名的 caption）。真 provider 只需实现同一个 `caption()` 接口。

接口：`caption(facts, style, images=None, rng=None) -> str`
  facts:  facts.extract_facts(sample) 的产出（客观事实）
  style:  styles.STYLES 之一
  images: {"before": path, "after": path}（Stub 不看图；真 VLM 看）
"""
from __future__ import annotations


class BaseProvider:
    name = "base"

    def caption(self, facts, style, images=None, rng=None):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# StubProvider：从事实 + 风格拼一句自然指令。纯字符串逻辑，给定 rng 确定性。
# ---------------------------------------------------------------------------
def _pick(rng, options):
    if rng is None:
        return options[0]
    return options[int(rng.integers(len(options)))]


def _ref(facts):
    return facts.get("reference") or ("the " + (facts.get("noun") or "object"))


def _loc(facts, default=""):
    return facts.get("location_phrase") or default


def _view_word(facts):
    kind = (facts.get("view_change") or {}).get("kind")
    return {"opposite_side": "so its other side faces me",
            "side_face": "so I can see it from the side",
            "partial_turn": "a bit",
            "tipped": "onto its side"}.get(kind, "around")


class StubProvider(BaseProvider):
    name = "stub"

    def caption(self, facts, style, images=None, rng=None):
        op = facts.get("op")
        ref = _ref(facts)
        fn = getattr(self, (op or "").replace("object_", "op_"), None)
        if fn is None:
            return facts.get("base_instruction") or f"edit {ref}"
        return fn(facts, style, ref, rng)

    def op_delete(self, f, style, ref, rng):
        return {
            "direct":  _pick(rng, [f"Remove {ref}.", f"Delete {ref}."]),
            "spatial": f"Take {ref} out of the scene.",
            "intent":  _pick(rng, [f"I don't want to see {ref} anymore.",
                                   f"Get rid of {ref}."]),
            "goal":    _pick(rng, ["Clear the clutter here.", "Tidy this up."]),
            "casual":  _pick(rng, [f"just get rid of {ref}", f"lose {ref}"]),
        }.get(style, f"Remove {ref}.")

    def op_add(self, f, style, ref, rng):
        noun = f.get("noun") or "object"
        loc = _loc(f, "here")
        return {
            "direct":  f"Add a {noun} {loc}.".replace("  ", " "),
            "spatial": f"Place a {noun} {loc}.".replace("  ", " "),
            "intent":  f"I'd like a {noun} {loc}.".replace("  ", " "),
            "goal":    f"This spot could use a {noun}.",
            "casual":  f"put a {noun} {loc}".replace("  ", " ").strip(),
        }.get(style, f"Add a {noun} {loc}.")

    def op_move(self, f, style, ref, rng):
        loc = _loc(f)
        dirs = f.get("direction") or []
        dw = dirs[0] if dirs else "over"
        tail = loc or f"to the {dw}"
        return {
            "direct":  f"Move {ref} {tail}.",
            "spatial": f"Move {ref} {tail}.",
            "intent":  f"{ref[0].upper()+ref[1:]} should be {tail}.",
            "goal":    f"Rearrange so {ref} sits {tail}.",
            "casual":  f"shift {ref} {tail}",
        }.get(style, f"Move {ref} {tail}.")

    def op_scale(self, f, style, ref, rng):
        sd = f.get("scale_dir") or "bigger"
        word = "bigger" if sd == "bigger" else "smaller"
        return {
            "direct":  f"Make {ref} {word}.",
            "spatial": f"Resize {ref} to be {word}.",
            "intent":  f"I want {ref} a bit {word}.",
            "casual":  f"make {ref} {word}",
        }.get(style, f"Make {ref} {word}.")

    def op_rotate(self, f, style, ref, rng):
        vw = _view_word(f)
        return {
            "direct":  f"Turn {ref} {vw}.",
            "spatial": f"Rotate {ref} {vw}.",
            "intent":  _pick(rng, [f"I want to see {ref} from another angle.",
                                   f"Turn {ref} {vw}."]),
            "casual":  f"spin {ref} {vw}",
        }.get(style, f"Turn {ref} {vw}.")

    def op_replace(self, f, style, ref, rng):
        to = f.get("to_noun") or "something else"
        frm = f.get("from_noun") or f.get("noun") or "it"
        return {
            "direct":  f"Replace the {frm} with a {to}.",
            "spatial": f"Swap the {frm} for a {to}.",
            "intent":  f"I'd rather have a {to} instead of the {frm}.",
            "goal":    f"Change the {frm} into a {to}.",
            "casual":  f"make the {frm} a {to}",
        }.get(style, f"Replace the {frm} with a {to}.")


# --- 真 VLM 后端占位（等有数据再填；接口同 StubProvider）---
class QwenVLProvider(BaseProvider):
    name = "qwen_vl"

    def __init__(self, **params):
        self.params = params

    def caption(self, facts, style, images=None, rng=None):
        # TODO: 用 labeling.caption.prompt.build_messages(facts, style) + images 调 Qwen-VL，
        # 输出一条自然指令（看图补 front/back）。留到接真模型时实现。
        raise NotImplementedError("QwenVLProvider 待接入真模型；先用 provider='stub' 跑通链路")


_PROVIDERS = {"stub": StubProvider, "qwen_vl": QwenVLProvider}


def get_provider(name="stub", **params):
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"未知 VLM provider: {name}（可选 {list(_PROVIDERS)}）")
    try:
        return cls(**params)
    except TypeError:
        return cls()
