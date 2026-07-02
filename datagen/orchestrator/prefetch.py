"""
在 orchestrator 侧预下载 Objaverse 资产到本地缓存，并生成 uid 列表。
避免每个渲染进程联网下载（慢且易触发限流）。

可选：用 Objaverse-LVIS 的类别标注，把 uid->category 写出来，
供 worker 在指令里填真实名词（如 "a wooden chair" 而非 "object"）。

用法：
    python -m orchestrator.prefetch --n 500 --out ./assets/objaverse \\
        --uid-list ./assets/objaverse_uids.txt
"""
from __future__ import annotations
import os
import argparse
import json
import random


def sample_diverse_uids(lvis, n, exclude=None, seed=0, indoor_only=True):
    """跨类别均匀采样 n 个 uid（纯函数，可单测，不依赖 objaverse）。

    旧实现按 LVIS 字典顺序取前 N → 全挤在头几个类别。这里改成：打乱类别顺序后
    round-robin，每轮从每个类别各取一个，保证类别多样性。固定 seed 可复现。
    indoor_only=True 时只从**室内家居**类别采（不下载动物/车辆/户外，从源头保证场景协调）。

    Args:
        lvis: {category: [uid, ...]}
        n: 目标数量
        exclude: 要跳过的 uid 集合（已用账本）
    Returns:
        (uids:list, uid2cat:dict)
    """
    exclude = exclude or set()
    rng = random.Random(seed)
    cats = list(lvis.keys())
    if indoor_only:
        from datagen.worker.assets.indoor_categories import is_indoor
        indoor = [c for c in cats if is_indoor(c)]
        if indoor:
            cats = indoor                              # 只采室内类；万一都不匹配则保留全部
    rng.shuffle(cats)
    iters = {c: iter(rng.sample(lvis[c], len(lvis[c]))) for c in cats}
    uid2cat, uids = {}, []
    while len(uids) < n:
        progressed = False
        for c in cats:
            if len(uids) >= n:
                break
            uid = next(iters[c], None)
            while uid is not None and uid in exclude:
                uid = next(iters[c], None)
            if uid is None:
                continue
            uid2cat[uid] = c
            uids.append(uid)
            progressed = True
        if not progressed:                 # 所有类别都取空了
            break
    return uids, uid2cat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200, help="下载多少个资产")
    ap.add_argument("--out", default="./assets/objaverse")
    ap.add_argument("--uid-list", default="./assets/objaverse_uids.txt")
    ap.add_argument("--category-map", default="./assets/objaverse_categories.json")
    ap.add_argument("--meta-map", default="./assets/objaverse_meta.json",
                    help="富标注输出（name/tags/license），供 metadata 的物体描述")
    ap.add_argument("--exclude-used", default=None,
                    help="已用账本路径(used_ledger.json)，下载时跳过其中已用过的 uid")
    ap.add_argument("--exclude-db", action="store_true",
                    help="从数据库 asset_usage 表排除已用过的 uid（跨批次去重，需 --db-url 或 $DATABASE_URL）")
    ap.add_argument("--db-url", default=None)
    ap.add_argument("--seed", type=int, default=0, help="跨类别采样随机种子（可复现）")
    args = ap.parse_args()

    import objaverse
    os.makedirs(args.out, exist_ok=True)

    # 已用过的 uid（从账本读），下载时排除，保证新批次不重复
    exclude = set()
    if args.exclude_used:
        from datagen.orchestrator.usage_ledger import load_ledger, used_uid_set
        exclude = used_uid_set(load_ledger(args.exclude_used))
        print(f"已用账本(JSON): 排除 {len(exclude)} 个用过的 uid")
    if args.exclude_db:
        from datagen.orchestrator.db import get_engine
        from datagen.orchestrator.ingest import used_asset_uids
        db_used = used_asset_uids(get_engine(args.db_url))
        exclude |= db_used
        print(f"已用账本(DB): 排除 {len(db_used)} 个用过的 uid，合计排除 {len(exclude)}")

    # 取带 LVIS 类别的子集，跨类别均匀采样（保证类别多样性），顺手拿到 uid->category
    lvis = objaverse.load_lvis_annotations()      # {category: [uid, ...]}
    uids, uid2cat = sample_diverse_uids(lvis, args.n, exclude=exclude, seed=args.seed)
    print(f"跨 {len(set(uid2cat.values()))} 个类别采样到 {len(uids)} 个 uid")

    print(f"下载 {len(uids)} 个 Objaverse 资产 ...")
    objects = objaverse.load_objects(uids=uids)   # {uid: local_glb_path}

    # 软链/复制到统一缓存布局 <out>/<uid>.glb
    for uid, src in objects.items():
        dst = os.path.join(args.out, f"{uid}.glb")
        if not os.path.exists(dst):
            try:
                os.symlink(os.path.abspath(src), dst)
            except OSError:
                import shutil
                shutil.copy(src, dst)

    with open(args.uid_list, "w") as f:
        f.write("\n".join(objects.keys()))
    with open(args.category_map, "w", encoding="utf-8") as f:
        json.dump({u: uid2cat.get(u, "object") for u in objects}, f, ensure_ascii=False, indent=2)

    # 富标注：从 Objaverse 标注取 name/tags/license，写进 metadata 当物体描述/溯源
    try:
        ann = objaverse.load_annotations(list(objects.keys()))
        meta = {u: {"category": uid2cat.get(u, "object"),
                    "name": ann.get(u, {}).get("name"),
                    "tags": [t.get("name") for t in (ann.get(u, {}).get("tags") or [])][:10],
                    "license": ann.get(u, {}).get("license")}
                for u in objects}
        with open(args.meta_map, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"[prefetch] 富标注写出跳过: {e}")

    print(f"完成。uid 列表 -> {args.uid_list}，类别映射 -> {args.category_map}，"
          f"富标注 -> {args.meta_map}")
    print("提示：类别映射在加载时写入 noun（指令更自然），富标注写入物体描述。")


if __name__ == "__main__":
    main()
