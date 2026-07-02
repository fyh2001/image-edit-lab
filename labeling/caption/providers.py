"""VLM 后端**可插拔**。

现在提供 **StubProvider**（规则/模板版，不联网、确定性、**双语 zh/en**）——先把
render→vlm_caption→pack 整条 pipeline + 单测跑通；等有真数据再实现 QwenVLProvider /
OpenAIProvider（看 before/after 图生成更自然、能补 front/back 命名的 caption）。真 provider
只需实现同一个 `caption()` 接口。

接口：`caption(facts, style, language="zh", images=None, rng=None) -> str`
  facts:    facts.extract_facts(sample) 的产出（客观事实）
  style:    styles.STYLES 之一
  language: "zh" | "en"
  images:   {"before": path, "after": path}（Stub 不看图；真 VLM 看）
"""
from __future__ import annotations


class BaseProvider:
    name = "base"

    def caption(self, facts, style, language="zh", images=None, rng=None):
        raise NotImplementedError


def _pick(rng, options):
    if rng is None:
        return options[0]
    return options[int(rng.integers(len(options)))]


def _g(x):
    """数字去尾零：1.5→'1.5'，2.0→'2'。"""
    return f"{float(x):g}"


def _ref(facts, lang="en"):
    """主体指代。en 用消歧短语（the nearest table）；zh 用裸名词——名词多是英文类别 token，
    stub 不翻译、也丢消歧词，真实 zh 命名/消歧由看图的 VLM 重做（stub 只证明链路）。"""
    if lang == "zh":
        return facts.get("noun") or "物体"
    return facts.get("reference") or ("the " + (facts.get("noun") or "object"))


def _loc_en(facts, default=""):
    """英文落位短语（来自基线指令，本就英文）。zh 不复用（会混英文），改用方向词/交给 VLM。"""
    return facts.get("location_phrase") or default


# ---- scale：把倍数事实翻成数值/定性短语（zh/en）----
_BUCKET = {"zh": {"slightly": "稍微", "moderately": "明显", "much": "大幅"},
           "en": {"slightly": "a bit", "moderately": "noticeably", "much": "much"}}


def _scale_phrase(f, language):
    """返回 (grow: bool, phrase)。整齐倍数→数值说法；否则→定性档。"""
    factor = f.get("scale_factor")
    grow = f.get("scale_dir") == "bigger"
    bucket = f.get("scale_bucket") or "moderately"
    if f.get("factor_round") and factor is not None:
        if grow:
            return grow, (f"放大{_g(factor)}倍" if language == "zh"
                          else f"{_g(factor)}× bigger")
        if abs(float(factor) - 0.5) < 0.02:
            return grow, ("缩小到一半" if language == "zh" else "half its size")
        return grow, (f"缩小到原来的{_g(factor)}倍" if language == "zh"
                      else f"{_g(factor)}× its original size")
    b = _BUCKET[language][bucket]
    if language == "zh":
        return grow, (f"{b}放大" if grow else f"{b}缩小")
    return grow, (f"{b} bigger" if grow else f"{b} smaller")


# ---- rotate：把角度/顺逆/视角事实翻成短语（zh/en）----
_VIEW = {"zh": {"opposite_side": "转过来背面对着我", "side_face": "转到侧面给我看",
                "partial_turn": "转一点", "tipped": "翻到侧面"},
         "en": {"opposite_side": "so its back faces me", "side_face": "so I see it from the side",
                "partial_turn": "a little", "tipped": "onto its side"}}


def _rotate_phrase(f, language):
    """整齐角度+相机相对顺逆 → "顺时针转90度"；否则退回视角短语（背面/侧面）。
    返回**不带前导动词**的短语（en 由模板补 Turn/Rotate/spin；zh 短语自带"转"）。"""
    turn = f.get("turn_direction")
    if f.get("angle_round") and turn and f.get("abs_degrees") is not None:
        deg = _g(round(f["abs_degrees"]))
        if language == "zh":
            return ("顺时针" if turn == "clockwise" else "逆时针") + f"转{deg}度"
        return f"{deg}° {turn}"
    kind = (f.get("view_change") or {}).get("kind", "partial_turn")
    return _VIEW[language].get(kind, "转一下" if language == "zh" else "around")


class StubProvider(BaseProvider):
    name = "stub"

    def caption(self, facts, style, language="zh", images=None, rng=None):
        op = facts.get("op")
        fn = getattr(self, (op or "").replace("object_", "op_"), None)
        if fn is None:
            return facts.get("base_instruction") or f"edit {_ref(facts, language)}"
        return fn(facts, style, language, _ref(facts, language), rng)

    def op_delete(self, f, style, lang, ref, rng):
        if lang == "zh":
            return {"direct": f"删掉{ref}", "spatial": f"把{ref}从画面里移除",
                    "intent": _pick(rng, [f"我不想看到{ref}", f"把{ref}弄走"]),
                    "goal": "把这里收拾干净", "casual": f"把{ref}删了吧"}.get(style, f"删掉{ref}")
        return {"direct": f"Remove {ref}.", "spatial": f"Take {ref} out of the scene.",
                "intent": _pick(rng, [f"I don't want to see {ref}.", f"Get rid of {ref}."]),
                "goal": "Clear the clutter here.", "casual": f"just get rid of {ref}"}.get(style, f"Remove {ref}.")

    def op_add(self, f, style, lang, ref, rng):
        noun = f.get("noun") or ("物体" if lang == "zh" else "object")
        if lang == "zh":                                   # zh 落位交给 VLM 看图补，stub 不塞英文短语
            return {"direct": f"加一个{noun}", "spatial": f"在场景里放一个{noun}",
                    "intent": f"我想要一个{noun}", "goal": f"这里可以摆个{noun}",
                    "casual": f"放个{noun}吧"}.get(style, f"加一个{noun}")
        loc = _loc_en(f)
        return {"direct": f"Add a {noun} {loc}.".strip().replace("  ", " "),
                "spatial": f"Place a {noun} {loc}.".strip().replace("  ", " "),
                "intent": f"I'd like a {noun} {loc}.".strip().replace("  ", " "),
                "goal": f"This spot could use a {noun}.",
                "casual": f"put a {noun} {loc}".strip()}.get(style, f"Add a {noun}.")

    def op_move(self, f, style, lang, ref, rng):
        dirs = f.get("direction") or []
        if lang == "zh":                                   # zh 用方向词（英文 location 短语不复用）
            dw = {"left": "左边", "right": "右边", "up": "上面", "down": "下面",
                  "closer": "近处", "farther": "远处"}.get(dirs[0] if dirs else "", "旁边")
            return {"direct": f"把{ref}往{dw}挪", "spatial": f"把{ref}移到{dw}",
                    "intent": f"{ref}该往{dw}点", "goal": f"重新摆一下{ref}",
                    "casual": f"{ref}挪{dw}去"}.get(style, f"把{ref}往{dw}挪")
        loc = _loc_en(f)
        dw_en = dirs[0] if dirs else "over"
        tail = loc or f"to the {dw_en}"
        return {"direct": f"Move {ref} {tail}.", "spatial": f"Move {ref} {tail}.",
                "intent": f"{ref[0].upper()+ref[1:]} should be {tail}.",
                "goal": f"Rearrange so {ref} sits {tail}.",
                "casual": f"shift {ref} {tail}"}.get(style, f"Move {ref} {tail}.")

    def op_scale(self, f, style, lang, ref, rng):
        _grow, ph = _scale_phrase(f, lang)
        if lang == "zh":
            return {"direct": f"把{ref}{ph}", "spatial": f"把{ref}{ph}",
                    "intent": f"我想把{ref}{ph}", "casual": f"{ref}{ph}"}.get(style, f"把{ref}{ph}")
        return {"direct": f"Make {ref} {ph}.", "spatial": f"Resize {ref} {ph}.",
                "intent": f"I want {ref} {ph}.", "casual": f"make {ref} {ph}"}.get(style, f"Make {ref} {ph}.")

    def op_rotate(self, f, style, lang, ref, rng):
        ph = _rotate_phrase(f, lang)                        # zh 自带"转"，en 不带动词
        if lang == "zh":
            return {"direct": f"把{ref}{ph}", "spatial": f"把{ref}{ph}",
                    "intent": _pick(rng, [f"我想从另一个角度看{ref}", f"把{ref}{ph}"]),
                    "casual": f"{ref}{ph}"}.get(style, f"把{ref}{ph}")
        return {"direct": f"Turn {ref} {ph}.", "spatial": f"Rotate {ref} {ph}.",
                "intent": _pick(rng, [f"I want to see {ref} from another angle.", f"Turn {ref} {ph}."]),
                "casual": f"spin {ref} {ph}"}.get(style, f"Turn {ref} {ph}.")

    def op_replace(self, f, style, lang, ref, rng):
        to = f.get("to_noun") or ("别的东西" if lang == "zh" else "something else")
        frm = f.get("from_noun") or f.get("noun") or ("它" if lang == "zh" else "it")
        if lang == "zh":
            return {"direct": f"把{frm}换成{to}", "spatial": f"用{to}替换掉{frm}",
                    "intent": f"我不太喜欢{frm}，换成{to}吧", "goal": f"把{frm}变成{to}",
                    "casual": f"{frm}换成{to}"}.get(style, f"把{frm}换成{to}")
        return {"direct": f"Replace the {frm} with a {to}.", "spatial": f"Swap the {frm} for a {to}.",
                "intent": f"I'd rather have a {to} instead of the {frm}.",
                "goal": f"Change the {frm} into a {to}.",
                "casual": f"make the {frm} a {to}"}.get(style, f"Replace the {frm} with a {to}.")


# --- 真 VLM 后端占位（等有数据再填；接口同 StubProvider）---
class QwenVLProvider(BaseProvider):
    name = "qwen_vl"

    def __init__(self, **params):
        self.params = params

    def caption(self, facts, style, language="zh", images=None, rng=None):
        # TODO: 用 labeling.caption.prompt.build_messages(facts, style, language, images) 调 Qwen-VL，
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
