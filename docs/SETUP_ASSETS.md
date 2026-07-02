# 资产下载指引（SETUP_ASSETS）

原则：**绝不在渲染时自动下载**。所有资产通过下面的脚本/命令**提前一次性下好**，
放到**持久化存储**（组目录 / 对象存储挂载点），机器清盘也不用重下；渲染时
worker 只读本地缓存。

> 为什么这样：8×H100 上几十个渲染进程同时跑，若各自联网下载会打爆网络、
> 触发限流，也破坏可复现性。

---

## 0. 准备

```bash
# 进入项目根目录
cd ~/Desktop/blender_data_pipeline

# 装依赖（orchestrator 环境）
pip install -r requirements.txt
pip install blenderproc
blenderproc quickstart           # 验证 BlenderProc 装好

# 可选：把资产目录指到持久盘（默认 ./assets）
export ASSET_DIR=/path/to/persistent/assets
```

最终目录结构：

```
$ASSET_DIR/
├── objaverse/                 # Objaverse 物体 (.glb)
├── objaverse_uids.txt         # uid 列表（脚本生成）
├── objaverse_categories.json  # uid->类别（让指令名词更自然）
├── haven/hdris/               # Poly Haven HDRI 环境光 (CC0)
├── cc_textures/               # ambientCG PBR 材质 (CC0)
├── 3D-FRONT/                  # 3D-FRONT 场景 json（手动下载）
├── 3D-FUTURE-model/           # 3D-FUTURE 家具库（手动下载）
└── 3D-FRONT-texture/          # 3D-FRONT 墙地贴图（手动下载）
```

---

## 1. Objaverse 物体（可脚本自动下）

```bash
# 默认下 200 个；改数量用 OBJAVERSE_N
OBJAVERSE_N=200 bash scripts/download_assets.sh objaverse
```

体积：平均 ~11 MB/个，方差大。200 个 ≈ ~2 GB。
License：逐资产不同，研究随便用，商用需逐个核对。

---

## 2. Poly Haven HDRI（可脚本自动下，CC0）

```bash
bash scripts/download_assets.sh haven
```

下完 HDRI 在 `$ASSET_DIR/haven/hdris/`，已对应 config 的 `hdri_dir`。
只需 ~10–30 个就有足够光照多样性。License：全 CC0，可商用。

---

## 3. ambientCG 材质（可脚本自动下，CC0，可选）

```bash
bash scripts/download_assets.sh cc_textures
```

给未来的"换材质"编辑算子用。License：全 CC0。

---

## 4. 3D-FRONT 室内场景（必须手动申请，无法脚本自动下）

打印指引：

```bash
bash scripts/download_assets.sh front3d
```

手动步骤：

1. 打开申请页：<https://tianchi.aliyun.com/specials/promotion/alibaba-3d-scene-dataset>
2. 申请并下载三件套，解压到 `$ASSET_DIR/` 下：
   - `3D-FRONT`（场景 json，~3–5 GB）
   - `3D-FUTURE-model`（家具库，**~20 GB，所有场景共享，必须整套下**）
   - `3D-FRONT-texture`（贴图，~2–5 GB）
3. 路径填进 `configs/front3d.yaml` 的 `scene.params`。

> ⚠️ 体积固定大头：不管用 100 还是 5000 个场景，家具库都得整套下（~25–30 GB）。
> License：仅限学术研究，不可商用。

---

## 5. 一键下全部（可自动的部分）

```bash
bash scripts/download_assets.sh all
```

会依次下 Objaverse + Haven + cc_textures，并打印 3D-FRONT 手动指引。

---

## 空间速查

| 资产 | 体积 | 增长方式 | License |
|---|---|---|---|
| Objaverse | ~11 MB/个（100 个 ≈ 1–2 GB） | 按个数线性 | 混杂 |
| Poly Haven HDRI | ~10–25 MB/个（2K） | 几十个就够 | CC0 |
| ambientCG 材质 | 每套几十 MB | 按需 | CC0 |
| 3D-FRONT 全库 | **~25–30 GB（固定）** | 一次性 | 研究 only |
| **渲染输出** | **每对 ~2–3 MB（768²）** | **按量线性，10 万对 ≈ 250 GB** | 你自己的 |

素材是一次性小投入；**真正会涨的是渲染输出**——大规模时打包成 WebDataset
上传 HF / 对象存储，本地只留 scratch 缓存。

---

## 6. 批次去重：已用账本（used ledger）

避免不同批次重复使用同一批资产/场景。流程是「生成 → 登记已用 → 下次过滤」。

```bash
# (1) 跑完一批数据后，把这批实际用过的资产登记进账本
python -m orchestrator.usage_ledger update \
    --ledger ./assets/used_ledger.json --scan ./out/raw

# (2) 查看账本累计用了多少
python -m orchestrator.usage_ledger show --ledger ./assets/used_ledger.json

# (3) 下一批下载新 Objaverse 物体时，自动跳过账本里用过的
LEDGER=./assets/used_ledger.json OBJAVERSE_N=300 \
    bash scripts/download_assets.sh objaverse
#   等价于： python -m orchestrator.prefetch --n 300 \
#               --exclude-used ./assets/used_ledger.json

# (4) 3D-FRONT 场景（库已整套下好）则在「生成时」过滤：
#     在 configs/front3d.yaml 的 scene.params 里加一行
#       exclude_used_ledger: "./assets/used_ledger.json"
#     生成时就会自动跳过账本里用过的房间。
```

原理：每个 job 的 `sample.json` 里记了 `provenance`（本次实际用到的 uid /
场景），`usage_ledger update` 扫描这些并累加去重进账本；下载/生成时据此过滤。
账本本身很小，建议和资产一起放持久盘。

---

## 下一步

资产备齐后回到主 `README.md` 跑四步流程：
`prefetch`(已含在上面) → `ray_runner` → `collector` → 上传。
循环生产时,每批之间插入「`usage_ledger update` → 带 `LEDGER` 重新下载」即可去重。
