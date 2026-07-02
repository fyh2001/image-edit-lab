#!/usr/bin/env bash
# ============================================================
# 资产下载脚本（显式运行，不在渲染时自动下载）。
# 建议把 ASSET_DIR 指到「持久化存储」（组目录/对象存储挂载点），
# 这样机器清盘也不用重下。
#
# 用法：
#   bash scripts/download_assets.sh objaverse        # 只下 Objaverse
#   bash scripts/download_assets.sh haven            # 只下 Poly Haven (HDRI/CC0)
#   bash scripts/download_assets.sh cc_textures      # 只下 ambientCG 材质 (CC0)
#   bash scripts/download_assets.sh all              # 一次下全部可自动下载的
#   bash scripts/download_assets.sh front3d          # 打印 3D-FRONT 手动下载指引
# ============================================================
set -euo pipefail

ASSET_DIR="${ASSET_DIR:-./assets}"          # 改这里或用环境变量覆盖
OBJAVERSE_N="${OBJAVERSE_N:-200}"           # 下多少个 Objaverse 物体
LEDGER="${LEDGER:-}"                         # 已用账本路径；设了就跳过用过的 uid
mkdir -p "${ASSET_DIR}"

download_objaverse() {
  echo ">>> [Objaverse] 下载 ${OBJAVERSE_N} 个物体 -> ${ASSET_DIR}/objaverse"
  local exclude_arg=()
  if [[ -n "${LEDGER}" ]]; then
    echo "    过滤已用账本: ${LEDGER}"
    exclude_arg=(--exclude-used "${LEDGER}")
  fi
  python -m orchestrator.prefetch \
    --n "${OBJAVERSE_N}" \
    --out "${ASSET_DIR}/objaverse" \
    --uid-list "${ASSET_DIR}/objaverse_uids.txt" \
    --category-map "${ASSET_DIR}/objaverse_categories.json" \
    "${exclude_arg[@]}"
  echo "    完成。约 ${OBJAVERSE_N} * 11MB ≈ $(( OBJAVERSE_N * 11 / 1000 )).$(( OBJAVERSE_N * 11 / 100 % 10 )) GB（方差较大）"
}

download_haven() {
  echo ">>> [Poly Haven] 下载 HDRI + 贴图 + 模型 (CC0) -> ${ASSET_DIR}/haven"
  # BlenderProc 自带下载器
  blenderproc download haven "${ASSET_DIR}/haven"
  echo "    HDRI 在 ${ASSET_DIR}/haven/hdris/ ，填进 config 的 hdri_dir"
}

download_cc_textures() {
  echo ">>> [ambientCG] 下载 PBR 材质 (CC0) -> ${ASSET_DIR}/cc_textures"
  blenderproc download cc_textures "${ASSET_DIR}/cc_textures"
}

front3d_instructions() {
  cat <<'EOF'
>>> [3D-FRONT] 需手动申请下载（无法脚本自动下，因为要登录+签协议）

  1. 打开官方申请页（阿里天池）：
     https://tianchi.aliyun.com/specials/promotion/alibaba-3d-scene-dataset
  2. 申请并下载三件套，解压到 ${ASSET_DIR} 下：
     ${ASSET_DIR}/3D-FRONT           (场景 json，约 3-5 GB)
     ${ASSET_DIR}/3D-FUTURE-model    (家具库，约 ~20 GB —— 所有场景共享，必须整套下)
     ${ASSET_DIR}/3D-FRONT-texture   (墙地贴图，约 2-5 GB)
  3. 把这三个路径填进 configs/front3d.yaml 的 scene.params。

  注意：3D-FRONT 仅限学术研究使用，不可商用。
EOF
}

TARGET="${1:-all}"
case "${TARGET}" in
  objaverse)    download_objaverse ;;
  haven)        download_haven ;;
  cc_textures)  download_cc_textures ;;
  front3d)      front3d_instructions ;;
  all)
    download_objaverse
    download_haven
    download_cc_textures
    front3d_instructions
    ;;
  *)
    echo "未知目标: ${TARGET}"
    echo "可选: objaverse | haven | cc_textures | front3d | all"
    exit 1
    ;;
esac

echo ">>> 完成。资产根目录: ${ASSET_DIR}"
