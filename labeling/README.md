# labeling — VLM 打标（captioner 已搭骨架）

把 datagen 产的**模板指令**改写成**自然、语义化**的一条 caption（"把桌上的花瓶放到冰箱上"、
"把电视背面对着我"），注册为 `common.ray_exec` 的任务类型 `vlm_caption`，复用通用流式执行器 +
资源档 + pipeline。设计谱系见 [../docs/CAPTION_DESIGN.md](../docs/CAPTION_DESIGN.md)。

## 策略（已定）
**训练主集每对一条 caption + 数据集级风格均衡**：captioner 按亏空采样挑一种风格
（direct / spatial / intent / goal / casual），把 `style` 标签写回。措辞鲁棒性靠整个数据集
覆盖各风格，而非同图重复。多条留给 paraphrase 评测集和补稀缺片。

## 模块（`caption/`）
- `styles.py`   — 5 种风格 + **亏空采样**均衡（纯逻辑）；`OP_STYLE_BLOCK` 禁掉别扭组合（如 scale×goal）。
- `facts.py`    — 从 `sample.json` 抽**客观事实**（op/noun/消歧指代/方向/view_change/缩放/replace 新旧物）。
- `verify.py`   — caption **一致性校验**：只抓硬矛盾（方向说反/算子说错/放大缩小反）→ 不过就重生成/回退。
- `providers.py`— VLM 后端**可插拔**：`StubProvider`（规则版，无网络，跑通全链路 + 单测）；
  `QwenVLProvider` 占位，接真模型时实现同一个 `caption(facts, style, images)` 接口。
- `prompt.py`   — 给真 VLM 组 prompt（看 before/after 图，出一句该风格指令，不准与事实矛盾）。
- `task.py`     — `@register_task("vlm_caption")`：遍历 raw_dir，逐样本改写并写回 `caption`/`caption_style`/`caption_meta`。

## 在 pipeline 里用
`datagen/configs/hssd.yaml` 已串成 `render → vlm_caption → pack_parquet`：
```yaml
pipeline:
  - {type: vlm_caption, profile: caption, params: {provider: stub, seed: 0}}
  - {type: pack_parquet, profile: pack}
```
`export_hf` 打包时**优先读 `caption`**（没打标才退回模板 `instruction`），并带 `caption_style` 列。
不重渲、随时可重标。

## 接真 VLM（待做）
实现 `QwenVLProvider.caption()`：用 `prompt.build_messages(facts, style, images)` + before/after 图
调 Qwen-VL，输出一条自然指令（看图补 front/back 命名）。config 改
`params: {provider: qwen_vl, use_images: true}` 并给 `caption` 资源档配 `num_gpus: 1`。

## 测试
`python -m pytest labeling/tests/ -q`（纯 Python，不依赖 Blender/VLM/网络）。
