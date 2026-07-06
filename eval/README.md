# eval — 评测 / 基座模型试跑

## Qwen-Image-Edit 推理 —— 两种跑法

**A. 直接加载模型跑（`qwen_edit_diffusers.py`，不用起服务，推荐先用这个试效果）**
```bash
pip install -U diffusers transformers accelerate torch pillow
# 单图：
python eval/qwen_edit_diffusers.py --image before.png --prompt "把桌上的花瓶移到左边" --out out.png
# 批量跑我们的数据 + 三栏对比 HTML：
python eval/qwen_edit_diffusers.py --dataset-dir ./out/gallery_v5 --out-dir ./out/qwen_eval --limit 40
# 8 卡数据并行(不用 serve)：CUDA_VISIBLE_DEVICES=$i ... --shard $i/8（脚本末尾有 for 循环示例）
# 2509 改进版： --model Qwen/Qwen-Image-Edit-2509 --plus ；显存紧： --cpu-offload
```

**B. 起服务再调（`qwen_edit_sglang.py`，要高吞吐/多人共用时）**
见下。

## Qwen-Image-Edit 推理（SGLang Diffusion，serve 方式）

`qwen_edit_sglang.py` —— 用 **SGLang Diffusion**(2025-11 起支持扩散图像编辑,day-0 支持
Qwen-Image-Edit)跑基座模型推理,试它在我们数据上的编辑效果。注意 **vLLM / SGLang 的普通
LLM 模式跑不了扩散模型**,要用 SGLang **Diffusion**(`sglang serve` 起 OpenAI 兼容服务)。

```bash
# 服务端(8×H100，另开 tmux)：
pip install "sglang[diffusion]"                 # 以官方 SGLang-Diffusion 安装指引为准
sglang serve --model-path Qwen/Qwen-Image-Edit-2511 --num-gpus 8 --tp-size 8

# 客户端：
pip install openai pillow
# 单图测：
python eval/qwen_edit_sglang.py --image before.png --prompt "把桌上的花瓶移到左边" --out out.png
# 批量跑我们的数据 + 出 before|Qwen结果|target 三栏对比 HTML：
python eval/qwen_edit_sglang.py --dataset-dir ./out/gallery_v5 --out-dir ./out/qwen_eval --limit 40
```

## Benchmark 评测（待建）

在真实图像编辑 benchmark（MagicBrush / PIE-Bench / Emu Edit 等）上评测训练出的模型；
按"物体算子正确性 + 未编辑区保真度"分维度测，物体级与外观/全局编辑分开看。
