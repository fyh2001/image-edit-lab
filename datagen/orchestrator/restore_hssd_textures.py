"""
把 HSSD glb 里的 Basis/KTX2 贴图解码成 PNG 并重写进 glb —— 这样 Blender 能加载真实
木纹/布纹贴图，家具不再是平涂中性色。用 KTX-Software 的 `ktx` CLI 转码。

    KTX_BIN=/path/to/ktx python -m orchestrator.restore_hssd_textures --data-dir ./assets/hssd

`ktx` 二进制：官方 macOS/Linux release 里有（KhronosGroup/KTX-Software），本项目 tools/ 下
放了一份 macOS-arm64 的。转码是一次性的，转完 glb 里就是 PNG 贴图了。
"""
from __future__ import annotations
import os
import json
import glob
import struct
import argparse
import subprocess
import tempfile


def _decode_ktx2(ktx_bin, ktx2_bytes):
    """KTX2(Basis) 字节 → PNG 字节；失败返回 None。"""
    with tempfile.TemporaryDirectory() as td:
        ip = os.path.join(td, "t.ktx2")
        op = os.path.join(td, "t.png")
        with open(ip, "wb") as f:
            f.write(ktx2_bytes)
        r = subprocess.run([ktx_bin, "extract", "--level", "0", "--transcode", "rgba8", ip, op],
                           capture_output=True)
        if r.returncode != 0 or not os.path.exists(op):
            return None
        with open(op, "rb") as f:
            return f.read()


def restore_glb(path, ktx_bin) -> bool:
    """把一个 glb 里所有 image/ktx2 贴图解成 PNG 并重写；有改动返回 True。"""
    data = open(path, "rb").read()
    if data[:4] != b"glTF":
        return False
    jlen = struct.unpack("<I", data[12:16])[0]
    j = json.loads(data[20:20 + jlen])
    off = 20 + jlen
    blen = struct.unpack("<I", data[off:off + 4])[0]
    bin_ = bytearray(data[off + 8:off + 8 + blen])
    bvs = j.get("bufferViews", [])
    changed = False
    for im in j.get("images", []):
        if im.get("mimeType") != "image/ktx2":
            continue
        bv = bvs[im["bufferView"]]
        s = bv.get("byteOffset", 0)
        png = _decode_ktx2(ktx_bin, bytes(bin_[s:s + bv["byteLength"]]))
        if png is None:
            continue
        while len(bin_) % 4:                                   # 4 字节对齐
            bin_.append(0)
        noff = len(bin_)
        bin_ += png
        im["bufferView"] = len(bvs)
        im["mimeType"] = "image/png"
        bvs.append({"buffer": 0, "byteOffset": noff, "byteLength": len(png)})
        changed = True
    if not changed:
        return False
    # 贴图从 basisu 扩展指回标准 source
    for tex in j.get("textures", []):
        ext = (tex.get("extensions") or {}).get("KHR_texture_basisu")
        if ext is not None:
            tex["source"] = ext.get("source", tex.get("source"))
            del tex["extensions"]["KHR_texture_basisu"]
            if not tex["extensions"]:
                del tex["extensions"]
    for key in ("extensionsUsed", "extensionsRequired"):
        if "KHR_texture_basisu" in j.get(key, []):
            j[key] = [e for e in j[key] if e != "KHR_texture_basisu"]
            if not j[key]:
                del j[key]
    j["bufferViews"] = bvs
    if j.get("buffers"):
        j["buffers"][0]["byteLength"] = len(bin_)
    njson = json.dumps(j, separators=(",", ":")).encode()
    njson += b" " * ((4 - len(njson) % 4) % 4)
    while len(bin_) % 4:
        bin_.append(0)
    total = 12 + 8 + len(njson) + 8 + len(bin_)
    out = b"glTF" + struct.pack("<II", 2, total)
    out += struct.pack("<II", len(njson), 0x4E4F534A) + njson
    out += struct.pack("<II", len(bin_), 0x004E4942) + bytes(bin_)
    open(path, "wb").write(out)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="./assets/hssd")
    ap.add_argument("--ktx", default=os.environ.get("KTX_BIN", "ktx"))
    ap.add_argument("--glob", default="**/*.glb")
    args = ap.parse_args()
    glbs = [g for g in glob.glob(os.path.join(args.data_dir, args.glob), recursive=True)
            if ".collider" not in g and ".filtered" not in g]
    n = 0
    for i, g in enumerate(glbs):
        try:
            if restore_glb(g, args.ktx):
                n += 1
        except Exception as e:
            print(f"skip {g}: {e}")
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(glbs)} ...")
    print(f"还原贴图：{n}/{len(glbs)} 个 glb")


if __name__ == "__main__":
    main()
