"""给**真 VLM** 后端组 prompt（Stub 用不到；QwenVL/OpenAI provider 接入时用）。

原则：VLM 看 before/after 图，把客观事实说成**一句**该风格的自然指令；可看图补 front/back
之类命名，但**不准与事实矛盾**（方向/算子/放大缩小）。这样既自然又不毒化训练。
"""
from __future__ import annotations

_STYLE_HINT = {
    "direct":  "a short imperative command",
    "spatial": "grounded in where things are (use landmarks / support surfaces)",
    "intent":  "phrased as a wish or preference (\"I don't want...\", \"I'd like...\")",
    "goal":    "phrased as a desired outcome (\"clear the table\", \"make it tidy\")",
    "casual":  "loose, conversational, like texting a friend",
}

_OP_HINT = {
    "object_delete":  "The subject is removed. Do NOT ask to add anything.",
    "object_add":     "A new object is added. Ground it on its support surface.",
    "object_move":    "The subject moves. Keep the direction consistent with the facts.",
    "object_scale":   "The subject is resized. Keep bigger/smaller consistent with the facts.",
    "object_rotate":  "The subject is rotated. You MAY name which face shows (front/back/side) "
                      "from the images, but keep it consistent with view_change.",
    "object_replace": "The subject is replaced by a different object.",
}


def build_messages(facts, style, language="zh", images=None):
    """返回 chat messages（system + user(text+images)），交给具体 provider 的 SDK 调用。"""
    sys = (
        "You write a single natural image-editing instruction. You are given a BEFORE and AFTER "
        "image plus objective facts about the one edit between them. Output ONE sentence only, "
        "no explanation. Never contradict the facts (subject, operation, direction, bigger/smaller, "
        "rotation degrees & clockwise/counterclockwise, scale factor)."
    )
    lang_hint = "用中文" if language == "zh" else "in English"
    lines = [
        f"Edit facts: {facts}",
        f"Language: write {lang_hint}.",
        f"Style: write {_STYLE_HINT.get(style, 'a natural instruction')}.",
        f"Operation note: {_OP_HINT.get(facts.get('op'), '')}",
        "Refer to the subject unambiguously. If a numeric magnitude is given AND flagged round "
        "(factor_round / angle_round), you may quote it (e.g. 1.5×, 90°); otherwise stay qualitative. "
        "Output only the instruction sentence.",
    ]
    user_content = [{"type": "text", "text": "\n".join(lines)}]
    for role in ("before", "after"):
        p = (images or {}).get(role)
        if p:
            user_content.append({"type": "image", "path": p, "label": role})
    return [{"role": "system", "content": sys}, {"role": "user", "content": user_content}]
