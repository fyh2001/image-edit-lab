# Blender 3D 编辑配对数据采集 (Ray + BlenderProc)

为图像编辑模型（如 Qwen-Image-Edit）批量生产**像素级完美对齐**的
`(before, instruction, after)` 三元组。用 3D 引擎只改目标参数、其余不动，
天然解决 2D 合成对齐不准的痛点。面向 **8×H100** 分布式渲染。

## 核心架构

```
Ray Orchestrator（普通 Python 进程）
 ├─ jobspec_gen   参数化 + 固定种子 → N 个可复现 JobSpec
 ├─ ray_runner    每个 job 一个 Ray task，@ray.remote(num_gpus=1)
 │                 → 子进程 `blenderproc run worker/run_job.py -- spec.json`
 │     worker（Blender Python 内，一个 job 一进程）:
 │        SceneBuilder → render BEFORE → EditOperator → render AFTER → 落地
 └─ collector     原始产物 → WebDataset tar 分片 → 传 HF / 对象存储
```

**关键约束**：BlenderProc 必须用 `blenderproc run` 在 Blender 自带 Python 里跑，
不能在 Ray worker 进程直接 import。所以 Ray 层用**子进程**调度，靠 `num_gpus=1`
让 8×H100 上最多 8 个渲染任务并发、互不抢卡。

## 模块化扩展点（注册表模式）

| 类别 | 基类 | 装饰器 | 现有实现 |
|---|---|---|---|
| 场景 | `SceneBuilder` | `@register_scene` | `tabletop` |
| 资产 | `AssetProvider` / `EnvironmentProvider` | `@register_asset` | `objaverse`, `haven` |
| 编辑算子 | `EditOperator` | `@register_edit` | move / scale / rotate / add / delete / replace |
| 渲染后端 | `RenderBackend` | `@register_backend` | `cycles_gpu`, `eevee_fast` |

**加一个新编辑类型**（比如换材质）只需三步，主流程零改动：

```python
# worker/edits/material_edits.py
from worker.edits.base import EditOperator
from worker.registry import register_edit

@register_edit("object_recolor")
class RecolorEdit(EditOperator):
    def apply(self, ctx):
        # ... 改 ctx.subject 的材质 ...
        return "change the color of the object", {"op": "object_recolor"}
```

然后在 `worker/plugins.py` 加一行 import，在 `configs/*.yaml` 的
`edits.sampling_weights` 里点名即可。

## 目录结构

```
blender_data_pipeline/
├── configs/default.yaml        # 配置驱动一切
├── orchestrator/               # 普通 Python：Ray 编排 + 打包 + 预下载
│   ├── prefetch.py             #   预下载 Objaverse 资产
│   ├── jobspec_gen.py          #   生成可复现 JobSpec
│   ├── ray_runner.py           #   Ray 分布式调度（子进程跑 blenderproc）
│   └── collector.py            #   打包 WebDataset 分片
└── worker/                     # Blender Python：单个 job 的渲染逻辑
    ├── run_job.py              #   入口（blenderproc run 调用）
    ├── registry.py             #   插件注册表
    ├── context.py              #   JobSpec / SceneContext
    ├── plugins.py              #   集中注册所有插件
    ├── assets/  scene/  edits/  render/  export/
```

## 安装

```bash
pip install -r requirements.txt        # orchestrator 环境（含 ray / webdataset / objaverse）
pip install blenderproc                 # 它会管理一个内置 Blender
blenderproc quickstart                  # 验证 BlenderProc 装好
blenderproc pip install imageio         # worker 侧依赖（写 PNG）
```

资产准备：把 Poly Haven 的 HDRI 放到 `assets/haven/hdris/`（.hdr/.exr）。

## 四步跑通

```bash
# 1) 预下载 Objaverse 资产 + 生成 uid 列表/类别映射
python -m orchestrator.prefetch --n 500

# 2) 本地冒烟测试：先用快后端跑 2 个 job 验证整条逻辑
#    （把 configs/default.yaml 的 render.backend 改成 eevee_fast）
python -m orchestrator.ray_runner --config configs/default.yaml --limit 2

# 3) 生产：切回 cycles_gpu，在 8×H100 上全量跑
python -m orchestrator.ray_runner --config configs/default.yaml

# 4) 打包成 WebDataset，准备上传 HF / 对象存储
python -m orchestrator.collector --config configs/default.yaml
hf upload <your-repo> ./out/shards --repo-type dataset
```

训练时用 `webdataset` / `datasets` 直接从 HF 流式读这些 tar 分片即可。

## 渲染后端切换

- **本地调试** → `render.backend: eevee_fast`（秒级出图，验证 pipeline 逻辑）
- **生产** → `render.backend: cycles_gpu`（OptiX 路径追踪，跑满 H100，真实感最好）

## 重要提醒 / 待核对

1. **BlenderProc API 版本差异**：代码里 `load_obj` 对 glb 的返回类型、
   `obj.hide()`、`set_world_background_hdr_img` 的签名在不同版本可能略有出入，
   已加兼容/容错，但请用 `blenderproc debug worker/run_job.py` 在 GUI 里核对一遍。
2. **H100 + OptiX**：H100 是数据中心卡，Cycles 支持 CUDA/OptiX；
   首次跑确认 `bpy ... preferences` 能枚举到 8 张卡并启用。
3. **指令名词**：默认用 Objaverse-LVIS 类别填 `{noun}`。要更自然的指令
   （"a red wooden chair"），可在 prefetch 阶段或后处理用 VLM 给渲染图重写指令。
4. **domain gap**：纯渲染图偏 CGI 感。上规模前先验证在真实照片上的迁移，
   必要时加「渲染→提真」后处理（对 before/after 用同一结构约束以保持对齐）。
5. **质量过滤**：建议后续加一个 `QualityFilter` 阶段（对齐度 / 清晰度 / 指令一致性），
   架构已为其预留位置。
```
