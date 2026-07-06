#!/usr/bin/env bash
# 用 uv 建 Qwen-Image-Edit 推理环境(H100/CUDA)。在仓库根目录跑： bash eval/setup_uv.sh
# 关键点：torch + torchvision **一起从 pytorch 的 CUDA index 装**，才不会出现
# "operator torchvision::nms does not exist"(二者版本/编译错配)。
set -euo pipefail

# CUDA 轮子版本：新驱动用 cu124；驱动较老改成 cu121。
CUDA="${CUDA:-cu124}"
PYVER="${PYVER:-3.11}"     # 别用 3.13(太新，wheel 常缺)
# **钉死一对官方成对发布的 torch/torchvision**——只给 index 不钉版本时，uv 可能解析出不配套的
# 组合，仍报 "operator torchvision::nms does not exist"。torch 2.6.0 ↔ torchvision 0.21.0。
TORCH="${TORCH:-2.6.0}"
TVISION="${TVISION:-0.21.0}"

# 1) 装 uv(若没有)
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2) 建虚拟环境(固定 Python 版本)
uv venv --python "$PYVER" .venv
# shellcheck disable=SC1091
source .venv/bin/activate

# 3) torch + torchvision 配套(钉死版本 + 同一 CUDA index，成对安装)
uv pip install --force-reinstall \
  "torch==${TORCH}" "torchvision==${TVISION}" \
  --index-url "https://download.pytorch.org/whl/${CUDA}"

# 4) 推理库(从默认 PyPI)
uv pip install -U diffusers transformers accelerate pillow openai

# 5) 自检
python - <<'PY'
import torch, torchvision
print("torch", torch.__version__, "| torchvision", torchvision.__version__,
      "| cuda_available", torch.cuda.is_available())
from diffusers import QwenImageEditPipeline   # 导得到就说明依赖链通了
print("QwenImageEditPipeline import OK")
PY

echo
echo "✅ 环境就绪。以后先： source .venv/bin/activate"
echo "   单图： python -m eval.qwen_edit_diffusers --image data/xxx.png --prompt '把电视机放到地上' --out outputs/out.png"
echo "   批量： python -m eval.qwen_edit_diffusers --dataset-dir ./out/gallery_v5 --out-dir ./out/qwen_eval --limit 40"
