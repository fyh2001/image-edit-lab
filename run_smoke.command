#!/bin/bash
# ============================================================
# 双击即可运行的冒烟测试启动器（macOS）。
# 它会：建虚拟环境 → 装 BlenderProc → 跑 6 个算子的冒烟测试
#       → 把所有输出写进 out/smoke_run.log（Claude 直接读这个日志调试）。
#
# 若双击被 Gatekeeper 拦：右键 → 打开；或在终端里 `bash run_smoke.command`。
# ============================================================
cd "$(dirname "$0")" || exit 1
mkdir -p out
LOG="$(pwd)/out/smoke_run.log"

{
  echo "=== 开始 $(date) ==="
  echo "项目目录: $(pwd)"
  echo "--- python ---"
  python3 --version

  echo "--- 创建虚拟环境 .venv ---"
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install --upgrade pip

  echo "--- 安装 BlenderProc 及依赖（首次较慢）---"
  pip install blenderproc pyyaml imageio numpy

  echo "--- BlenderProc 版本 ---"
  blenderproc --version

  echo "--- 跑冒烟测试（首次会下载 Blender，约几百 MB）---"
  python scripts/smoke.py

  echo "=== 结束 $(date) ==="
  echo "产物在 out/smoke_raw/，本日志在 $LOG"
} 2>&1 | tee "$LOG"

echo ""
echo "完成。日志已写到: $LOG"
echo "可以把这个日志告诉 Claude，或让它直接读取。"
