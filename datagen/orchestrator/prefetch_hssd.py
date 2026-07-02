"""
下载少量 HSSD（Habitat Synthetic Scenes Dataset）场景到本地缓存，供 front-style 室内场景使用。

HSSD 在 HuggingFace（hssd/hssd-hab）：一个场景 = 1 个 stage(房间外壳 glb) +
若干 object_instances（每个引用一个 object glb + Habitat Y-up 下的 translation/rotation/scale）。
这里只拉「渲染用 glb」（跳过 collider/config），按需缓存。

    python -m orchestrator.prefetch_hssd --scenes 102344115 --out ./assets/hssd

注：hssd-hab 在 HF 上标注 gated，但 resolve 直链当前可直接 GET。若被拦，需带 HF token。
"""
from __future__ import annotations
import os
import json
import glob
import struct
import argparse
import urllib.request

BASE = "https://huggingface.co/datasets/hssd/hssd-hab/resolve/main"


def strip_basisu(path: str) -> bool:
    """把 glb 里的 KHR_texture_basisu 从 extensionsRequired/Used 移除。

    HSSD 家具贴图用 Basis Universal(KTX2) 压缩；Blender 4.2 自带 glTF 导入器不支持，
    且它在 extensionsRequired 里 → 直接导入报错。降级为可选后，Blender 能导入几何、
    只是跳过这些贴图（家具显默认色，loader 里再给中性材质）。
    """
    try:
        data = open(path, "rb").read()
        magic, ver, _ = struct.unpack("<III", data[:12])
        if magic != 0x46546C67:
            return False
        clen, ctype = struct.unpack("<II", data[12:20])
        jchunk = data[20:20 + clen]
        j = json.loads(jchunk.decode("utf-8"))
        changed = False
        for key in ("extensionsRequired", "extensionsUsed"):
            if "KHR_texture_basisu" in j.get(key, []):
                j[key] = [e for e in j[key] if e != "KHR_texture_basisu"]
                if not j[key]:
                    del j[key]
                changed = True
        if not changed:
            return False
        newj = json.dumps(j, separators=(",", ":")).encode("utf-8")
        newj += b" " * ((4 - len(newj) % 4) % 4)
        rest = data[20 + clen:]
        out = (struct.pack("<III", magic, ver, 12 + 8 + len(newj) + len(rest))
               + struct.pack("<II", len(newj), ctype) + newj + rest)
        open(path, "wb").write(out)
        return True
    except Exception:
        return False


_TOKEN = "unset"


def _hf_headers():
    """带上 HF token（gated 场景/复合 ID 需鉴权；未登录则退化为匿名）。"""
    global _TOKEN
    if _TOKEN == "unset":
        try:
            from huggingface_hub import get_token
            _TOKEN = get_token()
        except Exception:
            _TOKEN = None
    h = {"User-Agent": "Mozilla/5.0"}
    if _TOKEN:
        h["Authorization"] = f"Bearer {_TOKEN}"
    return h


def _get(url, dst, tries=5):
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return True
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    import time
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=_hf_headers())
            with urllib.request.urlopen(req) as r:
                if r.status != 200:
                    return False
                data = r.read()
            with open(dst, "wb") as f:
                f.write(data)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < tries - 1:     # 限流 → 退避重试
                time.sleep(6 * (attempt + 1))
                continue
            return False
        except Exception:
            if attempt < tries - 1:
                time.sleep(2)
                continue
            return False
    return False


def _object_glb_relpath(template: str):
    """template_name → 候选的 object glb 相对路径（按首字符分桶，部分在 decomposed/）。"""
    c = template[0]
    return [
        f"objects/{c}/{template}.glb",
        f"objects/decomposed/{c}/{template}.glb",
        f"objects/decomposed/{template}.glb",
    ]


def _find_ktx():
    """找可用的 ktx CLI：$KTX_BIN → 项目 tools/ktx/ktx → PATH。找不到返回 None。"""
    import shutil
    cand = os.environ.get("KTX_BIN")
    if cand and os.path.exists(cand):
        return cand
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    local = os.path.join(here, "tools", "ktx", "ktx")
    if os.path.exists(local):
        return local
    return shutil.which("ktx")


def fetch_scene(scene_id: str, out: str):
    # 1) scene_instance.json
    sj_rel = f"scenes/{scene_id}.scene_instance.json"
    sj_dst = os.path.join(out, sj_rel)
    if not _get(f"{BASE}/{sj_rel}", sj_dst):
        raise RuntimeError(f"拉取 scene json 失败: {scene_id}")
    spec = json.load(open(sj_dst))

    # 1b) 语义表（object_id -> 类别/名称），一次即可，供真实名词 + 描述
    _get(f"{BASE}/semantics/objects.csv", os.path.join(out, "semantics_objects.csv"))

    # 2) stage（房间外壳）
    stage_tmpl = spec["stage_instance"]["template_name"]      # e.g. "stages/102344115"
    stage_rel = f"{stage_tmpl}.glb"
    ok_stage = _get(f"{BASE}/{stage_rel}", os.path.join(out, stage_rel))

    # 3) 每个 object instance 的渲染 glb（并行下载，几十个小文件串行太慢）
    from concurrent.futures import ThreadPoolExecutor
    templates = sorted({o["template_name"] for o in spec.get("object_instances", [])})

    def _fetch_one(t):
        for rel in _object_glb_relpath(t):
            if _get(f"{BASE}/{rel}", os.path.join(out, rel)):
                return t, True
        return t, False

    got, miss = 0, []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for t, ok in ex.map(_fetch_one, templates):
            if ok:
                got += 1
            else:
                miss.append(t)
    # 4) 处理贴图：有 ktx CLI 就把 Basis/KTX2 解成 PNG 还原真实材质；否则剥掉 basisu
    #    扩展（Blender 4.2 读不了 basisu，家具走中性色兜底）。
    glbs = [g for g in glob.glob(os.path.join(out, "**", "*.glb"), recursive=True)
            if ".collider" not in g and ".filtered" not in g]
    ktx = _find_ktx()
    if ktx:
        from datagen.orchestrator.restore_hssd_textures import restore_glb
        n = sum(bool(restore_glb(g, ktx)) for g in glbs)
        tex_msg = f"还原真实贴图 {n} 个 glb"
    else:
        n = sum(strip_basisu(p) for p in glbs)
        tex_msg = f"剥 basisu {n} 个 glb（无 ktx，家具走中性色）"
    print(f"[hssd] scene {scene_id}: stage={'OK' if ok_stage else 'MISS'}  "
          f"objects {got}/{len(templates)}  缺 {len(miss)}  {tex_msg}")
    if miss:
        print(f"[hssd]   缺失模板(前若干): {miss[:5]}")
    return sj_dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", nargs="+", default=["102344115"], help="HSSD 场景 id 列表")
    ap.add_argument("--out", default="./assets/hssd")
    args = ap.parse_args()
    if not os.environ.get("SSL_CERT_FILE"):
        try:
            import certifi
            os.environ["SSL_CERT_FILE"] = certifi.where()
        except Exception:
            pass
    ok, skipped = 0, []
    for sid in args.scenes:
        try:
            fetch_scene(str(sid), args.out)             # 单场景失败不拖垮整批
            ok += 1
        except Exception as e:
            print(f"[hssd] 跳过场景 {sid}: {e}")
            skipped.append(str(sid))
    print(f"[hssd] 完成 {ok}，跳过 {len(skipped)}：{skipped[:8]}")


if __name__ == "__main__":
    main()
