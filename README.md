# image-edit-lab

端到端**图像编辑模型**流水线：**数据合成 → VLM 打标 → 训练 → 评测**。
当前主体是数据合成（`datagen/`）——用 **BlenderProc + Ray** 在 3D 场景里
**只改目标物体、其余不动**，批量生产**像素级完美对齐**的 `(before, instruction, after)`
三元组。3D 渲染天然解决 2D 合成对齐不准的痛点。面向 **8×H100** 分布式生产。

> 打标 / 训练 / 评测（`labeling/` `training/` `eval/`）是后续模块，都注册为下面这套
> 通用 Ray 执行器的任务类型，用同一套编排。

## 目录结构

```
common/          通用件（不绑 Blender，各模块共享）
  └─ ray_exec.py 通用流式执行器：任务类型可插拔 + 每档资源可配 + 流式/增量/pipeline
datagen/         Blender 数据合成（本模块）
  ├─ orchestrator/  Ray 编排 / 打包 / 入库 / 导出 / 预取
  │   ├─ ray_runner.py   config → 任务流 → 通用执行器；render 后接后处理 pipeline
  │   ├─ jobspec_gen.py  多场景 × 多物体组合 + 任务粒度 → task 流
  │   ├─ tasks/          blender_render / pack_parquet / upload_hf … （算子）
  │   ├─ export_hf / collector / upload_hf   HF Parquet / WebDataset / 上传
  │   ├─ ingest.py db.py PostgreSQL 入库（可选）
  │   └─ prefetch(_hssd).py 资产预取
  ├─ worker/       Blender Python：单 job 渲染逻辑（scene/assets/edits/physics/quality/render）
  └─ configs/ scripts/ tests/(63 纯 Python 单测)
labeling/ training/ eval/   未来模块（骨架）
docs/            设计 / 踩坑记录 / 进度 / 配置指南
```

**关键约束**：BlenderProc 必须用 `blenderproc run` 在 Blender 自带 Python 里跑，不能在
Ray worker 进程直接 import。所以 Ray 层用**子进程**调度 `blenderproc run`。

## 通用 Ray 执行器（`common/ray_exec.py`）

不绑 Blender——render / 打包 / 上传 / 未来的 VLM 打标 / 训练 / 评测都是**平级任务类型**。

- **任务可插拔**：`@register_task` / `@register_stream_task`，task = `{type, profile, payload}`
- **每档资源可配**：`profile → {num_cpus, num_gpus}`，提交时 `.options()` 动态设
- **三种编排**：
  - `run_stream`：任务级流式（有界窗口，一个完成搞下一个，吃 generator）
  - `run_stream_incremental`：逐项增量流式（一个任务边跑边吐结果，driver 实时消费每一对）
  - `run_pipeline`：多阶段（`staged` 批式between/流式within ｜ `streaming` 完全流式）

## 数据合成能力（均已本机验证）

- **6 编辑算子**：move / scale / rotate / delete / add / replace
- **三种主体来源**（`subject_source` 可配混合）：直接编辑场景已有物体 ｜ spawn 外部物体到空表面 ｜ 用外部物体替换已有物体槽位
- **多数据集组合**：`scenes[]`（tabletop/room/hssd 真实室内）× `object_sources[]`（objaverse/primitives，`composite` 按权重混）
- **物理有效性**：世界系 BVH 碰撞 + **基线感知**（忽略原场景就有的接触）+ **表面感知放置**（桌面/柜顶/叠放）
- **数据质量**：**指代消歧**（多同类物体→"the chair on the left"）、**场景内去重**、QualityFilter（清晰度/背景稳定/变化幅度）、方向语义校验
- **均衡产出**：`sampling_weights` 作目标产出占比，亏空采样命中（补偿各算子 yield 差异）
- **相机 + 照明**：广角室内取景 + 遮挡检查（砍穿墙机位）+ 自适应补光
- **完整溯源**：`provenance` 存数据集来源 + 解析后 scene_id + 资产 uid/license + 完整生成 config + 工具版本 + seed → **可还原现场**
- **真实贴图**：HSSD 的 KTX2 贴图解码还原（`tools/ktx`）

## 快速开始

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # 编排侧：ray/datasets/huggingface_hub/sqlalchemy...
pip install blenderproc                    # 管理内置 Blender；worker 侧 imageio 用 blenderproc pip install

# 冒烟：零下载，6 算子各渲一对（cycles + primitives）
python datagen/scripts/smoke.py
python -m pytest datagen/tests/ -q         # 63 纯 Python 单测

# 用通用执行器跑一批（config 驱动：多数据集组合 + 资源档 + 后处理 pipeline）
python -m datagen.orchestrator.ray_runner --config datagen/configs/hssd.yaml --limit 4
```

`ray_runner` 跑完 render 后按 config 的 `pipeline:` 自动接后处理算子（`pack_parquet → upload_hf`），
一条命令从渲染到 HF 数据集。

## 加一个新算子（注册表模式，主流程零改动）

编辑/场景/资产/后端都靠 `@register_*` 注册 + 在 `datagen/worker/plugins.py` 加一行 import；
非渲染算子（打标/训练…）用 `@register_task` 注册到 `common.ray_exec`，config 的 `pipeline:` 里串。

```python
# datagen/worker/edits/material_edits.py
from datagen.worker.edits.base import EditOperator
from datagen.worker.registry import register_edit

@register_edit("object_recolor")
class RecolorEdit(EditOperator):
    def apply(self, ctx):
        ...  # 改 ctx.subject 材质
        return "change the color of the object", {"op": "object_recolor"}
```

## 打包 / 上传 / 训练读取

- **HF 原生 Parquet**（推荐，可浏览 + `load_dataset`）：`datagen.orchestrator.export_hf`，带 Image 特征 + 场景级 split
- **WebDataset**（大规模流式训练）：`datagen.orchestrator.collector`
- 上传：`hf auth login` 后 `pack_parquet → upload_hf` 算子自动推，或手动 `export_hf --repo-id ...`

## 文档

设计 `docs/DESIGN_object_edits.md` ｜ **踩坑与解决 `docs/TROUBLESHOOTING.md`** ｜
进度/backlog `docs/NEXT_STEPS.md` ｜ 资产下载 `docs/SETUP_ASSETS.md` ｜ 数据库 `docs/SETUP_DB.md`。
项目导览（给 Claude Code）`CLAUDE.md`。

## 状态

数据合成管线本机（macOS + Blender 4.2.1，cycles_gpu→Metal）全跑通并验证；
**大规模生产在 8×H100 上跑**（OPTIX + Ray 铺满多卡，本机单 GPU 太慢）。
打标 / 训练 / 评测为后续模块。
