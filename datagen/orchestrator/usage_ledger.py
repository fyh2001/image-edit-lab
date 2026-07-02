"""
已用资产账本（usage ledger）。

作用：每生成完一批数据，扫描输出里的 provenance，把「实际用过的」
Objaverse uid 和 3D-FRONT 场景累加进账本（去重）。下次下载/生成时
据此过滤，避免重复使用同一批资产/场景。

账本格式（JSON）：
{
  "objaverse_uids": ["uid1", ...],     # 已用过的物体
  "front3d_scenes": ["xxx.json", ...], # 已用过的场景文件名
  "batches": [{"time": ..., "raw_dir": ..., "new_uids": N, "new_scenes": M}]
}

用法：
  # 一批跑完后，把用过的登记进账本
  python -m orchestrator.usage_ledger update \\
      --ledger ./assets/used_ledger.json --scan ./out/raw

  # 查看账本统计
  python -m orchestrator.usage_ledger show --ledger ./assets/used_ledger.json
"""
from __future__ import annotations
import os
import json
import glob
import time
import argparse


def load_ledger(path: str) -> dict:
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    else:
        d = {}
    d.setdefault("objaverse_uids", [])
    d.setdefault("front3d_scenes", [])
    d.setdefault("batches", [])
    return d


def save_ledger(path: str, ledger: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)


def used_uid_set(ledger: dict) -> set:
    return set(ledger.get("objaverse_uids", []))


def used_scene_set(ledger: dict) -> set:
    return set(ledger.get("front3d_scenes", []))


def scan_outputs(raw_dir: str):
    """从输出目录的 sample.json provenance 里收集已用 uid / 场景。"""
    uids, scenes = set(), set()
    for sample_json in glob.glob(os.path.join(raw_dir, "*", "sample.json")):
        try:
            with open(sample_json, encoding="utf-8") as f:
                prov = json.load(f).get("provenance", {})
        except Exception:
            continue
        uids.update(prov.get("objaverse_uids", []) or [])
        sc = prov.get("front3d_scene")
        if sc:
            scenes.add(sc)
    return uids, scenes


def update(ledger_path: str, raw_dir: str) -> dict:
    ledger = load_ledger(ledger_path)
    old_u, old_s = used_uid_set(ledger), used_scene_set(ledger)
    new_u, new_s = scan_outputs(raw_dir)

    added_u = new_u - old_u
    added_s = new_s - old_s
    ledger["objaverse_uids"] = sorted(old_u | new_u)
    ledger["front3d_scenes"] = sorted(old_s | new_s)
    ledger["batches"].append({
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "raw_dir": os.path.abspath(raw_dir),
        "new_uids": len(added_u),
        "new_scenes": len(added_s),
    })
    save_ledger(ledger_path, ledger)
    print(f"[ledger] 扫描 {raw_dir}")
    print(f"[ledger] 新增已用 uid {len(added_u)}，场景 {len(added_s)}")
    print(f"[ledger] 累计已用 uid {len(ledger['objaverse_uids'])}，"
          f"场景 {len(ledger['front3d_scenes'])} -> {ledger_path}")
    return ledger


def show(ledger_path: str):
    ledger = load_ledger(ledger_path)
    print(f"账本: {ledger_path}")
    print(f"  已用 Objaverse uid : {len(ledger['objaverse_uids'])}")
    print(f"  已用 3D-FRONT 场景 : {len(ledger['front3d_scenes'])}")
    print(f"  批次记录           : {len(ledger['batches'])}")
    for b in ledger["batches"][-5:]:
        print(f"    - {b['time']}  +{b['new_uids']} uid / +{b['new_scenes']} 场景")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("update", help="扫描输出并登记已用资产")
    u.add_argument("--ledger", default="./assets/used_ledger.json")
    u.add_argument("--scan", required=True, help="要扫描的输出目录（run.output_dir）")

    s = sub.add_parser("show", help="查看账本统计")
    s.add_argument("--ledger", default="./assets/used_ledger.json")

    args = ap.parse_args()
    if args.cmd == "update":
        update(args.ledger, args.scan)
    elif args.cmd == "show":
        show(args.ledger)


if __name__ == "__main__":
    main()
