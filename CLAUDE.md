# CLAUDE.md — 项目上下文（给 Claude Code 的导览）

## 这是什么

为图像编辑模型（Qwen-Image-Edit 等）批量生产**像素级完美对齐**的
`(before, instruction, after)` 三元组训练数据。用 **Ray 分布式 + BlenderProc** 在
3D 场景里只改目标物体、其余不动来渲染编辑配对。最终目标机器是 **8×H100**。

## 架构（关键约束先看这条）

**BlenderProc 必须用 `blenderproc run <script>` 在 Blender 自带 Python 里跑，不能在
普通进程/Ray worker 里直接 import。** 所以 Ray 层用**子进程**调度 `blenderproc run`。

仓库是**端到端图像编辑模型**项目（`image-edit-lab`）：数据合成 → 打标 → 训练 → 评测。
顶层按模块分（导入用 `datagen.*` / `common.*`；仓库根 conftest.py 把根加进 sys.path）：

```
common/                    通用件（不绑 Blender，各模块共享）
  └─ ray_exec.py           **通用流式执行器**：任务类型可插拔(@register_task/@register_stream_task)、
                           每档资源可配(.options)、run_stream/run_stream_incremental(逐对)/run_pipeline(多阶段)
datagen/                   Blender 数据合成（本模块，当前主体）
  ├─ orchestrator/         普通 Python：Ray 编排/打包/入库/导出/下载
  │   ├─ jobspec_gen.py    多场景×多物体组合 + 粒度(摊销/逐算子) → task 流
  │   ├─ ray_runner.py     用 common.ray_exec 跑 blender_render 任务流
  │   ├─ tasks/            blender_render(+_stream) 任务类型
  │   ├─ collector/export_hf/upload_hf   WebDataset 打包 / HF Parquet 导出 / 上传
  │   ├─ ingest.py db.py   PostgreSQL 入库
  │   └─ prefetch(_hssd).py 资产预取
  ├─ worker/               Blender Python：单 job 渲染逻辑
  │   ├─ run_job.py        入口：建场景→渲before→编辑→渲after→过滤→落地
  │   ├─ registry/plugins  插件注册表 + 集中 import
  │   ├─ scene/ assets/ edits/ physics/ geometry/ quality/ render/
  ├─ configs/  scripts/(smoke,run_local)  tests/(63 纯Python单测)
labeling/ training/ eval/  未来：VLM 打标 / Qwen 训练 / benchmark 评测（各注册为 ray_exec 任务类型）
```

`sample.json` metadata 已较完整：coordinate_frame、cameras(内参+外参)、subject.init_transform、
各算子 edit 字段、validity(碰撞/悬空数值 + quality 分数 + move 的 direction_check)、
distractor_categories(消歧审计)。详见 DESIGN §5 与 NEXT_STEPS。

设计细节见 `docs/DESIGN_object_edits.md`，资产下载见 `docs/SETUP_ASSETS.md`，总览见 `README.md`，
**踩坑与解决记录见 `docs/TROUBLESHOOTING.md`**。

## 冒烟测试（已跑通 ✅，本机 macOS arm64 + Blender 4.2.1）

冒烟测试零下载、零网络（primitives + **cycles**），为 6 个算子各渲一对：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install blenderproc pyyaml imageio numpy
python datagen/scripts/smoke.py        # 首次自动下载 Blender；smoke.py 已自动设 SSL_CERT_FILE
python -m pytest datagen/tests/ -q     # 纯 Python 单测（frames / validity，不依赖 Blender）
```

产物在 `out/smoke_raw/smoke_<算子>/`：`before_v0.png`、`after_v0.png`、`sample.json`。
6 算子全绿；`sample.json` 已对齐 DESIGN §5 完整 schema（相机内参+外参、subject.init_transform、
move.final_transform、rotate.delta_quat、scale.per_axis、validity.penetration_depth/floating_gap）。
**踩坑与修正清单见 `docs/NEXT_STEPS.md` 的"冒烟测试现状/已修"。**

> macOS 关键点：① 冒烟用 `cycles_gpu`（Eevee-Next 无显示器下不重评估可见性，object_add 失效）；
> ② 首次下载的 `Blender.app` 被 App Management 保护，需 `xattr -dr com.apple.provenance <Blender.app>`
> 才能让 BlenderProc 往 bundle 内装包。

### 验收标准（均已满足）

1. 6 个算子(object_move/scale/rotate/delete/add/replace)都能产出一对，无报错。
2. before/after **只有主体变**，背景/干扰物/光照不动（对齐干净）。
3. 物体不穿模、不悬空（move/scale 后贴支撑面）；主体清晰可见、不太小。
4. `sample.json` 的 metadata 正确：move 有 translation_world/translation_camera/
   semantic_direction/placement_mode；rotate 有 axis/degrees；scale 有 factor；
   外加 coordinate_frame、cameras(内参+外参)、subject.init_transform、validity。

## 已知风险（最可能要改的地方）

**已实跑验证**（Blender 4.2.1）：`obj.hide()`、`BVHTree`（要烘世界系，见 validity._bvh_of）、
`scene.ray_cast`、`bproc.camera.project_points`、`get_intrinsics_as_K_matrix`、
`simulate_physics_and_fix_final_poses`、**`bproc.loader.load_obj`(.glb，用合成 glb 验证过)**。

**真实资产链路已小跑验证**（tabletop + objaverse + cycles，14/14 job 全产出，见 NEXT_STEPS）：
- 真实名词：`objaverse_provider` 从 `category_map`(uid→LVIS 类别) 写 `category`+`noun`；纯逻辑
  在 `worker/assets/categories.py`（有单测）。
- objaverse glb 是**层级结构**：provider 过滤 mesh + `join_with_other_objects` 合并多 part
  （别盲取 objs[0]，别 join 后再删被并入对象 → Blender double-free 崩溃）。
- 本机 `cycles_gpu` 自动回退 **Metal GPU**（M5 Pro，~0.85s/帧）；OPTIX 仅在 8×H100 真机有。
- 本地不用 Ray 跑批：`python datagen/scripts/run_local.py --config datagen/configs/default.yaml --num-jobs N`。

资产处理（均已实跑验证，2026-07-01）：
- prefetch 跨类别均匀采样（`sample_diverse_uids`）；objaverse 原点归几何中心 + 最长边缩放
  （统一 `target_size` 或按类别 `category_sizes`）；replace 新物体尺度对齐旧物（`_match_size`）。
- HDRI：`set_world_background_hdr_img` 已验证；`hdri_dir` 有 .hdr/.exr 就用，没有退平光。

**剩余限制**（不算 bug，规模化前再议）：
- up-axis 朝向沿用 glb 导入朝向，不强制摆正（个别资产可能侧躺）。
- 类别真实尺度默认只在 room 级场景开（tabletop 用统一尺寸，否则小物体看不清）。
- 调试单个 job：`blenderproc debug datagen/worker/run_job.py -- <spec.json>` 可在 GUI 里逐步看。

## 约定

- 所有随机走 `ctx.rng`（numpy Generator，seed 固定保证可复现）。
- 加新编辑/场景/资产 = 写一个类 + `@register_*` + 在 `plugins.py` 加一行 import。
- 纯 Python 逻辑（geometry/frames、validity 的 change_is_visible/find_valid）可不依赖
  Blender 直接单测。
