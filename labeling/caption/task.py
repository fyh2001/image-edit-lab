"""captioner 算子：注册为 `@register_task("vlm_caption")`，在 render→**vlm_caption**→pack 里编排。

reduce/sink 型（和 pack_parquet 一样遍历 raw_dir，逐个 sample.json 改写），所以用 run_one 跑一次。
每个样本：抽事实 → 亏空采样一种风格 → provider 出一条 → 一致性校验（不过就重试/回退基线）→
写回 sample.json 的 `caption` / `caption_style` / `caption_meta`。**不重渲、随时可重标。**
"""
from __future__ import annotations
import glob
import json
import os

import numpy as np

from common.ray_exec import register_task
from labeling.caption import facts as _facts
from labeling.caption import styles as _styles
from labeling.caption import verify as _verify
from labeling.caption.providers import get_provider


def caption_sample(sample, provider, rng, counts, lang_counts=None, weights=None,
                   lang_weights=None, max_retries=3, images=None):
    """给单个 sample dict 生成一条 caption（就地写回 sample）。返回 (caption, style, lang, ok)。

    counts: {style: 已产条数}；lang_counts: {lang: 已产条数}——都在成功后 +1（跨样本亏空均衡）。
    """
    f = _facts.extract_facts(sample)
    op = f.get("op")
    style = _styles.sample_style(rng, counts, op=op, weights=weights)
    lang = _styles.sample_language(rng, lang_counts if lang_counts is not None else {},
                                   weights=lang_weights)

    caption, ok, reasons = None, False, []
    for _ in range(max(1, int(max_retries))):
        cap = provider.caption(f, style, language=lang, images=images, rng=rng)
        ok, reasons = _verify.check_consistency(cap, f)
        caption = cap
        if ok:
            break

    source = provider.name
    if not ok:                                   # 反复不过 → 回退基线模板指令（保证不出矛盾标签）
        caption = f.get("base_instruction") or caption
        style, lang, source = "direct", "en", "fallback_base"   # 基线模板是英文

    counts[style] = counts.get(style, 0) + 1
    if lang_counts is not None:
        lang_counts[lang] = lang_counts.get(lang, 0) + 1
    sample["caption"] = caption
    sample["caption_style"] = style
    sample["caption_lang"] = lang
    sample["caption_meta"] = {"provider": source, "verified": bool(ok),
                              "reasons": reasons, "base_instruction": f.get("base_instruction")}
    return caption, style, lang, ok


@register_task("vlm_caption")
def vlm_caption(payload):
    """payload: {raw_dir, provider?='stub', provider_params?, style_weights?, lang_weights?,
                 seed?=0, max_retries?=3, use_images?=False}
    遍历 raw_dir/*/sample.json，逐个改写并落盘。返回 {raw_dir, n, verified, fallback, by_style, by_lang}。
    """
    raw_dir = payload["raw_dir"]
    provider = get_provider(payload.get("provider", "stub"), **(payload.get("provider_params") or {}))
    weights = payload.get("style_weights")
    lang_weights = payload.get("lang_weights")
    max_retries = int(payload.get("max_retries", 3))
    use_images = bool(payload.get("use_images", False))
    rng = np.random.default_rng(int(payload.get("seed", 0)))

    counts, lang_counts, by_style, by_lang = {}, {}, {}, {}
    n = verified = fallback = 0
    for sj in sorted(glob.glob(os.path.join(raw_dir, "*", "sample.json"))):
        try:
            sample = json.load(open(sj))
        except Exception as e:
            print(f"[vlm_caption] 跳过无法读取的 {sj}: {e}")
            continue
        images = None
        if use_images:
            d = os.path.dirname(sj)
            b = sorted(glob.glob(os.path.join(d, "before_*.png")))
            a = sorted(glob.glob(os.path.join(d, "after_*.png")))
            images = {"before": b[0] if b else None, "after": a[0] if a else None}

        _cap, style, lang, ok = caption_sample(
            sample, provider, rng, counts, lang_counts=lang_counts, weights=weights,
            lang_weights=lang_weights, max_retries=max_retries, images=images)
        json.dump(sample, open(sj, "w"), ensure_ascii=False, indent=1)
        n += 1
        verified += int(ok)
        fallback += int(not ok)
        by_style[style] = by_style.get(style, 0) + 1
        by_lang[lang] = by_lang.get(lang, 0) + 1

    print(f"[vlm_caption] {raw_dir} | {n} 样本，校验通过 {verified}，回退 {fallback} | "
          f"风格 {by_style} | 语言 {by_lang}")
    return {"raw_dir": raw_dir, "n": n, "verified": verified, "fallback": fallback,
            "by_style": by_style, "by_lang": by_lang}
