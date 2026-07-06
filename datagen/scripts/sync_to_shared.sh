#!/usr/bin/env bash
# 把本地快盘生成的数据打成 tar 推到共享目录。
# 为什么这样：直写 s3fs 共享会因"成千上万个小文件"卡住 worker、GPU 空等(实测掉到 <100 对/时);
# 本地盘生成 GPU 稳 100%，再把整批打成**一个 tar** 顺序写到 s3fs(快)。这样既满速、又跨机器可取。
#
# 用法：
#   bash datagen/scripts/sync_to_shared.sh [本地out目录] [共享tar路径] [循环间隔秒(可选)]
# 例：
#   一次性同步：  bash datagen/scripts/sync_to_shared.sh /mnt/local/sss/image-edit-out/hssd_raw
#   每 30 分钟自动备份： bash datagen/scripts/sync_to_shared.sh /mnt/local/sss/image-edit-out/hssd_raw "" 1800
set -euo pipefail

SRC="${1:-/mnt/local/sss/image-edit-out/hssd_raw}"
DST="${2:-/mnt/shared/sss/image-edit-out/$(basename "$SRC").tar}"
INTERVAL="${3:-0}"
TMP="/mnt/local/.sync_$(basename "$SRC").tar"   # 先打到本地(快)，再整文件拷到 s3fs(一次大写)

[ -z "${2:-}" ] && DST="/mnt/shared/sss/image-edit-out/$(basename "$SRC").tar"

sync_once() {
  [ -d "$SRC" ] || { echo "[sync] 源不存在: $SRC"; return; }
  mkdir -p "$(dirname "$DST")"
  tar cf "$TMP" -C "$(dirname "$SRC")" "$(basename "$SRC")"       # 本地打包(快)
  cp "$TMP" "$DST.tmp" && mv "$DST.tmp" "$DST"                    # 一次大文件写 s3fs(快)
  rm -f "$TMP"
  local n; n=$(find "$SRC" -name sample.json 2>/dev/null | wc -l)
  echo "[sync] $(date +%F' '%T) → $DST | $(du -sh "$SRC" 2>/dev/null | cut -f1) | ${n} 对"
}

if [ "$INTERVAL" -gt 0 ]; then
  echo "[sync] 每 ${INTERVAL}s 自动同步 $SRC → $DST（Ctrl-C 停）"
  while true; do sync_once; sleep "$INTERVAL"; done
else
  sync_once
fi
