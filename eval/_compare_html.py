"""三栏对比 HTML(before | Qwen结果 | 我们的target)生成，纯 PIL/base64，无额外依赖。
供 qwen_edit_sglang.py / qwen_edit_diffusers.py 共用。"""
from __future__ import annotations
import base64
import io
import os

from PIL import Image


def _first(paths):
    return sorted(paths)[0] if paths else None


def _b64(path, mx=384, q=82):
    im = Image.open(path).convert("RGB")
    im.thumbnail((mx, mx))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=q)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _cell(path, label):
    if not path or not os.path.exists(path):
        return f'<figure class="miss"><figcaption>{label}(无)</figcaption></figure>'
    return f'<figure><img src="{_b64(path)}"><figcaption>{label}</figcaption></figure>'


def write_compare_html(results, out):
    """results: [{key, op, prompt, before, result, target}]，result 为 None 表示该图推理失败。"""
    cards = []
    for r in results:
        instr = str(r.get("prompt", "")).replace("<", "&lt;")
        op = (r.get("op") or "")
        op = op[7:] if op.startswith("object_") else op
        cards.append(f"""<div class="c"><div class="h"><span class="op">{op}</span>
          <span class="i">{instr}</span></div><div class="row">
          {_cell(r.get('before'), 'before')}{_cell(r.get('result'), 'Qwen-Edit')}{_cell(r.get('target'), '我们的 target')}
          </div></div>""")
    html = f"""<!doctype html><meta charset="utf-8"><title>Qwen-Image-Edit 效果对比</title>
<style>:root{{color-scheme:dark}}body{{margin:0;background:#111418;color:#e6e8ea;font:14px/1.5 -apple-system,'PingFang SC',sans-serif}}
h1{{padding:16px 24px;margin:0;font-size:18px;border-bottom:1px solid #262b31}}
.grid{{display:grid;grid-template-columns:1fr;gap:14px;padding:18px 24px}}
.c{{background:#181c22;border:1px solid #262b31;border-radius:12px;overflow:hidden}}
.h{{padding:9px 12px;display:flex;gap:8px;align-items:baseline}}
.op{{background:#5a636e;color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:6px}}
.row{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:2px;background:#0c0e11}}
figure{{margin:0;position:relative;min-height:60px}}figure img{{width:100%;display:block}}
figcaption{{position:absolute;left:6px;top:6px;background:#000a;color:#fff;font-size:10px;padding:1px 6px;border-radius:4px}}
.miss{{display:flex;align-items:center;justify-content:center;color:#5f6b76}}</style>
<h1>Qwen-Image-Edit 基座效果 · {len(results)} 张（before ｜ Qwen 编辑结果 ｜ 我们渲的 target）</h1>
<div class="grid">{''.join(cards)}</div>"""
    open(out, "w", encoding="utf-8").write(html)
