"""预取 Poly Haven 室内 HDRI（免费 CC0），用于 HSSD/tabletop 的图像照明（脱 CG 平光感）。

用法：
  python -m datagen.orchestrator.prefetch_hdri                 # 默认精选室内 + 1k
  python -m datagen.orchestrator.prefetch_hdri --res 2k --n 8  # 更高分辨率 / 更多张

Poly Haven API（无需 key）：
  files 端点 https://api.polyhaven.com/files/<slug> → hdri.<res>.hdr.url
CC0 许可，可商用。默认存到 ./assets/haven/hdris。
"""
from __future__ import annotations
import argparse
import json
import os
import ssl
import urllib.request


def _ssl_ctx():
    """macOS 常缺系统 CA → 优先用 certifi 的 CA 包，没有就退回不校验（仅下公开 CC0 资产）。"""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl._create_unverified_context()


_CTX = _ssl_ctx()

# 精选：读起来像真实室内房间的 HDRI（避免纯影棚/教堂/仓库那种极端场景）。
CURATED = [
    "cayley_interior", "combination_room", "aft_lounge", "anniversary_lounge",
    "art_studio", "church_meeting_room", "brown_photostudio_02", "small_hangar_01",
]
API = "https://api.polyhaven.com/files/"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (image-edit-lab hdri prefetch)"})
    with urllib.request.urlopen(req, timeout=60, context=_CTX) as r:
        return r.read()


def fetch_one(slug, res, out_dir):
    meta = json.loads(_get(API + slug))
    hdri = meta.get("hdri", {})
    entry = hdri.get(res) or hdri.get("1k") or next(iter(hdri.values()), None)
    if not entry:
        print(f"[hdri] {slug}: 无 HDRI 文件，跳过"); return None
    url = (entry.get("hdr") or entry.get("exr") or {}).get("url") or entry.get("url")
    if not url:
        print(f"[hdri] {slug}: 无下载 url，跳过"); return None
    ext = ".hdr" if url.endswith(".hdr") else ".exr"
    dst = os.path.join(out_dir, f"{slug}_{res}{ext}")
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        print(f"[hdri] {slug}: 已存在，跳过"); return dst
    open(dst, "wb").write(_get(url))
    print(f"[hdri] {slug} → {dst} ({os.path.getsize(dst)//1024} KB)")
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./assets/haven/hdris")
    ap.add_argument("--res", default="1k")
    ap.add_argument("--n", type=int, default=len(CURATED))
    ap.add_argument("--slugs", nargs="*", default=None, help="自定义 slug 列表（覆盖精选）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    slugs = (args.slugs or CURATED)[: args.n]
    ok = 0
    for s in slugs:
        try:
            if fetch_one(s, args.res, args.out):
                ok += 1
        except Exception as e:
            print(f"[hdri] {s} 下载失败: {e}")
    print(f"[hdri] 完成：{ok}/{len(slugs)} 张 → {args.out}")


if __name__ == "__main__":
    main()
