"""QA 初筛：从一批渲染产物里**自动标出"可能有问题/不合理"的对**，供人工精筛。

两层：
  1) 本模块——**廉价启发式**（像素度量 + metadata 规则），零模型、秒级，给每对打 flag + 严重度；
     产出 flagged.json + 一个只含**可疑对**的 HTML（按严重度排序，标注原因）。
  2) 上层 skill（.claude/skills/qa-review）——让 **agent 看图**做语义判断（物体消失/穿模/悬空/
     换了样/不合理），确认或补充 flag，再交人工。

用法： python -m datagen.orchestrator.qa_screen <raw_dir> [out.html]
启发式规则见 screen_pair()；阈值可按需调。
"""
from __future__ import annotations
import base64
import glob
import io
import json
import math
import os
import sys

import imageio.v2 as iio

from datagen.worker.quality.metrics import mean_luminance, change_ratio, background_diff

# flag: (代码, 中文原因, 严重度 1~3)。严重度越高越可能是坏样本。
SEV = {1: "低", 2: "中", 3: "高"}
FLAG_COLOR = {3: "#e05252", 2: "#c9973f", 1: "#4a90d9"}


def _mag(v):
    return math.sqrt(sum(float(x) ** 2 for x in (v or []))) if v else 0.0


def screen_pair(sample, before, after):
    """对一对 (sample.json, before, after) 跑启发式规则，返回 [(code, reason, sev), ...]。"""
    e = sample.get("edit") or {}
    op = e.get("op")
    v = e.get("validity") or {}
    q = v.get("quality") or {}
    cr = q.get("change_ratio", v.get("pixel_change_ratio"))
    flags = []

    # 1) 变化几乎看不见 → 物体没动/移出画面/消失。阈值取很低(0.0015)：小物 add 虽变化小但通常可见，
    #    别误报；真正"消失"的多半更小、或另有 far_move 命中。
    if cr is not None and float(cr) < 0.0015:
        flags.append(("imperceptible", f"变化几乎看不见(change_ratio={cr})——物体可能移出画面/消失", 3))

    # 2) move 移动过远 → 大概率移出画面或落点离谱
    if op == "object_move":
        m = _mag(e.get("translation_world"))
        if m > 3.0:
            flags.append(("far_move", f"移动距离过大({m:.1f}m)，可能移出画面/落点不合理", 3))
        elif m > 2.0:
            flags.append(("long_move", f"移动距离较大({m:.1f}m)，留意落点是否合理", 1))
        if str(e.get("placement_mode")) in ("ceiling", "wall", "floating"):
            flags.append(("odd_placement", f"落到 {e.get('placement_mode')}(天花板/墙/悬空)，多半不合理", 2))

    # 3) 缩放过猛 → 穿模/太小看不见
    if op == "object_scale":
        f = e.get("factor")
        if f and (float(f) > 2.5 or float(f) < 0.35):
            flags.append(("extreme_scale", f"缩放过猛(×{f})，留意穿模/太小", 2))

    # 4) 删除后有承载物落地 → 看是否自然（显示器躺地上那种）
    if e.get("dropped_supported", 0):
        flags.append(("dropped_objects", f"删除后 {e['dropped_supported']} 个承载物落地，看是否自然", 1))

    # 5) 变化占比过大 → 穿模/撑满/整体错位
    if cr is not None and float(cr) > 0.55:
        flags.append(("huge_change", f"变化占比过大(change_ratio={cr})，可能穿模/撑满/错位", 2))

    # 6) 像素复核（用图重算，抓 metadata 没覆盖的）：编辑区外漂移 + 过暗
    try:
        bd = background_diff(before, after)
        if bd > 2.0:
            flags.append(("bg_drift", f"编辑区外差异偏大({bd:.1f})，对齐可能漂移", 2))
        br = mean_luminance(before)
        if br < 15:
            flags.append(("dark", f"画面偏暗(亮度={br:.0f})，看清费劲", 2))
        # replace/move 后主体没露脸的兜底：整体变化极小已由 imperceptible 覆盖
    except Exception:
        pass

    return flags


def _b64(path, mx=384, q=80):
    im = iio.imread(path)
    from PIL import Image
    pil = Image.fromarray(im).convert("RGB")
    pil.thumbnail((mx, mx))
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=q)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def collect(raw_dir):
    """遍历 raw_dir，返回所有对的 (dir, sample, flags, before_path, after_path)。"""
    out = []
    for sj in sorted(glob.glob(os.path.join(raw_dir, "*", "sample.json"))):
        d = os.path.dirname(sj)
        bp = sorted(glob.glob(os.path.join(d, "before_*.png")))
        ap = sorted(glob.glob(os.path.join(d, "after_*.png")))
        if not bp or not ap:
            continue
        try:
            sample = json.load(open(sj))
            before, after = iio.imread(bp[0]), iio.imread(ap[0])
        except Exception:
            continue
        flags = screen_pair(sample, before, after)
        out.append({"dir": d, "sample": sample, "flags": flags,
                    "before": bp[0], "after": ap[0]})
    return out


def render_report(flagged, title="QA 初筛：可疑对"):
    cards = []
    for it in flagged:
        s = it["sample"]; e = s.get("edit") or {}
        instr = (s.get("caption") or s.get("instruction") or "").replace("<", "&lt;")
        maxsev = max((f[2] for f in it["flags"]), default=1)
        badge = "".join(
            f'<span class="fl" style="background:{FLAG_COLOR[sev]}">{code} · {SEV[sev]}</span>'
            for code, _r, sev in it["flags"])
        reasons = "<br>".join(f"· {r}" for _c, r, _s in it["flags"])
        cards.append(f"""<div class="c" style="border-left:4px solid {FLAG_COLOR[maxsev]}">
          <div class="h"><span class="op">{e.get('op','')[7:]}</span><span class="i">{instr}</span></div>
          <div class="p"><figure><img src="{_b64(it['before'])}"><figcaption>before</figcaption></figure>
            <figure><img src="{_b64(it['after'])}"><figcaption>after</figcaption></figure></div>
          <div class="fls">{badge}</div><div class="rs">{reasons}</div>
          <div class="dir">{os.path.basename(it['dir'])}</div></div>""")
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>
 :root{{color-scheme:dark}} body{{margin:0;background:#111418;color:#e6e8ea;font:14px/1.5 -apple-system,system-ui,'PingFang SC',sans-serif}}
 header{{padding:18px 24px;border-bottom:1px solid #262b31}} h1{{margin:0;font-size:18px}}
 .sub{{color:#8b939c;font-size:13px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:16px;padding:20px 24px}}
 .c{{background:#181c22;border:1px solid #262b31;border-radius:12px;overflow:hidden}}
 .h{{padding:10px 12px;display:flex;gap:8px;align-items:baseline}}
 .op{{background:#5a636e;color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:6px}}
 .i{{font-size:13px}} .p{{display:grid;grid-template-columns:1fr 1fr;gap:2px;background:#0c0e11}}
 figure{{margin:0;position:relative}} figure img{{width:100%;display:block}}
 figcaption{{position:absolute;left:6px;top:6px;background:#000a;color:#fff;font-size:10px;padding:1px 6px;border-radius:4px}}
 .fls{{padding:8px 12px 0;display:flex;flex-wrap:wrap;gap:6px}}
 .fl{{color:#fff;font-size:11px;font-weight:600;padding:1px 8px;border-radius:5px}}
 .rs{{padding:6px 12px;color:#c3c9cf;font-size:12px}} .dir{{padding:0 12px 10px;color:#5f6b76;font-size:11px}}
</style></head><body>
<header><h1>{title} <span class="sub">· 标出 {len(flagged)} 对可疑（人工精筛用）</span></h1></header>
<div class="grid">{''.join(cards)}</div></body></html>"""


def build_review_html(raw_dir, verdicts, out, title="QA 终审：agent 确认的问题对"):
    """从 **agent 视觉判定** verdicts=[{dir, category, note, severity?}] 生成终审 HTML（人工精筛用）。"""
    idx = {os.path.basename(d): d for d in glob.glob(os.path.join(raw_dir, "*")) if os.path.isdir(d)}
    cards = []
    for v in verdicts:
        d = idx.get(v.get("dir"))
        if not d:
            continue
        bp = sorted(glob.glob(os.path.join(d, "before_*.png")))
        ap = sorted(glob.glob(os.path.join(d, "after_*.png")))
        if not bp or not ap:
            continue
        sev = int(v.get("severity", 2))
        col = FLAG_COLOR.get(sev, "#c9973f")
        cat = str(v.get("category", "issue")).replace("<", "&lt;")
        note = str(v.get("note", "")).replace("<", "&lt;")
        cards.append(f"""<div class="c" style="border-left:4px solid {col}">
          <div class="h"><span class="fl" style="background:{col}">{cat}</span></div>
          <div class="p"><figure><img src="{_b64(bp[0])}"><figcaption>before</figcaption></figure>
            <figure><img src="{_b64(ap[0])}"><figcaption>after</figcaption></figure></div>
          <div class="rs">{note}</div><div class="dir">{v.get('dir')}</div></div>""")
    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>:root{{color-scheme:dark}} body{{margin:0;background:#111418;color:#e6e8ea;font:14px/1.5 -apple-system,'PingFang SC',sans-serif}}
 header{{padding:18px 24px;border-bottom:1px solid #262b31}} h1{{margin:0;font-size:18px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:16px;padding:20px 24px}}
 .c{{background:#181c22;border:1px solid #262b31;border-radius:12px;overflow:hidden}}
 .h{{padding:10px 12px}} .fl{{color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:6px}}
 .p{{display:grid;grid-template-columns:1fr 1fr;gap:2px;background:#0c0e11}} figure{{margin:0;position:relative}} figure img{{width:100%;display:block}}
 figcaption{{position:absolute;left:6px;top:6px;background:#000a;color:#fff;font-size:10px;padding:1px 6px;border-radius:4px}}
 .rs{{padding:8px 12px;color:#c3c9cf;font-size:12px}} .dir{{padding:0 12px 10px;color:#5f6b76;font-size:11px}}</style></head><body>
<header><h1>{title} <span style="color:#8b939c;font-size:13px">· {len(cards)} 对</span></h1></header>
<div class="grid">{''.join(cards)}</div></body></html>"""
    open(out, "w", encoding="utf-8").write(html)
    return len(cards)


def main():
    args = sys.argv[1:]
    if args and args[0] == "--review":                        # agent 判定 → 终审 HTML
        raw_dir, verdicts_path = args[1], args[2]
        out = args[3] if len(args) > 3 else os.path.join(raw_dir, "qa_review.html")
        n = build_review_html(raw_dir, json.load(open(verdicts_path)), out)
        print(f"[qa_screen] 终审 HTML：{n} 对确认问题 → {out}")
        return
    raw_dir = args[0] if args else "./out/debug_gallery"
    out = args[1] if len(args) > 1 else os.path.join(raw_dir, "qa_flagged.html")
    items = collect(raw_dir)
    flagged = [it for it in items if it["flags"]]
    flagged.sort(key=lambda it: -max((f[2] for f in it["flags"]), default=0))
    # JSON（供 agent/程序消费）
    js = [{"dir": os.path.basename(it["dir"]),
           "op": (it["sample"].get("edit") or {}).get("op"),
           "instruction": it["sample"].get("caption") or it["sample"].get("instruction"),
           "flags": [{"code": c, "reason": r, "severity": s} for c, r, s in it["flags"]],
           "before": it["before"], "after": it["after"]} for it in flagged]
    json.dump(js, open(os.path.join(raw_dir, "qa_flagged.json"), "w"), ensure_ascii=False, indent=1)
    open(out, "w", encoding="utf-8").write(render_report(flagged))
    print(f"[qa_screen] {len(items)} 对中标出 {len(flagged)} 对可疑 → {out}")
    from collections import Counter
    cnt = Counter(c for it in flagged for c, _r, _s in it["flags"])
    for code, n in cnt.most_common():
        print(f"    {code}: {n}")


if __name__ == "__main__":
    main()
