"""把一批渲染产物做成**自包含 HTML 画廊**（图片内嵌 base64 JPEG，单文件浏览器直接开）。

用法： python datagen/scripts/make_gallery.py <raw_dir> [out.html]
每对显示 before|after 并排 + 指令 + 算子/场景/HDRI/主体等标签，按算子分组。
"""
from __future__ import annotations
import base64
import glob
import io
import json
import os
import sys

from PIL import Image

OP_ZH = {"object_delete": "删除", "object_add": "增加", "object_move": "移动",
         "object_scale": "缩放", "object_rotate": "旋转", "object_replace": "替换"}
OP_COLOR = {"object_delete": "#e05252", "object_add": "#3fb27f", "object_move": "#4a90d9",
            "object_scale": "#c9973f", "object_rotate": "#9b6bd6", "object_replace": "#d95fa0"}


def _b64_jpeg(path, max_px=448, quality=82):
    im = Image.open(path).convert("RGB")
    if max(im.size) > max_px:
        im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _first(paths):
    return sorted(paths)[0] if paths else None


def collect(raw_dir):
    items = []
    for sj in sorted(glob.glob(os.path.join(raw_dir, "*", "sample.json"))):
        d = os.path.dirname(sj)
        b = _first(glob.glob(os.path.join(d, "before_*.png")))
        a = _first(glob.glob(os.path.join(d, "after_*.png")))
        if not b or not a:
            continue
        try:
            rec = json.load(open(sj))
        except Exception:
            continue
        edit = rec.get("edit") or {}
        prov = (rec.get("provenance") or {}).get("scene_source") or {}
        items.append({
            "op": edit.get("op"),
            "instr": rec.get("caption") or rec.get("instruction") or "",
            "noun": edit.get("noun") or (edit.get("from") or {}).get("category"),
            "scene": prov.get("scene_id"),
            "hdri": rec.get("hdri"),
            "style": rec.get("caption_style"),
            "lang": rec.get("caption_lang"),
            "before": _b64_jpeg(b),
            "after": _b64_jpeg(a),
        })
    return items


def render_html(items, title="调试画廊"):
    # 按算子分组统计
    counts = {}
    for it in items:
        counts[it["op"]] = counts.get(it["op"], 0) + 1
    chips = "".join(
        f'<span class="chip" style="background:{OP_COLOR.get(op,"#888")}">'
        f'{OP_ZH.get(op,op)} {n}</span>' for op, n in sorted(counts.items()))

    cards = []
    for it in items:
        col = OP_COLOR.get(it["op"], "#888")
        tags = []
        if it["scene"]:
            tags.append(f'场景 {it["scene"]}')
        if it["hdri"]:
            tags.append(f'HDRI {it["hdri"].replace("_1k.hdr","")}')
        if it["style"]:
            tags.append(f'风格 {it["style"]}')
        if it["lang"]:
            tags.append(it["lang"])
        meta = " · ".join(tags)
        cards.append(f"""
        <div class="card">
          <div class="hd"><span class="op" style="background:{col}">{OP_ZH.get(it['op'], it['op'])}</span>
            <span class="instr">{_esc(it['instr'])}</span></div>
          <div class="pair">
            <figure><img src="{it['before']}"><figcaption>before</figcaption></figure>
            <figure><img src="{it['after']}"><figcaption>after</figcaption></figure>
          </div>
          <div class="meta">{_esc(meta)}</div>
        </div>""")

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; background:#111418; color:#e6e8ea; font:14px/1.5 -apple-system,system-ui,'PingFang SC',sans-serif; }}
  header {{ padding:20px 24px; border-bottom:1px solid #262b31; position:sticky; top:0; background:#111418ee; backdrop-filter:blur(6px); }}
  h1 {{ margin:0 0 8px; font-size:19px; }}
  .chips .chip {{ display:inline-block; margin:3px 6px 3px 0; padding:2px 10px; border-radius:20px; color:#fff; font-size:12px; font-weight:600; }}
  .sub {{ color:#8b939c; font-size:12px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:16px; padding:20px 24px; }}
  .card {{ background:#181c22; border:1px solid #262b31; border-radius:12px; overflow:hidden; }}
  .hd {{ padding:10px 12px; display:flex; gap:8px; align-items:baseline; }}
  .op {{ color:#fff; font-size:11px; font-weight:700; padding:2px 8px; border-radius:6px; white-space:nowrap; }}
  .instr {{ font-size:13px; color:#eef1f4; }}
  .pair {{ display:grid; grid-template-columns:1fr 1fr; gap:2px; background:#0c0e11; }}
  figure {{ margin:0; position:relative; }}
  figure img {{ width:100%; display:block; }}
  figcaption {{ position:absolute; left:6px; top:6px; background:#000a; color:#fff; font-size:10px; padding:1px 6px; border-radius:4px; }}
  .meta {{ padding:8px 12px; color:#8b939c; font-size:11px; border-top:1px solid #262b31; }}
</style></head><body>
<header>
  <h1>{title} <span class="sub">· {len(items)} 对</span></h1>
  <div class="chips">{chips}</div>
</header>
<div class="grid">{''.join(cards)}</div>
</body></html>"""


def _esc(s):
    return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    raw_dir = sys.argv[1] if len(sys.argv) > 1 else "./out/debug_gallery"
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(raw_dir, "index.html")
    items = collect(raw_dir)
    if not items:
        print(f"[gallery] {raw_dir} 下没有可用的 before/after 对"); return
    html = render_html(items)
    open(out, "w", encoding="utf-8").write(html)
    print(f"[gallery] {len(items)} 对 → {out}（{os.path.getsize(out)//1024} KB）")


if __name__ == "__main__":
    main()
